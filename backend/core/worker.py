
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
from core.imap_client import new_client, authenticate_plain
from core.proxy_pool import ProxyPool, ProxyInfo, ProxyStatus
from core.state import AppState
from core.classifier import ai_queue

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
        if isinstance(parsed[0], list):
            subparts = [p for p in parsed if isinstance(p, list)]
            for i, p in enumerate(subparts):
                num = f"{prefix}.{i+1}" if prefix else str(i+1)
                parts.extend(_find_parts(p, num))
        elif isinstance(parsed[0], str):
            mime1 = parsed[0].lower()
            mime2 = parsed[1].lower() if len(parsed) > 1 and isinstance(parsed[1], str) else ""
            mime = f"{mime1}/{mime2}"
            num = prefix if prefix else "1"
            parts.append((num, mime, parsed))
    return parts

def _extract_bodystructure(header_bytes: bytes) -> str:
    s = header_bytes.decode('utf-8', errors='ignore')
    idx = s.find("BODYSTRUCTURE (")
    if idx == -1: return ""
    idx += len("BODYSTRUCTURE ")
    
    open_p = 0
    for i in range(idx, len(s)):
        if s[i] == '(': open_p += 1
        elif s[i] == ')': open_p -= 1
        if open_p == 0:
            return s[idx:i+1]
    return ""

def _get_part_size(p_info: list) -> int:
    try:
        if len(p_info) > 6:
            for item in p_info[5:10]:
                if isinstance(item, str) and item.isdigit():
                    return int(item)
    except Exception:
        pass
    return -1

