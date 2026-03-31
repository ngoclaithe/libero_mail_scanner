import os
import multiprocessing as mp
from pathlib import Path

# ── IMAP BASE ────────────────────────────────────────────────
IMAP_HOST    = "imapmail.libero.it"
IMAP_PORT    = 993
SENT_FOLDER  = "outbox"
IMAP_TIMEOUT = 30
RETRY_MAX    = 3

# ── AUTO TUNING HỆ THỐNG ─────────────────────────────────────
def get_system_specs():
    specs = {
        "cpu_count": mp.cpu_count(),
        "ram_gb": 8.0,
        "vram_gb": 0.0,
        "has_gpu": False
    }
    
    # Cố gắng đọc RAM thật nếu đang mở trên Linux
    try:
        if os.name == 'posix':
            total_bytes = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
            specs["ram_gb"] = total_bytes / (1024**3)
    except Exception:
        pass
        
    # Cố gắng đọc Card màn hình (VRAM)
    try:
        smi = os.popen('nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null').read().strip()
        if smi:
            first_vram_mb = int(smi.split('\n')[0])
            specs["vram_gb"] = first_vram_mb / 1024.0
            specs["has_gpu"] = True
    except Exception:
        pass
        
    return specs

sys_specs = get_system_specs()

if sys_specs["has_gpu"]:
    # ── MÁY CHỦ GPU KHỦNG ──
    USE_CUDA = True
    BATCH_SIZE = 100
    # Chia VRAM cho Model (tính trung bình OCR+Face tốn 3.5GB VRAM cho 1 worker trên thực tế)
    # Tuyệt đối không để quá 6 workers trên card 24GB tránh tràn Swap qua RAM
    AI_WORKERS = min(int(sys_specs["vram_gb"] / 3.5), 6)
    # CPU khỏe => Tăng cực đại số proxy kéo mạng IMAP song song (1 core gánh 5 threads nhẹ nhàng)
    MAX_WORKERS = min(sys_specs["cpu_count"] * 5, 200)
else:
    # ── VPS CPU CŨ (RAM/CPU Thấp) ──
    USE_CUDA = False
    BATCH_SIZE = 50
    # CPU thường chỉ chạy 2-3 workers AI để không đứng máy
    AI_WORKERS = min(sys_specs["cpu_count"] - 1, 3) 
    # Băng thông tải chậm cho an toàn
    MAX_WORKERS = min(sys_specs["cpu_count"] * 10, 40)

if AI_WORKERS < 1: AI_WORKERS = 1

print(f"[AUTO-TUNE] Phần cứng: {sys_specs['cpu_count']} Cores, RAM={sys_specs['ram_gb']:.1f}GB, VRAM={sys_specs['vram_gb']:.1f}GB")
print(f"[AUTO-TUNE] Cấu hình: AI_WORKERS={AI_WORKERS} (GPU={USE_CUDA}), IMAP_WORKERS={MAX_WORKERS}, BATCH={BATCH_SIZE}", flush=True)

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
