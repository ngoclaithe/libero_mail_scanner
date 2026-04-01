
import os
import sys

os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional

from database import init_db, get_db
from auth import (
    TokenData, TokenResponse,
    verify_password, hash_password, create_access_token,
    get_current_user, require_admin,
)
from core.scanner import scanner_manager
from core.config import OUTPUT_DIR

init_db()

app = FastAPI(title="Libero Mail Scanner API", version="2.0.0")

origins = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    if not request.url.path.startswith(("/media", "/docs", "/openapi")):
        try:
            user_id = None
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                from jose import jwt as jose_jwt
                try:
                    payload = jose_jwt.decode(
                        auth[7:],
                        os.getenv("SECRET_KEY", ""),
                        algorithms=["HS256"]
                    )
                    user_id = payload.get("user_id")
                except Exception:
                    pass
            
            ip = request.client.host if request.client else "unknown"
            db = get_db()
            db.execute(
                "INSERT INTO logs (ip, endpoint, user_id) VALUES (?, ?, ?)",
                (ip, request.url.path, user_id)
            )
            db.commit()
            db.close()
        except Exception as e:
            print("Log error:", e)

    response = await call_next(request)
    return response

class LoginRequest(BaseModel):
    username: str
    password: str

class CreateUserRequest(BaseModel):
    username: str
    password: str
    credits: int = 0

class UpdateCreditsRequest(BaseModel):
    user_id: int
    amount: int
    action: str

@app.post("/api/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username=?", (req.username,)).fetchone()
    db.close()

    if not user or not verify_password(req.password, user["password"]):
        raise HTTPException(status_code=401, detail="Tên đăng nhập hoặc mật khẩu không đúng")

    token = create_access_token({
        "user_id": user["id"],
        "username": user["username"],
        "role": user["role"],
    })

    return TokenResponse(
        access_token=token,
        user={
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
            "credits": user["credits"],
        }
    )

@app.get("/api/auth/me")
async def get_me(current_user: TokenData = Depends(get_current_user)):
    return {
        "id": current_user.user_id,
        "username": current_user.username,
        "role": current_user.role,
        "credits": current_user.credits,
    }

SHARED_SCANNER_ID = 1

@app.get("/api/state")
async def api_state(current_user: TokenData = Depends(get_current_user)):
    sc = scanner_manager.get_scanner(SHARED_SCANNER_ID)
    return sc.get_state()

@app.post("/api/start")
async def api_start(current_user: TokenData = Depends(get_current_user)):
    sc = scanner_manager.get_scanner(SHARED_SCANNER_ID)
    ok, msg = sc.start()
    return {"ok": ok, "msg": msg}

@app.post("/api/stop")
async def api_stop(current_user: TokenData = Depends(get_current_user)):
    from core.scanner import scanner_manager
    scanner_manager.stop_scanner(current_user.id)
    return {"ok": True, "msg": "■ Lệnh dừng đã được gửi! Chờ luồng xử lý..."}

class StopEmailRequest(BaseModel):
    email: str

@app.post("/api/stop-email")
async def api_stop_email(req: StopEmailRequest, current_user: TokenData = Depends(get_current_user)):
    from core.scanner import scanner_manager
    scanner = scanner_manager.get_scanner(current_user.id)
    if not scanner:
        return {"ok": False, "msg": "Không có phiên quét nào đang chạy."}
    scanner.state.update_account(req.email, status="stopped", error="Đã dừng theo yêu cầu người dùng")
    return {"ok": True, "msg": f"■ Lệnh dừng đã gửi cho {req.email}"}

