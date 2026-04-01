"""
Libero Webmail client — bypass IMAP using web login + OX App Suite API.

Flow:
  1. Solve reCAPTCHA v2 via 2Captcha
  2. POST login.libero.it/logincheck.php (email + captcha)
  3. POST logincheck.php (email + password)
  4. Follow redirect → mail1.libero.it/appsuite/api/login
  5. Use OX App Suite API to list sent mail + download attachments
"""

import re
import time
import requests
from pathlib import Path
from typing import Optional

from core.config import ALLOWED_MIME, OUTPUT_DIR
from core.captcha_solver import solve_recaptcha_v2, CaptchaError


# ── OX App Suite mail columns ────────────────────────────────
# https://documentation.open-xchange.com/
# 600=id, 601=folder_id, 602=attachment, 603=from, 604=to,
# 607=subject, 609=date, 610=size, 651=has_attachment
MAIL_COLUMNS = "600,601,602,603,604,607,609,610,651"


class LiberoWebClient:
    """
    Stateful client: login once, reuse session for all API calls.
    """

    BASE_LOGIN = "https://login.libero.it"
    BASE_MAIL  = "https://mail1.libero.it"

    def __init__(self, captcha_api_key: str, proxy: Optional[dict] = None):
        self.captcha_api_key = captcha_api_key
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8",
        })
        if proxy:
            self.session.proxies = proxy
        self.ox_session = None  # OX session token
        self.email = None

    # ── LOGIN ─────────────────────────────────────────────────

    def login(self, email: str, password: str) -> bool:
        """
        Full Libero login flow with reCAPTCHA solving.
        Returns True on success, raises on failure.
        """
        self.email = email
        print(f"[WEB-LOGIN] {email} | Bắt đầu web login...", flush=True)

        # Step 0: Load login page to get cookies
        self.session.get(f"{self.BASE_LOGIN}/", timeout=15)

        # Step 1: Solve reCAPTCHA v2
        print(f"[WEB-LOGIN] {email} | Đang giải reCAPTCHA v2...", flush=True)
        captcha_token = solve_recaptcha_v2(self.captcha_api_key)
        print(f"[WEB-LOGIN] {email} | ✓ CAPTCHA solved", flush=True)

        # Step 2: POST email + captcha (step 1 of login form)
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

        # Check if we need password step
        if resp.status_code != 200:
            raise WebLoginError(f"Login step1 failed: HTTP {resp.status_code}")

        # Step 3: POST password to /keycheck.php (NOT /logincheck.php!)
        # Libero requires browser fingerprint fields alongside credentials
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

        # Step 4: After keycheck.php → 302 → inters_adv.phtml (interstitial ad page)
        # The interstitial page has ret_url param. We need to follow the SSO chain:
        #   inters_adv.phtml → /?service_id=appsuite&ret_url=... → mail1.libero.it/appsuite/api/login
        
        # If we landed on inters_adv.phtml, extract ret_url and follow manually
        if "inters_adv" in resp2.url or resp2.status_code == 200:
            # Extract ret_url from the page or from URL params
            ret_url = None
            ret_m = re.search(r'ret_url=([^&\s"\']+)', resp2.url)
            if ret_m:
                from urllib.parse import unquote
                ret_url = unquote(ret_m.group(1))
            
            if not ret_url:
                # Try to find it in the page body
                ret_m = re.search(r'ret_url[=:]\s*["\']?([^"\'&\s>]+)', resp2.text or "")
                if ret_m:
                    from urllib.parse import unquote
                    ret_url = unquote(ret_m.group(1))
            
            if not ret_url:
                ret_url = f"{self.BASE_MAIL}/appsuite/api/login?action=liberoLogin"
            
            print(f"[WEB-LOGIN] {email} | Step3 interstitial → following ret_url", flush=True)

        # Step 5: Navigate to /?service_id=appsuite to trigger SSO redirect chain
        # This sets up the SSO cookies for mail1.libero.it
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

        # Step 6: Follow the OX login endpoint — this should give us the session
        # mail1.libero.it/appsuite/api/login?action=liberoLogin → 302 with ssonc
        resp4 = self.session.get(
            f"{self.BASE_MAIL}/appsuite/api/login",
            params={"action": "liberoLogin"},
            allow_redirects=True,
            timeout=30,
        )
        print(f"[WEB-LOGIN] {email} | Step5 OX login status={resp4.status_code} url={resp4.url}", flush=True)
        
        # Extract OX session from the response chain
        self._extract_ox_session(resp4)
        
        # Also check resp3 in case session was set there
        if not self.ox_session:
            self._extract_ox_session(resp3)
        
        # Check all redirect history for session token
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
        """Try to extract OX session from JSON response, URL, or cookies."""
        # From JSON body
        try:
            data = resp.json()
            if "session" in data:
                self.ox_session = data["session"]
                return
        except Exception:
            pass

        # From URL params
        if "session=" in resp.url:
            m = re.search(r'session=([^&]+)', resp.url)
            if m:
                self.ox_session = m.group(1)
                return

        # From cookies
        for cookie in self.session.cookies:
            if "session" in cookie.name.lower() and cookie.value:
                self.ox_session = cookie.value
                return

    # ── LIST SENT EMAILS ──────────────────────────────────────

    def list_sent_folder(self) -> list[dict]:
        """
        Get all emails from sent/outbox folder.
        Returns list of email metadata dicts.
        """
        if not self.ox_session:
            raise WebLoginError("Not logged in")

        # First, find the sent folder
        folders = self._api("folders", action="list", parent="default0",
                            columns="1,300,301,302,304", tree="0")

        sent_folder_id = None
        if isinstance(folders, list):
            for f in folders:
                if isinstance(f, list) and len(f) >= 2:
                    folder_name = str(f[0]).lower() if f else ""
                    folder_id = str(f[0]) if f else ""
                    # Check for sent/outbox folders
                    for fname_check in f:
                        if isinstance(fname_check, str) and fname_check.lower() in (
                            "sent", "outbox", "posta inviata", "inviata", "inviati"
                        ):
                            sent_folder_id = f[0]
                            break
                if sent_folder_id:
                    break

        if not sent_folder_id:
            # Try standard OX folder IDs
            # Usually: default0/INBOX.Sent or default0/Sent
            for try_folder in ["default0/INBOX.Sent", "default0/Sent",
                                "default0/INBOX.outbox", "default0/outbox"]:
                try:
                    mails = self._api("mail", action="all", folder=try_folder,
                                      columns=MAIL_COLUMNS, limit="0,50")
                    if mails is not None:
                        sent_folder_id = try_folder
                        print(f"[WEB-API] {self.email} | Found sent folder: {try_folder}", flush=True)
                        break
                except Exception:
                    continue

        if not sent_folder_id:
            raise WebLoginError(f"Cannot find sent/outbox folder")

        self._sent_folder_id = sent_folder_id

        # Fetch all emails from sent folder
        all_mails = []
        offset = 0
        batch_size = 50

        while True:
            mails = self._api("mail", action="all", folder=sent_folder_id,
                              columns=MAIL_COLUMNS,
                              limit=f"{offset},{batch_size}",
                              sort="609", order="desc")  # Sort by date desc

            if not mails or not isinstance(mails, list) or len(mails) == 0:
                break

            all_mails.extend(mails)
            offset += batch_size

            if len(mails) < batch_size:
                break

        print(f"[WEB-API] {self.email} | Tìm thấy {len(all_mails)} email trong {sent_folder_id}", flush=True)
        return all_mails

    # ── GET MAIL + ATTACHMENTS ─────────────────────────────────

    def get_mail_detail(self, folder: str, mail_id: str) -> dict:
        """Get full mail detail including attachments list."""
        return self._api("mail", action="get", folder=folder, id=mail_id)

    def download_attachment(self, folder: str, mail_id: str,
                            attachment_id: str) -> bytes:
        """Download raw attachment bytes."""
        url = f"{self.BASE_MAIL}/appsuite/api/mail"
        resp = self.session.get(url, params={
            "action": "attachment",
            "folder": folder,
            "id": mail_id,
            "attachment": attachment_id,
            "session": self.ox_session,
        }, timeout=60)
        resp.raise_for_status()
        return resp.content

    # ── INTERNAL API CALL ─────────────────────────────────────

    def _api(self, module: str, **params):
        """Call OX App Suite API and return JSON data."""
        params["session"] = self.ox_session
        url = f"{self.BASE_MAIL}/appsuite/api/{module}"

        resp = self.session.get(url, params=params, timeout=30)
        resp.raise_for_status()

        data = resp.json()
        if "error" in data:
            raise WebApiError(f"OX API error: {data.get('error_desc', data['error'])}")

        return data.get("data", data)


