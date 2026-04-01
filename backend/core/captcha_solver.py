"""
2Captcha integration for solving reCAPTCHA v2.
API docs: https://2captcha.com/2captcha-api
"""

import time
import requests

# Libero reCAPTCHA v2 config
RECAPTCHA_SITE_KEY = "6LfhyXsUAAAAALD-dxi8n8-M4zSVBi9Z8D7D2H4L"
RECAPTCHA_PAGE_URL = "https://login.libero.it/"


def solve_recaptcha_v2(api_key: str,
                       site_key: str = RECAPTCHA_SITE_KEY,
                       page_url: str = RECAPTCHA_PAGE_URL,
                       timeout: int = 120) -> str:
    """
    Submit reCAPTCHA v2 to 2Captcha and poll until solved.
    Returns the g-recaptcha-response token string.
    """
    # 1. Submit task
    resp = requests.post("http://2captcha.com/in.php", data={
        "key": api_key,
        "method": "userrecaptcha",
        "googlekey": site_key,
        "pageurl": page_url,
        "json": 1,
    }, timeout=30)
    data = resp.json()

    if data.get("status") != 1:
        raise CaptchaError(f"2Captcha submit failed: {data}")

    request_id = data["request"]
    print(f"[CAPTCHA] Submitted to 2Captcha, request_id={request_id}", flush=True)

    # 2. Poll for result
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        resp = requests.get("http://2captcha.com/res.php", params={
            "key": api_key,
            "action": "get",
            "id": request_id,
            "json": 1,
        }, timeout=30)
        data = resp.json()

        if data.get("status") == 1:
            token = data["request"]
            print(f"[CAPTCHA] ✓ Solved! Token length={len(token)}", flush=True)
            return token

        if data.get("request") != "CAPCHA_NOT_READY":
            raise CaptchaError(f"2Captcha error: {data}")

    raise CaptchaError(f"2Captcha timeout after {timeout}s")


def check_balance(api_key: str) -> float:
    """Check remaining balance on 2Captcha account."""
    resp = requests.get("http://2captcha.com/res.php", params={
        "key": api_key,
        "action": "getbalance",
        "json": 1,
    }, timeout=10)
    data = resp.json()
    if data.get("status") == 1:
        return float(data["request"])
    raise CaptchaError(f"Cannot check balance: {data}")


class CaptchaError(Exception):
    pass
