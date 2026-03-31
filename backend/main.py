"""
Libero Mail Scanner — FastAPI + Uvicorn Backend
Replaces the previous Flask monolith.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List

from database import init_db, get_db
from auth import (
    TokenData, TokenResponse,
    verify_password, hash_password, create_access_token,
    get_current_user, require_admin,
)
from core.scanner import scanner_manager
from core.config import OUTPUT_DIR

# ── Init ──────────────────────────────────────────────────────

init_db()

app = FastAPI(title="Libero Mail Scanner API", version="2.0.0")

# CORS
origins = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Request Logging Middleware ─────────────────────────────────

@app.middleware("http")
async def log_requests(request: Request, call_next):
    # Skip logging for static/media paths
    if not request.url.path.startswith(("/media", "/docs", "/openapi")):
        try:
            # Try to get user from token (non-blocking)
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


# ── Pydantic Models ───────────────────────────────────────────

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
    action: str  # "add" or "set"


# ── Auth Routes ───────────────────────────────────────────────

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


# ── Scanner API ───────────────────────────────────────────────

@app.get("/api/state")
async def api_state(current_user: TokenData = Depends(get_current_user)):
    sc = scanner_manager.get_scanner(current_user.user_id)
    return sc.get_state()


@app.post("/api/start")
async def api_start(current_user: TokenData = Depends(get_current_user)):
    sc = scanner_manager.get_scanner(current_user.user_id)
    ok, msg = sc.start()
    return {"ok": ok, "msg": msg}


@app.post("/api/stop")
async def api_stop(current_user: TokenData = Depends(get_current_user)):
    sc = scanner_manager.get_scanner(current_user.user_id)
    sc.stop()
    return {"ok": True, "msg": "Stop signal sent"}


@app.post("/api/upload-accounts")
async def api_upload(
    file: UploadFile = File(...),
    current_user: TokenData = Depends(get_current_user),
):
    if not file.filename:
        raise HTTPException(400, "Empty filename")
    if not file.filename.lower().endswith((".csv", ".txt")):
        raise HTTPException(400, "Only .csv or .txt")

    # ── Save per-user accounts file ──
    save_path = f"accounts_{current_user.user_id}.csv"
    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    sc = scanner_manager.get_scanner(current_user.user_id)
    sc.set_accounts_file(save_path)
    preview = sc.accounts_preview()
    count = len(preview)

    # ── Check credits ──
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


@app.post("/api/upload-proxies")
async def api_upload_proxies(
    file: UploadFile = File(...),
    current_user: TokenData = Depends(get_current_user),
):
    if not file.filename:
        raise HTTPException(400, "Empty filename")
    if not file.filename.lower().endswith(".txt"):
        raise HTTPException(400, "Only .txt allowed for proxies")

    save_path = f"proxies_{current_user.user_id}.txt"
    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    sc = scanner_manager.get_scanner(current_user.user_id)
    sc.set_proxy_file(save_path)

    return {
        "ok": True,
        "msg": f"Đã upload danh sách proxy thành công",
        "count": len(sc.pool) if sc.pool else 0
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
    files: List[str]  # e.g. "email@x.com/documents/abc.jpg"


@app.post("/api/gallery/delete")
async def api_gallery_delete(req: GalleryBulkRequest, current_user: TokenData = Depends(get_current_user)):
    deleted = 0
    base_dir = OUTPUT_DIR.resolve()
    for file_path in req.files:
        try:
            full_path = (OUTPUT_DIR / file_path).resolve()
            # Security: Prevent path traversal
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
                    # Archive as slug/filename to avoid collision
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


# ── Media serving ─────────────────────────────────────────────

@app.get("/media/{filepath:path}")
async def serve_media(filepath: str, token: str = ""):
    """Serve media files. Token is passed via query param since <img> tags can't send headers."""
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


# ── Admin Routes ──────────────────────────────────────────────

@app.get("/api/admin/users")
async def admin_users(admin: TokenData = Depends(require_admin)):
    db = get_db()
    users = db.execute("SELECT id, username, role, credits FROM users").fetchall()
    db.close()
    return [dict(u) for u in users]


@app.get("/api/admin/logs")
async def admin_logs(admin: TokenData = Depends(require_admin)):
    db = get_db()
    logs = db.execute("""
        SELECT logs.id, logs.ip, logs.endpoint, logs.created_at, users.username 
        FROM logs LEFT JOIN users ON logs.user_id = users.id 
        ORDER BY logs.created_at DESC LIMIT 100
    """).fetchall()
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


# ── Startup ───────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=True,
    )
