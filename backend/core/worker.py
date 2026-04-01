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


# ── Custom IMAP Parser ────────────────────────────────────────

def _parse_imap_list(s: str) -> list:
    stack = [[]]
    token = ""
    in_str = False
    escape = False
    i = 0
    while i < len(s):
        char = s[i]
        if escape:
            token += char
            escape = False
        elif char == '\\':
            escape = True
        elif char == '"':
            in_str = not in_str
        elif in_str:
            token += char
        elif char == '(':
            stack.append([])
        elif char == ')':
            if token:
                stack[-1].append(token)
                token = ""
            sub = stack.pop()
            stack[-1].append(sub)
        elif char == ' ':
            if token:
                stack[-1].append(token)
                token = ""
        else:
            token += char
        i += 1
    if token: stack[-1].append(token)
    return stack[0][0] if stack and stack[0] else []

def _find_parts(parsed, prefix="") -> list:
    parts = []
    if isinstance(parsed, list) and len(parsed) > 0:
        if isinstance(parsed[0], list): # Multipart
            # Cấu trúc multipart chứa các sub-parts, ta đếm index (1-based)
            subparts = [p for p in parsed if isinstance(p, list)]
            for i, p in enumerate(subparts):
                num = f"{prefix}.{i+1}" if prefix else str(i+1)
                parts.extend(_find_parts(p, num))
        elif isinstance(parsed[0], str): # Single part
            mime1 = parsed[0].lower()
            mime2 = parsed[1].lower() if len(parsed) > 1 and isinstance(parsed[1], str) else ""
            mime = f"{mime1}/{mime2}"
            num = prefix if prefix else "1"
            parts.append((num, mime, parsed))
    return parts

def _extract_bodystructure(header_bytes: bytes) -> str:
    """Extracts the BODYSTRUCTURE (...) substring from IMAP response."""
    s = header_bytes.decode('utf-8', errors='ignore')
    # Tìm index của "BODYSTRUCTURE ("
    idx = s.find("BODYSTRUCTURE (")
    if idx == -1: return ""
    idx += len("BODYSTRUCTURE ")
    
    # Matching parenthesis
    open_p = 0
    for i in range(idx, len(s)):
        if s[i] == '(': open_p += 1
        elif s[i] == ')': open_p -= 1
        if open_p == 0:
            return s[idx:i+1]
    return ""