@app.post("/api/upload-accounts")
async def api_upload(
    file: UploadFile = File(...),
    current_user: TokenData = Depends(get_current_user),
):
    if not file.filename:
        raise HTTPException(400, "Empty filename")
    if not file.filename.lower().endswith((".csv", ".txt")):
        raise HTTPException(400, "Only .csv or .txt")

    save_path = f"accounts_{SHARED_SCANNER_ID}.csv"
    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    sc = scanner_manager.get_scanner(SHARED_SCANNER_ID)
    sc.set_accounts_file(save_path)
    preview = sc.accounts_preview()
    count = len(preview)

    db = get_db()
    user = db.execute(
        "SELECT credits, role FROM users WHERE id=?",
        (current_user.user_id,)
    ).fetchone()

    if user["role"] != "admin":
        if user["credits"] < count:
            db.close()
            raise HTTPException(
                403,
                f"Không đủ Credit! Cần {count} credits cho {count} mail (Hiện có {user['credits']})."
            )
        db.execute(
            "UPDATE users SET credits = credits - ? WHERE id=?",
            (count, current_user.user_id)
        )
        db.commit()

    db.close()

    return {
        "ok": True,
        "msg": f"Đã duyệt {count} accounts. Trừ {count if user['role'] != 'admin' else 0} credits.",
        "count": count,
        "preview": preview[:5],
    }

class AccountItem(BaseModel):
    email: str
    password: str

class SaveAccountsRequest(BaseModel):
    accounts: List[AccountItem]

@app.get("/api/accounts")
async def api_get_accounts(current_user: TokenData = Depends(get_current_user)):
    sc = scanner_manager.get_scanner(SHARED_SCANNER_ID)
    accounts = sc._load_accounts()
    return {"accounts": accounts}

@app.post("/api/accounts/save")
async def api_save_accounts(
    req: SaveAccountsRequest,
    current_user: TokenData = Depends(get_current_user),
):
    count = len(req.accounts)
    if count == 0:
        raise HTTPException(400, "Danh sách rỗng")

    db = get_db()
    user = db.execute(
        "SELECT credits, role FROM users WHERE id=?",
        (current_user.user_id,)
    ).fetchone()

    if user["role"] != "admin":
        if user["credits"] < count:
            db.close()
            raise HTTPException(
                403,
                f"Không đủ Credit! Cần {count} credits cho {count} mail (Hiện có {user['credits']})."
            )
        db.execute(
            "UPDATE users SET credits = credits - ? WHERE id=?",
            (count, current_user.user_id)
        )
        db.commit()
    db.close()

    save_path = f"accounts_{SHARED_SCANNER_ID}.csv"
    with open(save_path, "w", encoding="utf-8") as f:
        for acc in req.accounts:
            f.write(f"{acc.email}:{acc.password}\n")

    sc = scanner_manager.get_scanner(SHARED_SCANNER_ID)
    sc.set_accounts_file(save_path)

    return {
        "ok": True,
        "msg": f"Đã lưu {count} accounts. Trừ {count if user['role'] != 'admin' else 0} credits.",
        "count": count,
    }

@app.post("/api/upload-proxies")
async def api_upload_proxies(
    file: UploadFile = File(...),
    current_user: TokenData = Depends(get_current_user),
):
    if not file.filename:
        raise HTTPException(400, "Empty filename")
    if not file.filename.lower().endswith(".txt"):
        raise HTTPException(400, "Only .txt allowed for proxies")

    save_path = f"proxies_{SHARED_SCANNER_ID}.txt"
    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    sc = scanner_manager.get_scanner(SHARED_SCANNER_ID)
    sc.set_proxy_file(save_path)

    return {
        "ok": True,
        "msg": f"Đã upload danh sách proxy thành công",
        "count": len(sc.pool) if sc.pool else 0
    }

class ProxyItem(BaseModel):
    host: str
    port: int
    username: str
    password: str

class SaveProxiesRequest(BaseModel):
    proxies: List[ProxyItem]

