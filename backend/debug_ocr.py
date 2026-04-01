
import sys
import os
import time
import platform
import logging
from pathlib import Path

log_path = Path("ocr_debug.log")
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger(__name__)

SEP = "=" * 60

def section(title: str):
    log.info("")
    log.info(SEP)
    log.info(f"  {title}")
    log.info(SEP)

section("BƯỚC 1: Thông tin hệ thống")
log.info(f"Python   : {sys.version}")
log.info(f"Platform : {platform.platform()}")
log.info(f"CWD      : {os.getcwd()}")
log.info(f"Log file : {log_path.resolve()}")

import shutil
total, used, free = shutil.disk_usage("/")
log.info(f"Disk     : total={total//1e9:.1f}GB, used={used//1e9:.1f}GB, free={free//1e9:.1f}GB")

try:
    import psutil
    mem = psutil.virtual_memory()
    log.info(f"RAM      : total={mem.total//1e6:.0f}MB, available={mem.available//1e6:.0f}MB, used={mem.percent}%")
    log.info(f"CPU      : {psutil.cpu_count()} cores, load={psutil.cpu_percent(interval=1):.1f}%")
except ImportError:
    log.warning("psutil không có — cài bằng: pip install psutil")

section("BƯỚC 2: Import dependencies")

deps = {
    "numpy":       None,
    "cv2":         None,
    "PIL":         None,
    "pdfplumber":  None,
    "torch":       None,
    "easyocr":     None,
}

for name in deps:
    try:
        mod = __import__(name)
        ver = getattr(mod, "__version__", "unknown")
        deps[name] = mod
        log.info(f"  ✓ {name:<15} version = {ver}")
    except ImportError as e:
        log.error(f"  ✗ {name:<15} KHÔNG import được: {e}")

section("BƯỚC 3: GPU / CUDA")
if deps["torch"]:
    import torch
    log.info(f"  torch.cuda.is_available() = {torch.cuda.is_available()}")
    log.info(f"  → EasyOCR sẽ dùng: {'GPU' if torch.cuda.is_available() else 'CPU (chậm hơn nhưng vẫn chạy được)'}")
    if torch.cuda.is_available():
        log.info(f"  CUDA device: {torch.cuda.get_device_name(0)}")
else:
    log.warning("  torch chưa import được, bỏ qua bước này")

section("BƯỚC 4: EasyOCR Model Cache")
model_dirs = [
    Path.home() / ".EasyOCR" / "model",
    Path("/root/.EasyOCR/model"),
    Path("/home") / os.environ.get("USER", "user") / ".EasyOCR" / "model",
]
for d in model_dirs:
    if d.exists():
        files = list(d.glob("*"))
        log.info(f"  Model dir: {d}")
        for f in files:
            size_mb = f.stat().st_size / 1e6
            log.info(f"    - {f.name} ({size_mb:.1f} MB)")
        if not files:
            log.warning(f"  ⚠ Thư mục model RỖNG! EasyOCR cần download model lần đầu.")
    else:
        log.warning(f"  ✗ Không tìm thấy: {d}")

section("BƯỚC 5: Khởi tạo EasyOCR Reader")
reader = None
if deps["easyocr"]:
    import easyocr
    try:
        log.info("  Đang load easyocr.Reader(['it', 'en'], gpu=False) ...")
        log.info("  (Nếu chưa có model, sẽ tự download — cần internet & ~100MB)")
        t0 = time.time()
        reader = easyocr.Reader(['it', 'en'], gpu=False)
        elapsed = time.time() - t0
        log.info(f"  ✓ Reader load thành công trong {elapsed:.1f}s")
    except Exception as e:
        log.error(f"  ✗ FAILED khởi tạo Reader: {e}", exc_info=True)
else:
    log.error("  ✗ easyocr không import được, bỏ qua bước này")

section("BƯỚC 6: Test OCR trên ảnh thực tế")

test_images = []
attach_dir = Path("attachments")
if attach_dir.exists():
    for ext in ("*.jpg", "*.jpeg", "*.png"):
        test_images.extend(list(attach_dir.rglob(ext)))
    log.info(f"  Tìm thấy {len(test_images)} ảnh trong attachments/")
else:
    log.warning("  ✗ Thư mục attachments/ không tồn tại")

if not test_images:
    log.info("  → Tạo ảnh test giả để kiểm tra pipeline...")
    try:
        from PIL import Image, ImageDraw, ImageFont
        import numpy as np
        
        img = Image.new('RGB', (856, 540), color=(240, 240, 230))
        draw = ImageDraw.Draw(img)
        draw.rectangle([10, 10, 845, 530], outline=(0, 0, 0), width=3)
        draw.text((30, 30),  "CARTA D'IDENTITA",  fill=(0, 0, 128))
        draw.text((30, 80),  "REPUBBLICA ITALIANA", fill=(0, 0, 0))
        draw.text((30, 120), "COGNOME: ROSSI",      fill=(0, 0, 0))
        draw.text((30, 150), "NOME: MARIO",         fill=(0, 0, 0))
        draw.text((30, 180), "CODICE FISCALE: RSSMRA80A01H501Z", fill=(0, 0, 0))
        
        test_path = Path("test_id_card.jpg")
        img.save(test_path, quality=95)
        size_kb = test_path.stat().st_size / 1024
        log.info(f"  ✓ Tạo ảnh test: {test_path} ({size_kb:.1f} KB)")
        test_images = [test_path]
    except Exception as e:
        log.error(f"  ✗ Không tạo được ảnh test: {e}", exc_info=True)