class WebLoginError(Exception):
    pass


class WebApiError(Exception):
    pass


# ── Standalone scan function (called from worker) ────────────

def scan_account_web(
    email_addr: str,
    password: str,
    captcha_api_key: str,
    user_state,
    stop_event,
    proxy_dict: Optional[dict] = None,
):
    """
    Web-based fallback scanner for accounts blocked on IMAP.
    Same output as IMAP scanner: downloads attachments → pushes to AI queue.
    """
    from core.classifier import ai_queue

    slug = re.sub(r'[^\w]', '_', email_addr.split("@")[0])
    raw_dir = OUTPUT_DIR / slug / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    user_state.update_account(email_addr,
                              status="running",
                              error="Web login (giải CAPTCHA)...")

    client = LiberoWebClient(captcha_api_key, proxy=proxy_dict)

    try:
        # 1. Login
        client.login(email_addr, password)
        user_state.update_account(email_addr, error="Web login OK, đang quét...")

        if stop_event.is_set():
            user_state.update_account(email_addr, status="stopped")
            return

        # 2. List sent folder
        mails = client.list_sent_folder()
        total = len(mails)
        user_state.update_account(email_addr, total_mail=total)

        images_found = 0
        manifest_rows = []

        # 3. Process each email
        for idx, mail_meta in enumerate(mails):
            if stop_event.is_set():
                user_state.update_account(email_addr, status="stopped")
                return

            try:
                # mail_meta is a list: [id, folder, attachment_flag, from, to, subject, date, size, has_att]
                if isinstance(mail_meta, list) and len(mail_meta) >= 9:
                    mail_id = str(mail_meta[0])
                    folder = str(mail_meta[1])
                    has_att = mail_meta[8]  # has_attachment flag

                    if not has_att:
                        user_state.update_account(email_addr, processed=idx + 1)
                        continue
                else:
                    user_state.update_account(email_addr, processed=idx + 1)
                    continue

                # Get mail detail for attachments
                detail = client.get_mail_detail(folder, mail_id)
                if not detail:
                    user_state.update_account(email_addr, processed=idx + 1)
                    continue

                attachments = detail.get("attachments", [])
                date = detail.get("received_date", "")
                to_addr = ""
                to_data = detail.get("to", [])
                if to_data and isinstance(to_data, list) and len(to_data) > 0:
                    to_addr = str(to_data[0][-1]) if isinstance(to_data[0], list) else str(to_data[0])

                for att in attachments:
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

                    # Filter by allowed MIME types
                    if mime not in ALLOWED_MIME:
                        continue

                    # Skip too small (<10KB) or too large (>15MB)
                    if size and (size < 10_000 or size > 15_000_000):
                        continue

                    # Download
                    try:
                        content = client.download_attachment(folder, mail_id, att_id)
                        if not content or len(content) < 1000:
                            continue

                        fname = _safe_name(filename)
                        dest = raw_dir / f"mail_{idx:04d}_{fname}"
                        dest.write_bytes(content)

                        # Push to AI queue
                        ai_queue.put((email_addr, str(dest), mime, user_state.user_id))
                        images_found += 1
                        manifest_rows.append({
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
                        print(f"[WEB-API] {email_addr} | Lỗi tải attachment {att_id}: {e}", flush=True)

            except Exception as e:
                print(f"[WEB-API] {email_addr} | Lỗi xử lý mail {idx}: {e}", flush=True)

            user_state.update_account(email_addr, processed=idx + 1)

        # 4. Write manifest
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


def _safe_name(name: str) -> str:
    """Sanitize filename."""
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    return name[:200] if name else "unnamed"