@app.get("/api/proxies")
async def api_get_proxies(current_user: TokenData = Depends(get_current_user)):
    sc = scanner_manager.get_scanner(SHARED_SCANNER_ID)
    proxy_file = sc._proxy_file
    proxies = []
    try:
        with open(proxy_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(":")
                if len(parts) >= 4:
                    proxies.append({
                        "host": parts[0],
                        "port": int(parts[1]),
                        "username": parts[2],
                        "password": parts[3],
                    })
    except FileNotFoundError:
        pass
    return {"proxies": proxies}

@app.post("/api/proxies/save")
async def api_save_proxies(
    req: SaveProxiesRequest,
    current_user: TokenData = Depends(get_current_user),
):
    count = len(req.proxies)
    if count == 0:
        raise HTTPException(400, "Danh sách rỗng")

    save_path = f"proxies_{SHARED_SCANNER_ID}.txt"
    with open(save_path, "w", encoding="utf-8") as f:
        for p in req.proxies:
            f.write(f"{p.host}:{p.port}:{p.username}:{p.password}\n")

    sc = scanner_manager.get_scanner(SHARED_SCANNER_ID)
    sc.set_proxy_file(save_path)

    return {
        "ok": True,
        "msg": f"Đã lưu {count} proxies thành công",
        "count": count,
    }

@app.on_event("startup")
async def startup_event():
    db = get_db()
    row = db.execute("SELECT value FROM settings WHERE key_name='CAPTCHA_API_KEY'").fetchone()
    db.close()
    if row and row["value"]:
        import core.config as cfg
        cfg.CAPTCHA_API_KEY = row["value"]

class CaptchaKeyRequest(BaseModel):
    api_key: str

@app.get("/api/captcha-key")
async def api_get_captcha_key(current_user: TokenData = Depends(get_current_user)):
    db = get_db()
    row = db.execute("SELECT value FROM settings WHERE key_name='CAPTCHA_API_KEY'").fetchone()
    db.close()
    key = row["value"] if row else ""
    return {
        "configured": bool(key),
        "key_preview": key[:8] + "..." if key and len(key) > 8 else "",
    }

@app.post("/api/captcha-key")
async def api_set_captcha_key(
    req: CaptchaKeyRequest,
    current_user: TokenData = Depends(get_current_user),
):
    key = req.api_key.strip()
    if not key:
        raise HTTPException(400, "API key rỗng")

    try:
        from core.captcha_solver import check_balance
        balance = check_balance(key)
    except Exception as e:
        raise HTTPException(400, f"API key không hợp lệ: {e}")

    import core.config as cfg
    cfg.CAPTCHA_API_KEY = key

    db = get_db()
    db.execute("REPLACE INTO settings (key_name, value) VALUES (?, ?)", ("CAPTCHA_API_KEY", key))
    db.commit()
    db.close()

    return {
        "ok": True,
        "msg": f"✓ Capsolver API key saved! Balance: ${balance:.2f}",
        "balance": balance,
    }

@app.get("/api/gallery")
async def api_gallery(current_user: TokenData = Depends(get_current_user)):
    res = {}
    if not OUTPUT_DIR.exists():
        return res
    for slug in os.listdir(OUTPUT_DIR):
        user_path = OUTPUT_DIR / slug
        if not user_path.is_dir():
            continue

        raw_dir = user_path / "raw"
        raw_files = [
            f for f in os.listdir(raw_dir)
            if f.lower().endswith(('.jpg', '.jpeg', '.png', '.pdf'))
        ] if raw_dir.exists() else []

        doc_dir = user_path / "documents"
        doc_files = [
            f for f in os.listdir(doc_dir)
            if f.lower().endswith(('.jpg', '.jpeg', '.png', '.pdf'))
        ] if doc_dir.exists() else []

        if raw_files or doc_files:
            res[slug] = {"raw": raw_files, "documents": doc_files}
    return res

class GalleryBulkRequest(BaseModel):
    files: List[str]

@app.post("/api/gallery/delete")
async def api_gallery_delete(req: GalleryBulkRequest, current_user: TokenData = Depends(get_current_user)):
    deleted = 0
    base_dir = OUTPUT_DIR.resolve()
    for file_path in req.files:
        try:
            full_path = (OUTPUT_DIR / file_path).resolve()
            if not str(full_path).startswith(str(base_dir)):
                continue
            if full_path.exists() and full_path.is_file():
                os.remove(full_path)
                deleted += 1
        except Exception:
            pass
    return {"ok": True, "msg": f"Đã xóa thành công {deleted} ảnh"}
    
@app.post("/api/gallery/clear-all")
async def api_gallery_clear_all(current_user: TokenData = Depends(get_current_user)):
    import shutil
    try:
        if OUTPUT_DIR.exists():
            for item in OUTPUT_DIR.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
        return {"ok": True, "msg": "Đã dọn sạch toàn bộ thư viện ảnh!"}
    except Exception as e:
        return {"ok": False, "msg": f"Lỗi xóa: {e}"}

@app.post("/api/gallery/download")
async def api_gallery_download(req: GalleryBulkRequest, current_user: TokenData = Depends(get_current_user)):
    import zipfile
    import io
    from fastapi.responses import StreamingResponse

    zip_buffer = io.BytesIO()
    base_dir = OUTPUT_DIR.resolve()
    
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        for file_path in req.files:
            try:
                full_path = (OUTPUT_DIR / file_path).resolve()
                if not str(full_path).startswith(str(base_dir)):
                    continue
                if full_path.exists() and full_path.is_file():
                    parts = Path(file_path).parts
                    arcname = f"{parts[0]}_{parts[-1]}" if len(parts) > 1 else full_path.name
                    zipf.write(full_path, arcname)
            except Exception:
                pass

    zip_buffer.seek(0)
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=libero_images.zip"}
    )

