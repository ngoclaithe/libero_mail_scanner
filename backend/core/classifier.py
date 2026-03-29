import os
import time as _time
import shutil
import queue
import threading
from pathlib import Path
from email.message import Message

try:
    import easyocr
    import pdfplumber
    import cv2
    import numpy as np
    from PIL import Image
    AI_ENABLED = True
except ImportError:
    AI_ENABLED = False
    print("WARNING: easyocr, pdfplumber, cv2 not installed. Classifier is disabled.")

from core.state import state
from core.config import OUTPUT_DIR

# Global queue for jobs: (email_addr, file_path, mime_type)
ai_queue = queue.Queue()

class ClassifierEngine:
    """Consumes file paths from ai_queue, runs 3-layer filter, moves valid docs."""

    def __init__(self):
        self._stop = threading.Event()
        self._thread = None
        self.reader = None
        
        # Valid keywords for Italian IDs (Carta d'Identita, Codice Fiscale, Patente)
        self.VALID_KWS = ["identita", "carta", "codice", "fiscale", "patente", "repubblica italiana", "ministero"]
        # Invalid keywords (contracts, property, etc)
        self.INVALID_KWS = ["contratto", "catastale", "fattura", "preventivo", "bolletta"]

    def start(self):
        if not AI_ENABLED:
            return
        self._stop.clear()
        
        # Initialize EasyOCR Reader once (loading models takes time)
        print("[OCR-DEBUG] ═══════════════════════════════════════════")
        print("[OCR-DEBUG] Khởi tạo EasyOCR Models (Italian + English)...")
        t0 = _time.time()
        self.reader = easyocr.Reader(['it', 'en'], gpu=False)
        elapsed = _time.time() - t0
        print(f"[OCR-DEBUG] ✓ EasyOCR đã sẵn sàng — tải model mất {elapsed:.1f}s")
        print("[OCR-DEBUG] ═══════════════════════════════════════════")
        
        self._thread = threading.Thread(target=self._run, daemon=True, name="AI_Classifier")
        self._thread.start()

    def stop(self):
        self._stop.set()
        # Wake up the queue thread if it's blocking
        ai_queue.put(None) 
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self):
        while not self._stop.is_set():
            try:
                job = ai_queue.get(timeout=2)
                if job is None:
                    continue
                
                email_addr, file_path, mime = job
                print(f"[OCR-DEBUG] ───────────────────────────────────────")
                print(f"[OCR-DEBUG] 📥 Nhận job mới từ queue:")
                print(f"[OCR-DEBUG]   Email : {email_addr}")
                print(f"[OCR-DEBUG]   File  : {file_path}")
                print(f"[OCR-DEBUG]   MIME  : {mime}")
                print(f"[OCR-DEBUG]   Queue còn lại: ~{ai_queue.qsize()} jobs")
                t_start = _time.time()
                self.process_file(email_addr, Path(file_path), mime)
                t_total = _time.time() - t_start
                print(f"[OCR-DEBUG] ⏱ Tổng thời gian xử lý: {t_total:.2f}s")
                print(f"[OCR-DEBUG] ───────────────────────────────────────")
                ai_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                msg = f"[AI] Lỗi xử lý file: {e}"
                print(msg)
                state.add_ai_log(msg)

    # ── Pipeline ───────────────────────────────────────────────

    def process_file(self, email_addr: str, path: Path, mime: str):
        if not path.exists():
            return

        msg = f"[Tiến trình AI] Đang quét tệp... {path.name} ({mime})"
        print(msg)
        state.add_ai_log(msg)

        # Output folders
        slug = __import__('re').sub(r'[^\w]', '_', email_addr.split("@")[0])
        docs_dir = OUTPUT_DIR / slug / "documents"
        review_dir = OUTPUT_DIR / slug / "review"
        
        # Layer 1: File Heuristics
        size_kb = path.stat().st_size / 1024
        print(f"[OCR-DEBUG] Layer 1 — File size: {size_kb:.1f} KB (cho phép: 30KB ~ 10MB)")
        if not self._layer1_file_check(path):
            msg = f" ↳ [Bỏ qua] Kích thước file rác: {path.name} ({size_kb:.1f} KB)"
            print(msg)
            state.add_ai_log(msg)
            self._move(path, review_dir)
            return
        print(f"[OCR-DEBUG] Layer 1 — ✓ PASS")

        # Layer 2: Image Heuristics
        if "image" in mime:
            print(f"[OCR-DEBUG] Layer 2 — Kiểm tra image heuristics...")
            if not self._layer2_image_check(path):
                msg = f" ↳ [Bỏ qua] Ảnh không đúng tỷ lệ ID: {path.name}"
                print(msg)
                state.add_ai_log(msg)
                self._move(path, review_dir)
                return
            print(f"[OCR-DEBUG] Layer 2 — ✓ PASS")
        else:
            print(f"[OCR-DEBUG] Layer 2 — Bỏ qua (không phải image)")

        # Layer 3: OCR / PDF Text Scanning
        msg = f" ↳ Đang chạy EasyOCR/PDFPlumber trích xuất text..."
        print(msg)
        state.add_ai_log(msg)
        t_ocr = _time.time()
        text = self._layer3_extract_text(path, mime)
        ocr_elapsed = _time.time() - t_ocr
        print(f"[OCR-DEBUG] Layer 3 — OCR xong trong {ocr_elapsed:.2f}s, trích được {len(text)} ký tự")
        
        if self._evaluate_text(text):
            msg_ok = f" ↳ ✅ TÌM THẤY TÀI LIỆU ID HỢP LỆ: {path.name}"
            print(msg_ok)
            state.add_ai_log(msg_ok)
            self._move(path, docs_dir)
            # Update UI state
            state.inc("documents_found")
            state.update_account(email_addr, last_file=f"✅ DOC: {path.name}")
        else:
            msg_no = f" ↳ ❌ Không tìm thấy từ khóa liên quan ID: {path.name}"
            print(msg_no)
            state.add_ai_log(msg_no)
            self._move(path, review_dir)

    # ── Layers ───────────────────────────────────────────────

    def _layer1_file_check(self, path: Path) -> bool:
        """Check size limit: discard files < 30KB or > 10MB"""
        size_kb = path.stat().st_size / 1024
        if size_kb < 30 or size_kb > 10000:
            return False
        return True

    def _layer2_image_check(self, path: Path) -> bool:
        """Check aspect ratio and color saturation constraints"""
        try:
            name = path.name.lower()
            # Telegram/WhatsApp photo names often skip heuristic to prefer them
            if "photo_" in name or "whatsapp" in name:
                return True
                
            img = cv2.imread(str(path))
            if img is None:
                return False
                
            h, w = img.shape[:2]
            ratio = max(w, h) / min(w, h)
            # IDs typically have aspect ratio between 1.2 and 1.8
            if ratio < 1.1 or ratio > 2.5:
                return False
                
            return True
        except Exception:
            return True # Pass through to OCR if opencv fails

    def _layer3_extract_text(self, path: Path, mime: str) -> str:
        """Extract text depending on file type"""
        text = ""
        try:
            if "pdf" in mime:
                print(f"[OCR-DEBUG] ▶ PDFPlumber bắt đầu đọc: {path.name}")
                with pdfplumber.open(str(path)) as pdf:
                    print(f"[OCR-DEBUG]   PDF có {len(pdf.pages)} trang")
                    for i, page in enumerate(pdf.pages):
                        if i > 2: break # Only check first 3 pages
                        ext = page.extract_text()
                        if ext:
                            print(f"[OCR-DEBUG]   Trang {i+1}: trích được {len(ext)} ký tự")
                            text += ext.lower() + " "
                        else:
                            print(f"[OCR-DEBUG]   Trang {i+1}: không có text")
                print(f"[OCR-DEBUG] ◀ PDFPlumber xong: {path.name}")
            else:
                # OCR Image
                print(f"[OCR-DEBUG] ▶ EasyOCR bắt đầu quét ảnh: {path.name} ({mime})")
                print(f"[OCR-DEBUG]   File size: {path.stat().st_size / 1024:.1f} KB")
                t_read = _time.time()
                results = self.reader.readtext(str(path), detail=0)
                read_elapsed = _time.time() - t_read
                text = " ".join(results).lower()
                print(f"[OCR-DEBUG]   ⏱ EasyOCR readtext mất: {read_elapsed:.2f}s")
                print(f"[OCR-DEBUG]   EasyOCR trả về {len(results)} đoạn text")
                if results:
                    print(f"[OCR-DEBUG]   Nội dung OCR: {text[:300]}{'...' if len(text) > 300 else ''}")
                else:
                    print(f"[OCR-DEBUG]   ⚠ EasyOCR không đọc được text nào!")
                print(f"[OCR-DEBUG] ◀ EasyOCR xong: {path.name}")
        except Exception as e:
            print(f"[OCR-DEBUG] ✗ Text extraction FAILED trên {path.name}: {e}")
        
        print(f"[OCR-DEBUG] Tổng text trích xuất: {len(text)} ký tự")
        return text

    def _evaluate_text(self, text: str) -> bool:
        if not text.strip():
            print(f"[OCR-DEBUG] ✗ Evaluate: text rỗng → bỏ qua")
            return False
            
        # Reject invalid docs
        for k in self.INVALID_KWS:
            if k in text:
                print(f"[OCR-DEBUG] ✗ Evaluate: tìm thấy từ khóa LOẠI TRỪ '{k}' → loại bỏ")
                return False
                
        # Must contain at least one valid ID keyword
        for k in self.VALID_KWS:
            if k in text:
                print(f"[OCR-DEBUG] ✓ Evaluate: tìm thấy từ khóa HỢP LỆ '{k}' → DOCUMENT!")
                return True
        
        print(f"[OCR-DEBUG] ✗ Evaluate: không tìm thấy từ khóa hợp lệ nào → loại bỏ")
        return False

    def _move(self, path: Path, dest_dir: Path):
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(path), str(dest_dir / path.name))
        except Exception:
            pass

# Global Singleton
classifier = ClassifierEngine()
