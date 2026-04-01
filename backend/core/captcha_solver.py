import time
import requests

RECAPTCHA_SITE_KEY = "6LfhyXsUAAAAALD-dxi8n8-M4zSVBi9Z8D7D2H4L"
RECAPTCHA_PAGE_URL = "https://login.libero.it/"

def solve_recaptcha_v2(api_key: str,
                       site_key: str = RECAPTCHA_SITE_KEY,
                       page_url: str = RECAPTCHA_PAGE_URL,
                       timeout: int = 120) -> str:
    try:
        resp = requests.post("https://api.capsolver.com/createTask", json={
            "clientKey": api_key,
            "task": {
                "type": "ReCaptchaV2TaskProxyless",
                "websiteURL": page_url,
                "websiteKey": site_key
            }
        }, timeout=30)
        data = resp.json()
    except Exception as e:
        raise CaptchaError(f"Capsolver submit network/JSON error: {e}")

    if data.get("errorId") != 0:
        raise CaptchaError(f"Capsolver submit failed: {data.get('errorDescription', data)}")

    task_id = data.get("taskId")
    print(f"[CAPTCHA] Submitted to Capsolver, taskId={task_id}", flush=True)

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        try:
            resp = requests.post("https://api.capsolver.com/getTaskResult", json={
                "clientKey": api_key,
                "taskId": task_id
            }, timeout=30)
            data = resp.json()
        except Exception as e:
            print(f"[CAPTCHA] Poll error: {e}", flush=True)
            continue

        if data.get("errorId") != 0:
            raise CaptchaError(f"Capsolver error: {data.get('errorDescription', data)}")

        status = data.get("status")
        if status == "ready":
            token = data.get("solution", {}).get("gRecaptchaResponse", "")
            print(f"[CAPTCHA] ✓ Solved! Token length={len(token)}", flush=True)
            return token

        if status != "processing":
            raise CaptchaError(f"Capsolver unknown status: {data}")

    raise CaptchaError(f"Capsolver timeout after {timeout}s")

def check_balance(api_key: str) -> float:
    try:
        resp = requests.post("https://api.capsolver.com/getBalance", json={
            "clientKey": api_key
        }, timeout=10)
        data = resp.json()
    except Exception as e:
        raise CaptchaError(f"Cannot check balance (Network/JSON Error): {e}")
        
    if data.get("errorId") == 0:
        return float(data.get("balance", 0.0))
    raise CaptchaError(f"Cannot check balance: {data.get('errorDescription', data)}")

class CaptchaError(Exception):
    pass