if test_images and reader:
    for img_path in test_images[:3]:
        log.info(f"\n  --- OCR ảnh: {img_path} ---")
        size_kb = img_path.stat().st_size / 1024
        log.info(f"  File size: {size_kb:.1f} KB")
        
        if size_kb < 30:
            log.warning(f"  ⚠ Layer 1 FAIL: {size_kb:.1f} KB < 30 KB → sẽ bị loại bỏ!")
        elif size_kb > 10000:
            log.warning(f"  ⚠ Layer 1 FAIL: {size_kb:.1f} KB > 10 MB → sẽ bị loại bỏ!")
        else:
            log.info(f"  ✓ Layer 1 (size): PASS")
        
        if deps["cv2"]:
            import cv2
            img_cv = cv2.imread(str(img_path))
            if img_cv is not None:
                h, w = img_cv.shape[:2]
                ratio = max(w, h) / min(w, h)
                log.info(f"  Layer 2 (aspect): {w}x{h}, ratio={ratio:.2f}")
                if ratio < 1.1 or ratio > 2.5:
                    log.warning(f"  ⚠ Layer 2 FAIL: ratio={ratio:.2f} ngoài [1.1, 2.5] → sẽ bị loại bỏ!")
                else:
                    log.info(f"  ✓ Layer 2 (aspect): PASS")
            else:
                log.warning(f"  ⚠ cv2.imread() trả về None — ảnh bị corrupt hoặc format không hỗ trợ")
        
        try:
            log.info(f"  EasyOCR readtext() bắt đầu...")
            t0 = time.time()
            results = reader.readtext(str(img_path), detail=0)
            elapsed = time.time() - t0
            text = " ".join(results).lower()
            log.info(f"  ✓ EasyOCR xong trong {elapsed:.2f}s")
            log.info(f"  Số đoạn text: {len(results)}")
            log.info(f"  Text đọc được: {repr(text[:500])}")
            
            VALID_KWS   = ["identita", "carta", "codice", "fiscale", "patente", "repubblica italiana", "ministero"]
            INVALID_KWS = ["contratto", "catastale", "fattura", "preventivo", "bolletta"]
            
            found_invalid = [k for k in INVALID_KWS if k in text]
            found_valid   = [k for k in VALID_KWS   if k in text]
            
            if found_invalid:
                log.warning(f"  ✗ Keyword LOẠI TRỪ: {found_invalid}")
            if found_valid:
                log.info(f"  ✓ Keyword HỢP LỆ: {found_valid} → SẼ ĐƯỢC LƯU vào documents/")
            else:
                log.warning(f"  ✗ Không tìm thấy keyword hợp lệ nào → SẼ BỊ BỎ VÀO review/")
                log.warning(f"  (Kiểm tra text raw ở trên để xem có gì không)")
                
        except Exception as e:
            log.error(f"  ✗ EasyOCR CRASH: {e}", exc_info=True)
elif not reader:
    log.error("  ✗ Bỏ qua test OCR vì Reader không khởi tạo được!")
elif not test_images:
    log.warning("  ✗ Không có ảnh để test!")

section("BƯỚC 7: Test PDF (pdfplumber)")
if deps["pdfplumber"]:
    pdf_files = list(Path("attachments").rglob("*.pdf")) if Path("attachments").exists() else []
    if pdf_files:
        import pdfplumber
        test_pdf = pdf_files[0]
        log.info(f"  Test PDF: {test_pdf}")
        try:
            with pdfplumber.open(str(test_pdf)) as pdf:
                log.info(f"  Số trang: {len(pdf.pages)}")
                for i, page in enumerate(pdf.pages[:2]):
                    text = page.extract_text() or ""
                    log.info(f"  Trang {i+1}: {len(text)} ký tự — {repr(text[:200])}")
        except Exception as e:
            log.error(f"  ✗ PDFPlumber FAIL: {e}", exc_info=True)
    else:
        log.info("  Không có file PDF trong attachments/ để test")

section("KẾT LUẬN")
issues = []
if not deps["easyocr"]:
    issues.append("❌ easyocr không import được")
if not deps["cv2"]:
    issues.append("❌ opencv-python không import được")
if not reader:
    issues.append("❌ EasyOCR Reader không khởi tạo được (model download lỗi hoặc hết RAM)")

if issues:
    log.error("VẤN ĐỀ PHÁT HIỆN:")
    for i in issues:
        log.error(f"  {i}")
    log.error("\n→ Giải pháp:")
    log.error("  1. Kiểm tra internet VPS: curl -I https://github.com")
    log.error("  2. Download model thủ công: python -c \"import easyocr; easyocr.Reader(['it','en'], gpu=False)\"")
    log.error("  3. Kiểm tra RAM: free -m")
    log.error("  4. Cài đầy đủ: pip install easyocr opencv-python-headless pdfplumber pillow")
else:
    log.info("✅ Mọi component hoạt động bình thường!")
    log.info(f"  → Xem log đầy đủ tại: {log_path.resolve()}")

log.info(f"\nLog đã được lưu vào: {log_path.resolve()}")
