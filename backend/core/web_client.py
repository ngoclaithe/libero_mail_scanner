
import re
import time
import requests
import threading
import random
from pathlib import Path
from typing import Optional

from core.config import ALLOWED_MIME, OUTPUT_DIR
from core.captcha_solver import solve_recaptcha_v2, CaptchaError

MAIL_COLUMNS = "600,601,602,603,604,607,609,610,651"

class LiberoWebClient:

    BASE_LOGIN = "https://login.libero.it"
    BASE_MAIL  = "https://mail1.libero.it"

    def __init__(self, captcha_api_key: str, proxy=None):
        self.captcha_api_key = captcha_api_key
        self.session = requests.Session()
        
        adapter = requests.adapters.HTTPAdapter(pool_connections=100, pool_maxsize=100)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        
        self.email = None
        self.ox_session = None
        
        if proxy:
            import urllib.parse
            user = urllib.parse.quote(proxy.username)
            pwd = urllib.parse.quote(proxy.password)
            proxy_url = f"http://{user}:{pwd}@{proxy.host}:{proxy.port}"
            self.session.proxies = {"http": proxy_url, "https": proxy_url}
            
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8",
        })
        self._rate_lock = threading.Lock()
        self._pause_until = 0.0

        self._dl_proxies = []
        self._dl_sessions = {}
        self._proxy_pause = {}
        self._dl_lock = threading.Lock()
        self._dl_rr = 0
        self._pool_ref = None
        self._pool_account = ""
        if proxy:
            self._dl_proxies.append(proxy)

    def add_download_proxies(self, proxies):
        with self._dl_lock:
            existing = {p.id for p in self._dl_proxies}
            for p in proxies:
                if p.id not in existing:
                    self._dl_proxies.append(p)

    def set_pool_ref(self, pool, account_tag):
        self._pool_ref = pool
        self._pool_account = account_tag

    def _build_dl_session(self, proxy):
        import urllib.parse
        sess = requests.Session()
        sess.headers.update(self.session.headers)
        sess.cookies.update(self.session.cookies)
        user = urllib.parse.quote(proxy.username)
        pwd = urllib.parse.quote(proxy.password)
        proxy_url = f"http://{user}:{pwd}@{proxy.host}:{proxy.port}"
        sess.proxies = {"http": proxy_url, "https": proxy_url}
        adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10)
        sess.mount('http://', adapter)
        sess.mount('https://', adapter)
        return sess

    def _get_dl_session(self):
        with self._dl_lock:
            if not self._dl_proxies:
                return self.session, None
            now = time.time()
            n = len(self._dl_proxies)
            for _ in range(n):
                proxy = self._dl_proxies[self._dl_rr % n]
                self._dl_rr += 1
                if now >= self._proxy_pause.get(proxy.id, 0):
                    if proxy.id not in self._dl_sessions:
                        self._dl_sessions[proxy.id] = self._build_dl_session(proxy)
                    return self._dl_sessions[proxy.id], proxy
            earliest = min(self._proxy_pause.values())
            wait = max(0.5, earliest - now + random.uniform(0.5, 1.5))
        time.sleep(wait)
        return self._get_dl_session()

    def _pause_proxy(self, proxy):
        if proxy is None:
            self._pause_until = time.time() + 15.0
            return
        with self._dl_lock:
            self._proxy_pause[proxy.id] = time.time() + 15.0
            active = sum(1 for p in self._dl_proxies
                         if time.time() >= self._proxy_pause.get(p.id, 0))
            print(f"[RATE-LIMIT] Proxy {proxy.id} bị 429, pause 15s. "
                  f"{active}/{len(self._dl_proxies)} proxy còn OK", flush=True)

    def _try_grab_proxies(self):
        if not self._pool_ref:
            return []
        with self._dl_lock:
            if len(self._dl_proxies) >= 8:
                return []
        grabbed = self._pool_ref.acquire_multiple(
            f"{self._pool_account}#dl",
            count=min(3, 8 - len(self._dl_proxies))
        )
        if grabbed:
            self.add_download_proxies(grabbed)
            print(f"[PROXY-GRAB] {self._pool_account} grab thêm {len(grabbed)} proxy! "
                  f"Total: {len(self._dl_proxies)}", flush=True)
        return grabbed

    def login(self, email: str, password: str) -> bool:
        self.email = email
        print(f"[WEB-LOGIN] {email} | Bắt đầu web login...", flush=True)

        self.session.get(f"{self.BASE_LOGIN}/", timeout=15)

        print(f"[WEB-LOGIN] {email} | Đang giải reCAPTCHA v2...", flush=True)
        captcha_token = solve_recaptcha_v2(self.captcha_api_key)
        print(f"[WEB-LOGIN] {email} | ✓ CAPTCHA solved", flush=True)

        resp = self.session.post(
            f"{self.BASE_LOGIN}/logincheck.php",
            data={
                "LOGINID": email,
                "SERVICE_ID": "webmail",
                "RET_URL": f"{self.BASE_MAIL}/appsuite/api/login?action=liberoLogin",
                "g-recaptcha-response": captcha_token,
            },
            allow_redirects=True,
            timeout=30,
        )
        print(f"[WEB-LOGIN] {email} | Step1 status={resp.status_code} url={resp.url}", flush=True)

        if resp.status_code != 200:
            raise WebLoginError(f"Login step1 failed: HTTP {resp.status_code}")

        resp2 = self.session.post(
            f"{self.BASE_LOGIN}/keycheck.php",
            data={
                "LOGINID": email,
                "PASSWORD": password,
                "fullFingerprint[useragent]": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "fullFingerprint[language]": "",
                "fullFingerprint[color]": "32",
                "fullFingerprint[screen]": "1080x1920",
                "fullFingerprint[timezone]": "-60",
                "fullFingerprint[sessionstorage]": "true",
                "fullFingerprint[localstorage]": "true",
                "fullFingerprint[cpu]": "undefined",
                "fullFingerprint[platform]": "Win32",
                "fullFingerprint[donottrack]": "",
                "fullFingerprint[plugin]": "PDF Viewer::Portable Document Format::application/pdf~pdf,text/pdf~pdf",
                "fullFingerprint[canvas]": "",
                "hashFingerprint": "2554233846",
                "adblock": "",
            },
            allow_redirects=True,
            timeout=30,
        )
        print(f"[WEB-LOGIN] {email} | Step2 status={resp2.status_code} url={resp2.url}", flush=True)

        
        if "inters_adv" in resp2.url or resp2.status_code == 200:
            ret_url = None
            ret_m = re.search(r'ret_url=([^&\s"\']+)', resp2.url)
            if ret_m:
                from urllib.parse import unquote
                ret_url = unquote(ret_m.group(1))
            
            if not ret_url:
                ret_m = re.search(r'ret_url[=:]\s*["\'"]?([^"\'\&\s>]+)', resp2.text or "")
                if ret_m:
                    from urllib.parse import unquote
                    ret_url = unquote(ret_m.group(1))
            
            if not ret_url:
                ret_url = f"{self.BASE_MAIL}/appsuite/api/login?action=liberoLogin"
            
            print(f"[WEB-LOGIN] {email} | Step3 interstitial → following ret_url", flush=True)

        resp3 = self.session.get(
            f"{self.BASE_LOGIN}/",
            params={
                "service_id": "appsuite",
                "ret_url": f"{self.BASE_MAIL}/appsuite/api/login?action=liberoLogin",
            },
            allow_redirects=True,
            timeout=30,
        )
        print(f"[WEB-LOGIN] {email} | Step4 SSO status={resp3.status_code} url={resp3.url}", flush=True)

        resp4 = self.session.get(
            f"{self.BASE_MAIL}/appsuite/api/login",
            params={"action": "liberoLogin"},
            allow_redirects=True,
            timeout=30,
        )
        print(f"[WEB-LOGIN] {email} | Step5 OX login status={resp4.status_code} url={resp4.url}", flush=True)
        
        self._extract_ox_session(resp4)
        
        if not self.ox_session:
            self._extract_ox_session(resp3)
        
        if not self.ox_session:
            for r in resp4.history + resp3.history:
                if "session=" in r.url:
                    m = re.search(r'session=([^&]+)', r.url)
                    if m:
                        self.ox_session = m.group(1)
                        break
                self._extract_ox_session(r)
                if self.ox_session:
                    break

        if self.ox_session:
            print(f"[WEB-LOGIN] {email} | ✓ Login OK! session={self.ox_session[:16]}...", flush=True)
            return True
        else:
            body_preview = resp4.text[:500] if resp4.text else "(empty)"
            print(f"[WEB-LOGIN] {email} | ✗ Không tìm thấy OX session", flush=True)
            print(f"[WEB-LOGIN] {email} | Final URL: {resp4.url}", flush=True)
            print(f"[WEB-LOGIN] {email} | History: {[r.url for r in resp4.history]}", flush=True)
            print(f"[WEB-LOGIN] {email} | Body: {body_preview}", flush=True)
            raise WebLoginError(f"Cannot extract OX session after login")

    def _extract_ox_session(self, resp):
        try:
            data = resp.json()
            if "session" in data:
                self.ox_session = data["session"]
                return
        except Exception:
            pass

        if "session=" in resp.url:
            m = re.search(r'session=([^&]+)', resp.url)
            if m:
                self.ox_session = m.group(1)
                return

        for cookie in self.session.cookies:
            if "session" in cookie.name.lower() and cookie.value:
                self.ox_session = cookie.value
                return

    def list_sent_folder(self) -> list[dict]:
        if not self.ox_session:
            raise WebLoginError("Not logged in")

        print(f"[WEB-API] {self.email} | Đang tìm folder sent...", flush=True)
        folders = self._api("folders", action="list", parent="default0",
                            columns="1,300,301,302,304", tree="0")

        sent_folder_id = None
        if isinstance(folders, list):
            print(f"[WEB-API] {self.email} | Folders found: {len(folders)}", flush=True)
            for f in folders:
                if isinstance(f, list) and len(f) >= 2:
                    print(f"[WEB-API] {self.email} |   folder: {f[:3]}", flush=True)
                    for fname_check in f:
                        if isinstance(fname_check, str) and fname_check.lower() in (
                            "sent", "outbox", "posta inviata", "inviata", "inviati",
                            "sent items", "sent mail"
                        ):
                            sent_folder_id = f[0]
                            print(f"[WEB-API] {self.email} | ✓ Found sent folder by name: {sent_folder_id}", flush=True)
                            break
                if sent_folder_id:
                    break
        else:
            print(f"[WEB-API] {self.email} | Folders response type: {type(folders)}, val: {str(folders)[:300]}", flush=True)

        if not sent_folder_id:
            for try_folder in ["default0/Sent", "default0/INBOX.Sent",
                                "default0/INBOX.outbox", "default0/outbox",
                                "default0/Posta inviata", "default0/INBOX.Posta inviata"]:
                try:
                    mails = self._api("mail", action="all", folder=try_folder,
                                      columns=MAIL_COLUMNS, limit="0,5")
                    if mails is not None:
                        sent_folder_id = try_folder
                        print(f"[WEB-API] {self.email} | ✓ Found sent folder by probe: {try_folder}", flush=True)
                        break
                except Exception as e:
                    print(f"[WEB-API] {self.email} |   probe {try_folder} → {e}", flush=True)
                    continue

        if not sent_folder_id:
            raise WebLoginError(f"Cannot find sent/outbox folder")

        self._sent_folder_id = sent_folder_id

        all_mails = []
        offset = 0
        batch_size = 50

        while True:
            mails = self._api("mail", action="all", folder=sent_folder_id,
                              columns=MAIL_COLUMNS,
                              limit=f"{offset},{offset + batch_size}",
                              sort="609", order="desc")

            if not mails or not isinstance(mails, list) or len(mails) == 0:
                break

            all_mails.extend(mails)
            offset += len(mails)

            if len(mails) < batch_size:
                break

        print(f"[WEB-API] {self.email} | Tìm thấy {len(all_mails)} email trong {sent_folder_id}", flush=True)
        return all_mails

    def get_mail_detail(self, folder: str, mail_id: str) -> dict:
        return self._api("mail", action="get", folder=folder, id=mail_id)

    def download_attachment(self, folder: str, mail_id: str,
                            attachment_id: str) -> bytes:
        url = f"{self.BASE_MAIL}/appsuite/api/mail"
        for attempt in range(8):
            sess, proxy = self._get_dl_session()
            try:
                resp = sess.get(url, params={
                    "action": "attachment",
                    "folder": folder,
                    "id": mail_id,
                    "attachment": attachment_id,
                    "session": self.ox_session,
                }, timeout=60)
                if resp.status_code in [429, 406]:
                    self._pause_proxy(proxy)
                    continue
                resp.raise_for_status()
                return resp.content
            except Exception as e:
                err_str = str(e).lower()
                if "rate_limit" in err_str or "too many" in err_str:
                    self._pause_proxy(proxy)
                    continue
                if attempt >= 7:
                    raise e
                time.sleep(1)
        raise WebApiError("RATE_LIMIT Exhausted after proxy rotation")

    def _api(self, module: str, **params):
        params["session"] = self.ox_session
        url = f"{self.BASE_MAIL}/appsuite/api/{module}"

        max_retries = 5
        for attempt in range(max_retries):
            sess, proxy = self._get_dl_session()
            try:
                resp = sess.get(url, params=params, timeout=30)
                if resp.status_code in [429, 406]:
                    self._pause_proxy(proxy)
                    continue
                print(f"[OX-API] {module}?action={params.get('action','')} → status={resp.status_code} len={len(resp.text)}", flush=True)
                resp.raise_for_status()
                
                try:
                    data = resp.json()
                except Exception as je:
                    print(f"[OX-API] {module} NOT JSON (attempt {attempt+1}): {resp.text[:300]}", flush=True)
                    raise WebApiError(f"Extra data / non-JSON: {je}")

                if "error" in data:
                    print(f"[OX-API] {module} ERROR: {data.get('error_desc', data['error'])}", flush=True)
                    raise WebApiError(f"OX API error: {data.get('error_desc', data['error'])}")

                return data.get("data", data)
                
            except Exception as e:
                err_str = str(e).lower()
                if "rate_limit" in err_str or "too many" in err_str:
                    self._pause_proxy(proxy)
                    continue
                if "ox api error" in str(e).lower() or "not json" in str(e).lower():
                    raise e
                if attempt == max_retries - 1:
                    print(f"[OX-API] {module} FAILED after {max_retries} attempts: {e}", flush=True)
                    raise e
                print(f"[OX-API] Lỗi {e}, thử lại {attempt+1}/{max_retries}...", flush=True)
                time.sleep(2)
                
        raise WebApiError("RATE_LIMIT Exhausted - Failed API max attempts")

