from pathlib import Path

# ── IMAP ─────────────────────────────────────────────────────
IMAP_HOST    = "imapmail.libero.it"
IMAP_PORT    = 993
SENT_FOLDER  = "outbox"
BATCH_SIZE   = 50
RETRY_MAX    = 3
IMAP_TIMEOUT = 30

# ── Threading ────────────────────────────────────────────────
MAX_WORKERS  = 40

# ── Files ────────────────────────────────────────────────────
PROXY_FILE    = "SAR97653.txt"
ACCOUNTS_FILE = "accounts.csv"
OUTPUT_DIR    = Path("attachments")

# ── MIME types to download ────────────────────────────────────
ALLOWED_MIME = frozenset({
    "image/jpeg", "image/jpg", "image/png",
    "image/gif",  "image/bmp", "image/tiff",
    "image/webp", "image/heic",
    "application/pdf",
})
