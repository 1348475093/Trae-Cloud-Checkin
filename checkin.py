
import json
import os
import sys
import time
import random
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

BASE_URL = "https://api.trae.cn"
SESSION = os.environ.get("TRAE_SESSION", "").strip()
DEVICE_ID = os.environ.get("TRAE_DEVICE_ID", "").strip()

MAX_RETRIES = 3
TIMEOUT = 25


class CheckinError(Exception):
    pass


class TemporaryError(CheckinError):
    pass


def now_cn():
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")


def request_api(path, headers, body=""):
    req = urllib.request.Request(
        BASE_URL + path,
        data=body.encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            status = response.status
            payload = response.read()
    except urllib.error.HTTPError as err:
        status = err.code
        payload = err.read()
    except (urllib.error.URLError, TimeoutError) as err:
        raise TemporaryError("网络请求失败") from err

    if status in (408, 500, 502, 503, 504):
        raise TemporaryError(f"服务器暂时不可用: HTTP {status}")

    if status == 429:
        raise CheckinError("服务端限流 HTTP 429，请稍后再试")

    if status in (401, 403):
        raise CheckinError("认证失败，请更新 TRAE_SESSION")

    if status != 200:
        raise CheckinError(f"接口 HTTP 错误: {status}")

    try:
        return json.loads(payload)
    except (ValueError, UnicodeDecodeError) as err:
        raise CheckinError("服务端返回了非 JSON 内容") from err


def get_token():
    headers = {
        "Cookie": "X-Cloudide-Session=" + SESSION,
        "Referer": "https://www.trae.cn/",
        "Origin": "https://www.trae.cn",
        "Accept": "application/json",
        "User-Agent": "TraeCheckin/1.0",
    }

    data = request_api(
        "/cloudide/api/v3/common/GetUserToken",
        headers,
    )

    token = (data.get("Result") or {}).get("Token")
    if not token:
        raise CheckinError(
            "未获取到 JWT，登录会话可能已失效或接口发生变化"
        )
    return token


def credit_request(token, action):
    headers = {
        "Authorization": "Cloud-IDE-JWT " + token,
        "X-User-Region": "cn",
        "x-device-id": DEVICE_ID,
        "Content-Type": "application/json",
        "User-Agent": "TraeCheckin/1.0",
    }

    return request_api(
        f"/trae/api/v2/ug/checkin_credits/{action}",
        headers,
        "{}",
    )


def check_result(data, action):
    if not isinstance(data, dict):
        raise CheckinError(f"{action} 返回格式异常")

    code = data.get("code", 0)

    if str(code) == "9074":
        raise CheckinError("服务器繁忙或请求受限 (9074)")

    if str(code) != "0":
        raise CheckinError(
            f"{action} 失败，业务错误码: {code}"
        )


def checkin_once():
    token = get_token()

    # 先检查当天是否已经签到
    status = credit_request(token, "status")
    check_result(status, "status")

    if status.get("checked_in") is True:
        print("今日已经签到，无需重复领取")
        return

    # 未签到则领取
    result = credit_request(token, "claim")
    check_result(result, "claim")

    if result.get("checked_in") is True:
        print("签到确认成功")
    else:
        # 部分版本可能使用不同返回字段；
        # 不直接假定成功，交由下次定时任务再次确认。
        print("领取接口已接受请求，待再次确认签到状态")

    credits = result.get("credits")
    if credits is not None:
        print(f"接口返回积分：{credits}")


def main():
    print(f"Trae Work 签到任务：{now_cn()}")

    if not SESSION:
        print("错误：缺少 TRAE_SESSION")
        return 1

    if not DEVICE_ID:
        print("提醒：TRAE_DEVICE_ID 未设置")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"执行第 {attempt}/{MAX_RETRIES} 次尝试")
            checkin_once()
            return 0
        except TemporaryError as err:
            print(f"临时错误：{err}")
            if attempt == MAX_RETRIES:
                return 1
            delay = min(30 * 2 ** (attempt - 1), 120)
            delay += random.randint(0, 10)
            print(f"{delay} 秒后重试")
            time.sleep(delay)
        except CheckinError as err:
            print(f"签到未完成：{err}")
            return 1
        except Exception as err:
            # 不输出包含请求头或敏感数据的异常详情
            print(f"未知错误类型：{type(err).__name__}")
            return 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
