import os
import multiprocessing as mp
from pathlib import Path

CAPTCHA_API_KEY = os.environ.get("CAPTCHA_API_KEY", "")

def get_system_specs():
    specs = {
        "cpu_count": mp.cpu_count(),
        "ram_gb": 8.0,
        "vram_gb": 0.0,
        "has_gpu": False
    }
    
    try:
        if os.name == 'posix':
            total_bytes = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
            specs["ram_gb"] = total_bytes / (1024**3)
    except Exception:
        pass
        
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
    USE_CUDA = True
    AI_WORKERS = min(int(sys_specs["vram_gb"] / 2.0), 12)
    MAX_WORKERS = min(sys_specs["cpu_count"] * 10, 200)
else:
    USE_CUDA = False
    AI_WORKERS = min(sys_specs["cpu_count"] - 1, 3) 
    MAX_WORKERS = min(sys_specs["cpu_count"] * 10, 40)

if AI_WORKERS < 1: AI_WORKERS = 1

print(f"[AUTO-TUNE] Phần cứng: {sys_specs['cpu_count']} Cores, RAM={sys_specs['ram_gb']:.1f}GB, VRAM={sys_specs['vram_gb']:.1f}GB")
print(f"[AUTO-TUNE] Cấu hình: AI_WORKERS={AI_WORKERS} (GPU={USE_CUDA}), WEB_WORKERS={MAX_WORKERS}", flush=True)

PROXY_FILE    = "SAR97653.txt"
ACCOUNTS_FILE = "accounts.csv"
OUTPUT_DIR    = Path("attachments")

ALLOWED_MIME = frozenset({
    "image/jpeg", "image/jpg", "image/png",
    "image/gif",  "image/bmp", "image/tiff",
    "image/webp", "image/heic",
    "application/pdf",
})