@app.get("/media/{filepath:path}")
async def serve_media(filepath: str, token: str = ""):
    if not token:
        raise HTTPException(401, "Missing token")
    try:
        from jose import jwt as jose_jwt
        from jose import JWTError
        payload = jose_jwt.decode(token, os.getenv("SECRET_KEY", "super_secret_libero_jwt_key_2024"), algorithms=["HS256"])
        if not payload.get("user_id"):
            raise HTTPException(401, "Invalid token")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "Invalid or expired token")

    full_path = OUTPUT_DIR / filepath
    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(full_path)

@app.get("/api/admin/users")
async def admin_users(admin: TokenData = Depends(require_admin)):
    db = get_db()
    users = db.execute("SELECT id, username, role, credits FROM users").fetchall()
    db.close()
    return [dict(u) for u in users]

@app.get("/api/admin/logs")
async def admin_logs(admin: TokenData = Depends(require_admin)):
    db = get_db()
    logs = db.execute().fetchall()
    db.close()
    return [dict(l) for l in logs]

@app.post("/api/admin/create_user")
async def admin_create_user(
    req: CreateUserRequest,
    admin: TokenData = Depends(require_admin),
):
    try:
        db = get_db()
        db.execute(
            "INSERT INTO users (username, password, role, credits) VALUES (?, ?, ?, ?)",
            (req.username, hash_password(req.password), "user", req.credits)
        )
        db.commit()
        db.close()
        return {"ok": True, "msg": f"Tạo tài khoản '{req.username}' thành công!"}
    except Exception:
        raise HTTPException(400, "Lỗi: Tên đăng nhập này có thể đã tồn tại.")

@app.post("/api/admin/update_credits")
async def admin_update_credits(
    req: UpdateCreditsRequest,
    admin: TokenData = Depends(require_admin),
):
    db = get_db()
    if req.action == "add":
        db.execute("UPDATE users SET credits = credits + ? WHERE id = ?", (req.amount, req.user_id))
    elif req.action == "set":
        db.execute("UPDATE users SET credits = ? WHERE id = ?", (req.amount, req.user_id))
    db.commit()
    db.close()
    return {"ok": True, "msg": "Cập nhật credits thành công"}

@app.get("/api/data_backup.tar.gz")
async def get_data_backup():
    file_path = "data_backup.tar.gz"
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File data_backup.tar.gz không tồn tại. Hãy chạy lệnh tar trước!")
    return FileResponse(file_path, filename="data_backup.tar.gz", media_type="application/gzip")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=True,
    )
