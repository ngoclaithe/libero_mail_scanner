
import threading
from typing import Optional

from core.proxy_pool import ProxyPool, ProxyInfo
from core.state import AppState


def run_account(
    account:    dict,
    pool:       ProxyPool,
    stop_event: threading.Event,
    user_state: "AppState" = None,
    mode:       str = "adaptive",
):
    """Quét 1 tài khoản Libero bằng Web API (OX AppSuite)."""

    if user_state is None:
        from core.state import state as _global_state
        user_state = _global_state

    email_addr = account["email"]
    password   = account["password"]
    thread_name = threading.current_thread().name

    from core.config import CAPTCHA_API_KEY
    if not CAPTCHA_API_KEY:
        user_state.update_account(
            email_addr, status="failed",
            error="Chưa cấu hình CAPTCHA API Key (Capsolver)",
        )
        user_state.inc("accounts_failed")
        return

    proxy: Optional[ProxyInfo] = pool.acquire(email_addr) if pool else None
    proxy_id = proxy.id if proxy else "direct"

    user_state.update_account(
        email_addr,
        status="running",
        proxy=proxy_id,
        thread=thread_name,
    )

    try:
        from core.web_client import scan_account_web

        scan_account_web(
            email_addr=email_addr,
            password=password,
            captcha_api_key=CAPTCHA_API_KEY,
            user_state=user_state,
            stop_event=stop_event,
            proxy_dict=proxy,
            mode=mode,
            pool=pool,
        )
    finally:
        if proxy:
            pool.release(proxy)
