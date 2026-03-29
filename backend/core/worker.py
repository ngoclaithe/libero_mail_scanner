"""
Per-account IMAP worker.
Runs inside a ThreadPoolExecutor thread.
Uses ImapClient (thread-safe) instead of monkey-patched socket.
"""

import csv
import imaplib
import re
import time
import threading
from email import message_from_bytes
from email.header import decode_header
from pathlib import Path
from typing import Optional

from core.config import (
    IMAP_HOST, SENT_FOLDER, BATCH_SIZE, RETRY_MAX, ALLOWED_MIME, OUTPUT_DIR,
)
from core.imap_client import new_client
from core.proxy_pool import ProxyPool, ProxyInfo, ProxyStatus
from core.state import AppState
from core.classifier import ai_queue


# ── Header decode ─────────────────────────────────────────────

def _decode(value) -> str:
    if value is None:
        return ""
    parts = decode_header(value)
    result = []
    for chunk, enc in parts:
        if isinstance(chunk, bytes):
            result.append(chunk.decode(enc or "utf-8", errors="replace"))
        else:
            result.append(str(chunk))
    return " ".join(result)


def _safe_name(name: str) -> str:
    return re.sub(r'[^\w.\-_ ]', '_', name).strip() or "attachment"


# ── Main worker ───────────────────────────────────────────────

def run_account(
    account:    dict,
    pool:       ProxyPool,
    stop_event: threading.Event,
    user_state: "AppState" = None,
):
    """
    Download all image/* and PDF attachments from one account's outbox.
    Updates the per-user state object as it progresses.
    """
    # Use passed-in per-user state (fall back to module-level for backwards compat)
    if user_state is None:
        from core.state import state as _global_state
        user_state = _global_state
    
    email_addr = account["email"]
    password   = account["password"]
    thread_name = threading.current_thread().name

    # ── Acquire proxy ─────────────────────────────────────────
    proxy: Optional[ProxyInfo] = pool.acquire(email_addr)
    proxy_id = proxy.id if proxy else "direct"

    user_state.update_account(email_addr,
                         status="running",
                         proxy=proxy_id,
                         thread=thread_name)

    # Output dir
    slug    = re.sub(r'[^\w]', '_', email_addr.split("@")[0])
    raw_dir = OUTPUT_DIR / slug / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    mail = None
    try:
        # ── Connect + login (with retry) ──────────────────────
        for attempt in range(RETRY_MAX):
            if stop_event.is_set():
                user_state.update_account(email_addr, status="stopped")
                return
            try:
                mail = new_client(proxy=proxy)
                mail.login(email_addr, password)
                break
            except imaplib.IMAP4.error as e:
                err = str(e)
                _handle_auth_error(proxy, pool, err)
                user_state.update_account(email_addr, status="failed", error=err)
                return
            except Exception as e:
                err = str(e)
                if attempt == RETRY_MAX - 1:
                    _handle_conn_error(proxy, pool, err)
                    user_state.update_account(email_addr, status="failed", error=err)
                    return
                time.sleep(2 ** attempt)

        # ── Select outbox ─────────────────────────────────────
        status, _ = mail.select(f'"{SENT_FOLDER}"')
        if status != "OK":
            user_state.update_account(email_addr,
                                 status="failed",
                                 error=f"Cannot open {SENT_FOLDER}")
            return

        _, data   = mail.search(None, "ALL")
        all_ids   = data[0].split() if data[0] else []
        total     = len(all_ids)
        user_state.update_account(email_addr, total_mail=total)

        images_found = 0
        manifest_rows: list[dict] = []

        # ── Fetch in batches ──────────────────────────────────
        for b_start in range(0, total, BATCH_SIZE):
            if stop_event.is_set():
                user_state.update_account(email_addr, status="stopped")
                return

            batch = all_ids[b_start: b_start + BATCH_SIZE]

            for local_i, mid in enumerate(batch):
                mail_no = b_start + local_i + 1
                try:
                    _, msg_data = mail.fetch(mid, "(RFC822)")
                    if not msg_data or msg_data[0] is None:
                        continue

                    msg  = message_from_bytes(msg_data[0][1])
                    date = msg.get("Date", "")
                    to   = _decode(msg.get("To", ""))

                    att_i = 0
                    for part in msg.walk():
                        ct = part.get_content_type().lower()
                        if ct not in ALLOWED_MIME:
                            continue
                        if part.get("Content-Disposition") is None:
                            continue

                        orig = _decode(part.get_filename() or "")
                        if not orig:
                            ext  = ct.split("/")[-1].replace("jpeg", "jpg")
                            orig = f"att_{att_i}.{ext}"
                        att_i += 1

                        fname   = _safe_name(orig)
                        dest    = raw_dir / f"mail_{mail_no:04d}_{fname}"
                        payload = part.get_payload(decode=True)
                        if not payload:
                            continue

                        dest.write_bytes(payload)
                        ai_queue.put((email_addr, str(dest), ct))
                        images_found += 1
                        manifest_rows.append({
                            "mail_no":   mail_no,
                            "date":      date,
                            "recipient": to,
                            "filename":  fname,
                            "filepath":  str(dest),
                            "size":      len(payload),
                            "mime":      ct,
                        })

                        user_state.update_account(email_addr,
                                             images_found=images_found,
                                             last_file=fname)
                        user_state.inc("images_total")

                except Exception:
                    pass   # non-fatal: skip this message

            user_state.update_account(email_addr, processed=min(b_start + BATCH_SIZE, total))
            time.sleep(0.2)

        # ── Write manifest ────────────────────────────────────
        _write_manifest(raw_dir.parent / "manifest.csv", manifest_rows)

        user_state.update_account(email_addr, status="done")
        user_state.inc("accounts_done")

    except Exception as e:
        user_state.update_account(email_addr, status="failed", error=str(e))
        user_state.inc("accounts_failed")
        if proxy:
            pool.mark_dead(proxy, str(e))
    finally:
        if proxy:
            pool.release(proxy)
        if mail:
            try:
                mail.logout()
            except Exception:
                pass


# ── Helpers ───────────────────────────────────────────────────

def _handle_auth_error(proxy, pool, err: str):
    if proxy:
        if "rate" in err.lower() or "too many" in err.lower():
            pool.mark_rate_limited(proxy, err)
        else:
            pool.mark_blocked(proxy, err)


def _handle_conn_error(proxy, pool, err: str):
    if proxy:
        pool.mark_dead(proxy, err)


def _write_manifest(path: Path, rows: list[dict]):
    if not rows:
        return
    import csv as _csv
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=[
            "mail_no", "date", "recipient",
            "filename", "filepath", "size", "mime",
        ])
        w.writeheader()
        w.writerows(rows)