def _get_part_size(p_info: list) -> int:
    """Safely extract size in bytes from IMAP BODYSTRUCTURE part."""
    try:
        if len(p_info) > 6:
            for item in p_info[5:10]:
                if isinstance(item, str) and item.isdigit():
                    return int(item)
    except Exception:
        pass
    return -1




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
    max_proxy_switches = min(len(pool) // 2, 10)  # Tối đa thử nửa pool, cap 10
    proxy_switches = 0

    try:
        # ── Connect + login (with retry + proxy rotation) ─────
        attempt = 0
        while attempt < RETRY_MAX:
            if stop_event.is_set():
                user_state.update_account(email_addr, status="stopped")
                return
            try:
                mail = new_client(proxy=proxy)
                mail.login(email_addr, password)
                break
            except imaplib.IMAP4.error as e:
                err = str(e)
                # ── Policy bsc KO = IP bị chặn → đổi proxy thử lại ──
                if "policy" in err.lower() and "ko" in err.lower():
                    if proxy and proxy_switches < max_proxy_switches:
                        old_proxy_id = proxy.id
                        pool.mark_blocked(proxy, err)
                        pool.release(proxy)
                        proxy_switches += 1
                        proxy = pool.acquire(email_addr)
                        new_proxy_id = proxy.id if proxy else "direct"
                        print(f"[PROXY-ROTATE] {email_addr}: Policy KO trên {old_proxy_id} → đổi sang {new_proxy_id} (lần {proxy_switches}/{max_proxy_switches})", flush=True)
                        user_state.update_account(email_addr,
                                                  proxy=new_proxy_id,
                                                  error=f"Policy KO → đổi proxy ({proxy_switches}/{max_proxy_switches})")
                        time.sleep(1)
                        attempt = 0       # Reset retry counter cho proxy mới
                        continue
                    else:
                        # Hết proxy hoặc hết lượt đổi
                        if proxy:
                            pool.mark_blocked(proxy, err)
                        user_state.update_account(email_addr, status="failed",
                                                  error=f"Policy KO — đã thử {proxy_switches} proxy, không có proxy nào dùng được")
                        return
                else:
                    # Lỗi auth thật (sai mật khẩu, account bị khóa...)
                    _handle_auth_error(proxy, pool, err)
                    user_state.update_account(email_addr, status="failed", error=err)
                    return
            except Exception as e:
                err = str(e)
                # ── Proxy lỗi (502/407/connection refused) → đổi proxy ──
                is_proxy_error = proxy and any(k in err.lower() for k in [
                    "proxy rejected", "502", "407", "bad gateway",
                    "connection refused", "connect tunnel",
                ])
                if is_proxy_error and proxy_switches < max_proxy_switches:
                    old_proxy_id = proxy.id
                    pool.mark_dead(proxy, err)
                    pool.release(proxy)
                    proxy_switches += 1
                    proxy = pool.acquire(email_addr)
                    new_proxy_id = proxy.id if proxy else "direct"
                    print(f"[PROXY-ROTATE] {email_addr}: Proxy lỗi ({old_proxy_id}) → đổi sang {new_proxy_id} (lần {proxy_switches}/{max_proxy_switches})", flush=True)
                    user_state.update_account(email_addr,
                                              proxy=new_proxy_id,
                                              error=f"Proxy lỗi → đổi proxy ({proxy_switches}/{max_proxy_switches})")
                    time.sleep(1)
                    attempt = 0
                    continue
                else:
                    attempt += 1
                    if attempt >= RETRY_MAX:
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
            batch_str = b','.join(batch)
            
            try:
                # 1. Fetch BODYSTRUCTURE và thông tin Header cơ bản
                t_fetch = time.time()
                status, msg_data_list = mail.fetch(batch_str, "(BODYSTRUCTURE BODY.PEEK[HEADER.FIELDS (DATE TO)])")
                t_elapsed = time.time() - t_fetch
                
                if status != "OK" or not msg_data_list:
                    current_processed = min(b_start + BATCH_SIZE, total)
                    user_state.update_account(email_addr, processed=current_processed)
                    continue
                    
                print(f"[IMAP-PERF] {email_addr.split('@')[0]}: Tải BODYSTRUCTURE {len(batch)} email tốn {t_elapsed:.2f}s", flush=True)

                current_processed = b_start
                for item in msg_data_list:
                    if not isinstance(item, tuple):
                        continue

                    header_b = item[0]
                    msg_body = item[1]
                    
                    try:
                        mail_no = int(header_b.split(b' ', 1)[0])
                    except ValueError:
                        mail_no = b_start + 1

                    msg  = message_from_bytes(msg_body)
                    date = msg.get("Date", "")
                    to   = _decode(msg.get("To", ""))

                    bs_str = _extract_bodystructure(header_b)
                    if not bs_str:
                        continue
                        
                    parsed_bs = _parse_imap_list(bs_str)
                    parts_info = _find_parts(parsed_bs)
                    
                    # Lọc những phần có MIME type được phép
                    target_parts = [p for p in parts_info if p[1] in ALLOWED_MIME]
                    
                    att_i = 0
                    for part_num, mime, p_info in target_parts:
                        # Tối ưu băng thông Proxy: Lấy dung lượng file TỪ HEADER để quyết định tải hay bỏ qua
                        size_bytes = _get_part_size(p_info)
                        # Bỏ qua file bé hơn 10KB (icon mạng xã hội, pixel) hoặc lớn hơn 15MB (treo cứng proxy)
                        if size_bytes != -1 and (size_bytes < 10_000 or size_bytes > 15_000_000):
                            continue

                        # 2. Fetch CHỈ những bytes của part đính kèm (không tải toàn bộ)
                        try:
                            s2, pd_list = mail.fetch(str(mail_no).encode(), f"(BODY.PEEK[{part_num}])")
                            if s2 != "OK" or not pd_list or not isinstance(pd_list[0], tuple):
                                continue
                                
                            part_bytes = pd_list[0][1]
                            
                            # Parse sub-message payload
                            sub_msg = message_from_bytes(b"Content-Transfer-Encoding: base64\r\n\r\n" + part_bytes)
                            # Fallback if not base64? IMAP usually encodes base64 for images
                            # We can force base64 decode if the body isn't parsed properly
                            payload = sub_msg.get_payload(decode=True)
                            if not payload:
                                import base64
                                # Cleanup IMAP wrapping
                                raw_p = part_bytes.replace(b'\r', b'').replace(b'\n', b'')
                                try:
                                    payload = base64.b64decode(raw_p)
                                except Exception:
                                    continue
                        except Exception as e:
                            print(f"[IMAP] Lỗi tải part {part_num} của mail {mail_no}: {e}")
                            continue

                        # Extract filename from parsed BODYSTRUCTURE info if available
                        orig = ""
                        # p_info is ['image', 'jpeg', ['name', 'file.jpg'], ...]
                        # Search for "name" or "filename"
                        for i in range(len(p_info)):
                            if isinstance(p_info[i], list) and len(p_info[i]) >= 2:
                                if isinstance(p_info[i][0], str) and p_info[i][0].lower() in ('name', 'filename'):
                                    orig = _decode(p_info[i][1])
                                    break
                                    
                        if not orig:
                            ext  = mime.split("/")[-1].replace("jpeg", "jpg")
                            orig = f"att_{att_i}.{ext}"
                        att_i += 1

                        fname   = _safe_name(orig)
                        dest    = raw_dir / f"mail_{mail_no:04d}_{fname}"
                        
                        dest.write_bytes(payload)
                        # Push to AI Queue
                        ai_queue.put((email_addr, str(dest), mime, user_state.user_id))
                        images_found += 1
                        manifest_rows.append({
                            "mail_no":   mail_no,
                            "date":      date,
                            "recipient": to,
                            "filename":  fname,
                            "filepath":  str(dest),
                            "size":      len(payload),
                            "mime":      mime,
                        })

                        user_state.update_account(email_addr,
                                             images_found=images_found,
                                             last_file=fname)
                        user_state.inc("images_total")
                        
                    # Cập nhật tiến độ mượt mà từng mail thay vì đợi hết batch
                    current_processed += 1
                    user_state.update_account(email_addr, processed=current_processed)

            except Exception as e:
                # Nếu batch bị lỗi (do 1 thư quá lớn gây nghẽn RAM), bỏ qua
                print(f"[IMAP] Batch fetch error for {email_addr}: {e}")

            # Chốt sổ lại số tròn trịa cuối batch
            current_processed = min(b_start + BATCH_SIZE, total)
            user_state.update_account(email_addr, processed=current_processed)

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