def run_account(
    account:    dict,
    pool:       ProxyPool,
    stop_event: threading.Event,
    user_state: "AppState" = None,
    mode:       str = "adaptive",
):
    if user_state is None:
        from core.state import state as _global_state
        user_state = _global_state
    
    email_addr = account["email"]
    password   = account["password"]
    thread_name = threading.current_thread().name

    proxy: Optional[ProxyInfo] = pool.acquire(email_addr)
    proxy_id = proxy.id if proxy else "direct"

    user_state.update_account(email_addr,
                         status="running",
                         proxy=proxy_id,
                         thread=thread_name)

    slug    = re.sub(r'[^\w]', '_', email_addr.split("@")[0])
    raw_dir = OUTPUT_DIR / slug / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    mail = None
    MAX_PROXY_ROTATIONS = 3
    proxy_switches = 0

    try:

        attempt = 0
        while attempt < RETRY_MAX:
            if stop_event.is_set() or user_state.accounts.get(email_addr, {}).get("status") == "stopped":
                user_state.update_account(email_addr, status="stopped", error="Đã dừng phiên quét")
                return

            try:
                proxy_label = proxy.id if proxy else "direct"
                mail = new_client(proxy=proxy, mode="ssl")
                typ, data = mail.login(email_addr, password)
                break

            except imaplib.IMAP4.error as e:
                err = str(e)
                if "policy" in err.lower() and "ko" in err.lower():
                    print(f"[POLICY-KO] {email_addr} | proxy={proxy_label} → web fallback", flush=True)
                    from core.config import CAPTCHA_API_KEY
                    if CAPTCHA_API_KEY:
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
                        except Exception as we:
                            print(f"[WEB-FALLBACK] {email_addr} | ✗ {we}", flush=True)
                            user_state.update_account(email_addr, status="failed", error=f"Web: {we}")
                        finally:
                            if proxy:
                                pool.release(proxy)
                    else:
                        print(f"[POLICY-KO] {email_addr} | No CAPTCHA_API_KEY → skip", flush=True)
                        user_state.update_account(email_addr, status="failed", error="Policy KO, no captcha key")
                        if proxy:
                            pool.release(proxy)
                    return
                else:
                    _handle_auth_error(proxy, pool, err)
                    user_state.update_account(email_addr, status="failed", error=err)
                    return

            except Exception as e:
                err = str(e)
                err_type = type(e).__name__
                proxy_label = proxy.id if proxy else "direct"

                is_proxy_error = proxy and any(k in err.lower() for k in [
                    "proxy rejected", "502", "407", "bad gateway",
                    "connection refused", "connect tunnel",
                ])

                if is_proxy_error and proxy_switches < MAX_PROXY_ROTATIONS:
                    old_id = proxy.id
                    pool.mark_dead(proxy, err)
                    pool.release(proxy)
                    proxy_switches += 1
                    proxy = pool.acquire(email_addr)
                    new_id = proxy.id if proxy else "direct"
                    print(f"[PROXY] {email_addr}: {old_id} → {new_id} ({proxy_switches}/{MAX_PROXY_ROTATIONS})", flush=True)
                    user_state.update_account(email_addr, proxy=new_id)
                    time.sleep(0.5)
                    continue
                elif is_proxy_error and proxy_switches >= MAX_PROXY_ROTATIONS:
                    print(f"[PROXY-EXHAUSTED] {email_addr} | {proxy_switches} proxies failed → web fallback", flush=True)
                    from core.config import CAPTCHA_API_KEY
                    if CAPTCHA_API_KEY:
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
                        except Exception as we:
                            print(f"[WEB-FALLBACK] {email_addr} | ✗ {we}", flush=True)
                            user_state.update_account(email_addr, status="failed", error=f"Web: {we}")
                        finally:
                            if proxy:
                                pool.release(proxy)
                    else:
                        user_state.update_account(email_addr, status="failed", error="Proxy exhausted, no captcha key")
                        if proxy:
                            pool.release(proxy)
                    return
                else:
                    attempt += 1
                    if attempt >= RETRY_MAX:
                        _handle_conn_error(proxy, pool, err)
                        user_state.update_account(email_addr, status="failed", error=err)
                        return
                    time.sleep(2 ** attempt)
                    continue

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

        for b_start in range(0, total, BATCH_SIZE):
            if stop_event.is_set() or user_state.accounts.get(email_addr, {}).get("status") == "stopped":
                user_state.update_account(email_addr, status="stopped", error="Đã dừng phiên quét")
                return

            # CƠ CHẾ THROTTLE: Ép tải nghỉ ngơi để chừa sức đoạt giấy tờ sớm
            while mode == "adaptive" and ai_queue.qsize() > 20:
                if user_state.accounts.get(email_addr, {}).get("document_found"):
                    break
                if stop_event.is_set():
                    return
                time.sleep(1.0)
                
            # CƠ CHẾ CẮT ĐỨT: Phát hiện giấy tờ là quit hàm liền
            if mode == "adaptive" and user_state.accounts.get(email_addr, {}).get("document_found"):
                user_state.update_account(email_addr, status="found_doc", error="✅ Đã tìm thấy giấy tờ")
                print(f"[SMART-ADAPTIVE] {email_addr} Đã tìm thấy bài, ngưng tải batch để chừa băng thông!", flush=True)
                return

            batch = all_ids[b_start: b_start + BATCH_SIZE]
            batch_str = b','.join(batch)
            
            try:
                t_fetch = time.time()
                status, msg_data_list = mail.fetch(batch_str, "(BODYSTRUCTURE BODY.PEEK[HEADER.FIELDS (DATE TO)])")
                t_elapsed = time.time() - t_fetch
                
                if status != "OK" or not msg_data_list:
                    current_processed = min(b_start + BATCH_SIZE, total)
                    user_state.update_account(email_addr, processed=current_processed)
                    continue
                    
                print(f"[IMAP-PERF] {email_addr.split('@')[0]}: Tải BODYSTRUCTURE {len(batch)} email tốn {t_elapsed:.2f}s", flush=True)

                fetch_plan = {}
                meta_info = {}

                # Giai đoạn 1: Duyệt qua cấu trúc headers để nhóm các email có chứa part giống nhau
                for item in msg_data_list:
                    if not isinstance(item, tuple):
                        continue

                    header_b = item[0]
                    msg_body = item[1]
                    
                    try:
                        mail_no_bytes = header_b.split(b' ', 1)[0]
                        mail_no = int(mail_no_bytes)
                    except ValueError:
                        continue

                    msg  = message_from_bytes(msg_body)
                    date = msg.get("Date", "")
                    to   = _decode(msg.get("To", ""))

                    bs_str = _extract_bodystructure(header_b)
                    if not bs_str:
                        continue
                        
                    parsed_bs = _parse_imap_list(bs_str)
                    parts_info = _find_parts(parsed_bs)
                    
                    target_parts = [p for p in parts_info if p[1] in ALLOWED_MIME]
                    
                    att_i = 0
                    for part_num, mime, p_info in target_parts:
                        size_bytes = _get_part_size(p_info)
                        if size_bytes != -1 and (size_bytes < 10_000 or size_bytes > 15_000_000):
                            continue

                        orig = ""
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
                        # Lấy encoding trực tiếp từ BODYSTRUCTURE, rất quan trọng để parse file đúng định dạng
                        encoding = p_info[5] if len(p_info) > 5 and isinstance(p_info[5], str) else "base64"
                        
                        # Thêm vào plan tải xuống gom nhóm
                        fetch_plan.setdefault(part_num, []).append(mail_no_bytes)
                        meta_info[(mail_no, part_num)] = {
                            "date": date,
                            "to": to,
                            "mime": mime,
                            "fname": fname,
                            "encoding": encoding.lower()
                        }

                # Giai đoạn 2: Tải gom nhóm hàng loạt tất cả ảnh cùng một part_num
                for part_num, mail_nos in fetch_plan.items():
                    for i in range(0, len(mail_nos), 50):
                        if mode == "adaptive" and user_state.accounts.get(email_addr, {}).get("document_found"):
                            print(f"[SMART-ADAPTIVE] {email_addr} Phát hiện ra giấy tờ sớm, STOP FETCH part_num={part_num}", flush=True)
                            break
                            
                        chunk_nos = mail_nos[i:i+50]
                        batch_ids = b','.join(chunk_nos)
                        
                        try:
                            t_part = time.time()
                            # Thực thi lệnh tải HÀNG LOẠT cho N email trong một lệnh fetch duy nhất
                            s2, pd_list = mail.fetch(batch_ids, f"(BODY.PEEK[{part_num}])")
                            if s2 != "OK" or not pd_list:
                                continue
                            fetch_time = time.time() - t_part
                            print(f"[IMAP-PERF] {email_addr.split('@')[0]}: Tải {len(chunk_nos)} ảnh (mã part {part_num}) tốn {fetch_time:.2f}s", flush=True)
                            
                            if fetch_time > 20.0 and (fetch_time / max(1, len(chunk_nos))) > 4.0:
                                raise TimeoutError(f"SLOW_TIMEOUT: IMAP rùa bò ({fetch_time:.1f}s cho {len(chunk_nos)} file), ép Fallback Web-API!")

                            for pd_item in pd_list:
                                if not isinstance(pd_item, tuple):
                                    continue
                                
                                p_header = pd_item[0]
                                part_bytes = pd_item[1]
                                
                                try:
                                    p_mail_no = int(p_header.split(b' ', 1)[0])
                                except ValueError:
                                    continue
                                
                                meta = meta_info.get((p_mail_no, part_num))
                                if not meta:
                                    continue
                                
                                enc = meta["encoding"]
                                if enc in ("base64", "quoted-printable", "7bit", "8bit"):
                                    sub_msg = message_from_bytes(f"Content-Transfer-Encoding: {enc}\r\n\r\n".encode() + part_bytes)
                                    payload = sub_msg.get_payload(decode=True)
                                else:
                                    sub_msg = message_from_bytes(b"Content-Transfer-Encoding: base64\r\n\r\n" + part_bytes)
                                    payload = sub_msg.get_payload(decode=True)
                                
                                if not payload:
                                    import base64
                                    raw_p = part_bytes.replace(b'\r', b'').replace(b'\n', b'')
                                    try:
                                        payload = base64.b64decode(raw_p)
                                    except Exception:
                                        continue

                                dest = raw_dir / f"mail_{p_mail_no:04d}_{meta['fname']}"
                                dest.write_bytes(payload)
                                ai_queue.put((email_addr, str(dest), meta['mime'], user_state.user_id))
                                
                                images_found += 1
                                manifest_rows.append({
                                    "mail_no":   p_mail_no,
                                    "date":      meta['date'],
                                    "recipient": meta['to'],
                                    "filename":  meta['fname'],
                                    "filepath":  str(dest),
                                    "size":      len(payload),
                                    "mime":      meta['mime'],
                                })

                                user_state.update_account(email_addr,
                                                     images_found=images_found,
                                                     last_file=meta['fname'])
                                user_state.inc("images_total")
                        except Exception as e:
                            print(f"[IMAP] Lỗi tải gom nhóm part {part_num}: {e}", flush=True)
                            err_str = str(e).lower()
                            if any(k in err_str for k in ["timeout", "eof", "reset", "broken", "abort", "closed", "ssl", "connection"]):
                                raise e # Fatal, ngắt kết nối
                                
            except Exception as e:
                print(f"[IMAP] Batch fetch error for {email_addr}: {e}", flush=True)
                raise # Bubble up to mark account as failed or switch to web

            current_processed = min(b_start + BATCH_SIZE, total)
            user_state.update_account(email_addr, processed=current_processed)

        _write_manifest(raw_dir.parent / "manifest.csv", manifest_rows)

        acc_info = user_state.accounts.get(email_addr, {})
        if not acc_info.get("document_found"):
            user_state.update_account(email_addr, status="done")
        user_state.inc("accounts_done")

    except Exception as e:
        err_str = str(e).lower()
        print(f"[IMAP-ABORT] {email_addr} crashed: {e}", flush=True)
        if any(k in err_str for k in ["timeout", "eof", "reset", "broken", "abort", "closed", "ssl", "connection", "rate"]):
            print(f"[WEB-FALLBACK] {email_addr} IMAP stream die, testing Web...", flush=True)
            from core.config import CAPTCHA_API_KEY
            if CAPTCHA_API_KEY:
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
                    return # Exit worker peacefully
                except Exception as we:
                    user_state.update_account(email_addr, status="failed", error=f"Web Fallback: {we}")
            else:
                user_state.update_account(email_addr, status="failed", error=f"IMAP Network Error: {e}")
        else:
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