class WebLoginError(Exception):
    pass

class WebApiError(Exception):
    pass

def scan_account_web(
    email_addr: str,
    password: str,
    captcha_api_key: str,
    user_state,
    stop_event,
    proxy_dict: Optional[dict] = None,
    mode: str = "adaptive",
    pool=None,
):
    from core.classifier import ai_queue

    slug = re.sub(r'[^\w]', '_', email_addr.split("@")[0])
    raw_dir = OUTPUT_DIR / slug / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    user_state.update_account(email_addr,
                              status="scanning",
                              error="Web login (giải CAPTCHA)...")

    MAX_CAPTCHA_RETRIES = 3
    client = None
    extra_proxies = []

    try:
        for captcha_attempt in range(1, MAX_CAPTCHA_RETRIES + 1):
            try:
                client = LiberoWebClient(captcha_api_key, proxy=proxy_dict)
                client.login(email_addr, password)
                break
            except CaptchaError as ce:
                print(f"[WEB-LOGIN] {email_addr} | Captcha lần {captcha_attempt}/{MAX_CAPTCHA_RETRIES} thất bại: {ce}", flush=True)
                if captcha_attempt >= MAX_CAPTCHA_RETRIES:
                    raise
                user_state.update_account(email_addr, error=f"Captcha retry {captcha_attempt+1}/{MAX_CAPTCHA_RETRIES}...")
                continue
            except WebLoginError as wle:
                err_msg = str(wle).lower()
                if any(k in err_msg for k in ["authen", "password", "credential", "blocked"]):
                    raise
                print(f"[WEB-LOGIN] {email_addr} | Login lần {captcha_attempt}/{MAX_CAPTCHA_RETRIES} lỗi: {wle}", flush=True)
                if captcha_attempt >= MAX_CAPTCHA_RETRIES:
                    raise
                continue
            except Exception as ge:
                print(f"[WEB-LOGIN] {email_addr} | Lỗi mạng/ngoại lệ lần {captcha_attempt}/{MAX_CAPTCHA_RETRIES}: {ge}", flush=True)
                if captcha_attempt >= MAX_CAPTCHA_RETRIES:
                    raise WebLoginError(f"System/Network error: {ge}")
                time.sleep(3)
                continue

        if pool:
            extra_proxies = pool.acquire_multiple(f"{email_addr}#dl", count=4)
            if extra_proxies:
                client.add_download_proxies(extra_proxies)
                client.set_pool_ref(pool, email_addr)
                print(f"[PROXY-GRAB] {email_addr} grabbed {len(extra_proxies)} extra proxies for download", flush=True)

        user_state.update_account(email_addr, error="Web login OK, đang quét...")

        if stop_event.is_set() or user_state.accounts.get(email_addr, {}).get("status") == "stopped":
            user_state.update_account(email_addr, status="stopped", error="Đã dừng phiên quét")
            return

        mails = client.list_sent_folder()
        total = len(mails)

        mails_with_att = [m for m in mails if isinstance(m, list) and len(m) > 2 and m[2]]
        skipped = total - len(mails_with_att)
        if mails_with_att:
            print(f"[WEB-API] {email_addr} | {total} email, {len(mails_with_att)} có attachment, skip {skipped}", flush=True)
            mails = mails_with_att

        user_state.update_account(email_addr, total_mail=len(mails))

        images_found = 0
        manifest_rows = []
        import concurrent.futures
        import threading
        state_lock = threading.Lock()

        def _process_single_mail(idx, mail_meta):
            nonlocal images_found
            
            if stop_event.is_set() or user_state.accounts.get(email_addr, {}).get("status") == "stopped":
                return None
            if mode == "adaptive" and user_state.accounts.get(email_addr, {}).get("document_found"):
                return None

            local_rows = []
            try:
                if isinstance(mail_meta, list) and len(mail_meta) >= 2:
                    mail_id = str(mail_meta[0])
                    folder = str(mail_meta[1])
                else:
                    return None

                detail = client.get_mail_detail(folder, mail_id)
                if not detail:
                    return None

                attachments = detail.get("attachments", [])
                date = detail.get("received_date", "")
                to_addr = ""
                to_data = detail.get("to", [])
                if to_data and isinstance(to_data, list) and len(to_data) > 0:
                    to_addr = str(to_data[0][-1]) if isinstance(to_data[0], list) else str(to_data[0])

                for att in attachments:
                    if mode == "adaptive" and user_state.accounts.get(email_addr, {}).get("document_found"):
                        break

                    if isinstance(att, dict):
                        mime = att.get("content_type", "").lower()
                        att_id = att.get("id", "")
                        filename = att.get("filename", f"att_{att_id}")
                        size = att.get("size", 0)
                    elif isinstance(att, list) and len(att) >= 4:
                        att_id = str(att[0])
                        mime = str(att[1]).lower()
                        filename = str(att[3]) if att[3] else f"att_{att_id}"
                        size = att[2] if len(att) > 2 else 0
                    else:
                        continue

                    if mime not in ALLOWED_MIME:
                        continue
                    if size and (size < 10_000 or size > 15_000_000):
                        continue

                    try:
                        content = client.download_attachment(folder, mail_id, att_id)
                        if not content or len(content) < 1000:
                            continue

                        fname = _safe_name(filename)
                        dest = raw_dir / f"mail_{idx:04d}_{fname}"
                        dest.write_bytes(content)

                        ai_queue.put((email_addr, str(dest), mime, user_state.user_id))
                        
                        with state_lock:
                            images_found += 1
                            local_rows.append({
                                "mail_no": idx,
                                "date": date,
                                "recipient": to_addr,
                                "filename": fname,
                                "filepath": str(dest),
                                "size": len(content),
                                "mime": mime,
                            })
                            user_state.update_account(email_addr,
                                                      images_found=images_found,
                                                      last_file=fname)
                            user_state.inc("images_total")

                    except Exception as e:
                        print(f"[WEB-SCAN] {email_addr} | Lỗi tải attachment {att_id}: {e}", flush=True)

            except Exception as e:
                pass
            
            return local_rows

        offset_idx = 0
        total_mails = len(mails)

        while offset_idx < total_mails:
            if stop_event.is_set() or user_state.accounts.get(email_addr, {}).get("status") == "stopped":
                break
                
            running_accounts = len([a for a in user_state.accounts.values() if a.get("status") == "running"])
            max_w = min(15, max(5, 100 // max(1, running_accounts)))

            if offset_idx % 100 == 0 and pool:
                grabbed = client._try_grab_proxies()
                if grabbed:
                    extra_proxies.extend(grabbed)

            chunk = mails[offset_idx:]

            with concurrent.futures.ThreadPoolExecutor(max_workers=max_w) as executor:
                futures_list = []
                for i, mail_meta in enumerate(chunk):
                    f = executor.submit(_process_single_mail, offset_idx + i, mail_meta)
                    futures_list.append((offset_idx + i, f))

                for future_idx, f in futures_list:
                    if stop_event.is_set():
                        f.cancel()
                        continue
                        
                    try:
                        res = f.result()
                        if res:
                            manifest_rows.extend(res)
                        offset_idx = future_idx + 1
                        if offset_idx % 5 == 0:
                            user_state.update_account(email_addr, processed=offset_idx)
                    except WebApiError as we:
                        print(f"[WEB-SCAN] {email_addr} | Lỗi WebAPI (Exhausted): {we}", flush=True)
                        raise
                    except Exception as e:
                        print(f"[WEB-SCAN] {email_addr} | Bỏ qua thư lỗi tại {future_idx}: {e}", flush=True)
                        offset_idx = future_idx + 1

        user_state.update_account(email_addr, processed=len(mails))

        if manifest_rows:
            import csv
            manifest_path = raw_dir.parent / "manifest.csv"
            with open(manifest_path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=manifest_rows[0].keys())
                writer.writeheader()
                writer.writerows(manifest_rows)

        user_state.update_account(email_addr, status="done")
        user_state.inc("accounts_done")
        print(f"[WEB-API] {email_addr} | ✓ Done! {images_found} ảnh tìm thấy", flush=True)

    except (CaptchaError, WebLoginError, WebApiError) as e:
        user_state.update_account(email_addr, status="failed", error=f"Web: {e}")
        user_state.inc("accounts_failed")
        print(f"[WEB-API] {email_addr} | ✗ {type(e).__name__}: {e}", flush=True)
    except Exception as e:
        user_state.update_account(email_addr, status="failed", error=f"Web: {e}")
        user_state.inc("accounts_failed")
        print(f"[WEB-API] {email_addr} | ✗ Unexpected: {e}", flush=True)
    finally:
        if pool:
            for p in extra_proxies:
                pool.release(p)

def _safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    return name[:200] if name else "unnamed"
