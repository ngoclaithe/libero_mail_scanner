import sqlite3
import os

DB_PATH = os.getenv("DB_PATH", "data.db")

def _hash_password(password: str) -> str:
    import hashlib
    import secrets
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 260000)
    return f"pbkdf2:sha256:260000${salt}${h.hex()}"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        role TEXT,
        credits INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ip TEXT,
        endpoint TEXT,
        user_id INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key_name TEXT PRIMARY KEY,
        value TEXT
    )''')

    c.execute("SELECT id FROM users WHERE username='admin'")
    if not c.fetchone():
        c.execute("INSERT INTO users (username, password, role, credits) VALUES (?, ?, ?, ?)",
                  ('admin', _hash_password('admin123'), 'admin', 999999))

    c.execute("SELECT id FROM users WHERE username='user'")
    if not c.fetchone():
        c.execute("INSERT INTO users (username, password, role, credits) VALUES (?, ?, ?, ?)",
                  ('user', _hash_password('user123'), 'user', 0))

    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn
