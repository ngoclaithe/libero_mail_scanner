import os
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from database import get_db

SECRET_KEY = os.getenv("SECRET_KEY", "super_secret_libero_jwt_key_2024")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

class TokenData(BaseModel):
    user_id: int
    username: str
    role: str
    credits: int

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 260000)
    return f"pbkdf2:sha256:260000${salt}${h.hex()}"

def verify_password(plain: str, hashed: str) -> bool:
    try:
        parts = hashed.split("$")
        if len(parts) == 3:
            salt = parts[1]
            stored_hash = parts[2]
            h = hashlib.pbkdf2_hmac('sha256', plain.encode(), salt.encode(), 260000)
            return h.hex() == stored_hash
        else:
            from hashlib import sha256
            return False
    except Exception:
        return False

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme)) -> TokenData:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token không hợp lệ hoặc đã hết hạn",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("user_id")
        username: str = payload.get("username")
        role: str = payload.get("role")
        if user_id is None or username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    db = get_db()
    user = db.execute("SELECT credits FROM users WHERE id=?", (user_id,)).fetchone()
    db.close()
    if user is None:
        raise credentials_exception

    return TokenData(user_id=user_id, username=username, role=role, credits=user["credits"])

def require_admin(current_user: TokenData = Depends(get_current_user)) -> TokenData:
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin Access Required")
    return current_user
