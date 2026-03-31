import os
import sys
import time as _time
import shutil
import queue
import threading
from pathlib import Path
from email.message import Message


def _log(msg: str):
    """Print with immediate flush — required for PM2/non-TTY environments."""
    print(msg, flush=True)


_import_error = None
try:
    import easyocr
    import pdfplumber
    import cv2
    import numpy as np
    from PIL import Image
    from pyzbar.pyzbar import decode as pyzbar_decode
    AI_ENABLED = True
except ImportError as e:
    AI_ENABLED = False
    _import_error = str(e)

_log(f"[OCR-DEBUG] ══════════════════════════════════════════════════")
_log(f"[OCR-DEBUG] Module classifier.py loaded")
_log(f"[OCR-DEBUG] AI_ENABLED = {AI_ENABLED}")
if not AI_ENABLED:
    _log(f"[OCR-DEBUG] ✗ Import error: {_import_error}")
    _log(f"[OCR-DEBUG] ✗ Classifier sẽ KHÔNG hoạt động!")
else:
    _log(f"[OCR-DEBUG] ✓ Tất cả dependencies OK (easyocr, pdfplumber, cv2)")
_log(f"[OCR-DEBUG] ══════════════════════════════════════════════════")

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
        self.VALID_KWS = [
            "identita", "carta", "codice", "fiscale", "patente", "repubblica italiana", "ministero",
            "cognome", "nome", "nato", "nata", "cittadinanza", "residenza", "statura", "scadenza"
        ]
        # Invalid keywords (contracts, property, etc)
        self.INVALID_KWS = ["contratto", "catastale", "fattura", "preventivo", "bolletta"]

        self.face_cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml' if AI_ENABLED else ""
        self.face_cascade = None

    def start(self):
        _log(f"[OCR-DEBUG] classifier.start() được gọi — AI_ENABLED={AI_ENABLED}")
        if not AI_ENABLED:
            _log(f"[OCR-DEBUG] ✗ BỎ QUA: AI_ENABLED=False, classifier KHÔNG start!")
            _log(f"[OCR-DEBUG] ✗ Lỗi import: {_import_error}")
            return
        self._stop.clear()
        
        # Initialize EasyOCR Reader once (loading models takes time)
        _log("[OCR-DEBUG] ═══════════════════════════════════════════")
        _log("[OCR-DEBUG] Khởi tạo AI (EasyOCR + Face Cascades)...")
        t0 = _time.time()
        self.reader = easyocr.Reader(['it', 'en'], gpu=False)
        self.face_cascade = cv2.CascadeClassifier(self.face_cascade_path)
        elapsed = _time.time() - t0
        _log(f"[OCR-DEBUG] ✓ Khởi tạo xong AI — mất {elapsed:.1f}s")
        _log("[OCR-DEBUG] ═══════════════════════════════════════════")
        
        self._thread = threading.Thread(target=self._run, daemon=True, name="AI_Classifier")
        self._thread.start()
        _log("[OCR-DEBUG] ✓ AI Classifier thread đã start!")

    def stop(self):
        self._stop.set()
        # Wake up the queue thread if it's blocking
        ai_queue.put(None) 
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self):
        _log("[OCR-DEBUG] AI Classifier thread đang chạy, chờ jobs...")
        while not self._stop.is_set():
            try:
                job = ai_queue.get(timeout=2)
                if job is None:
                    continue
                
                email_addr, file_path, mime = job
                _log(f"[OCR-DEBUG] ───────────────────────────────────────")
                _log(f"[OCR-DEBUG] 📥 Nhận job mới từ queue:")
                _log(f"[OCR-DEBUG]   Email : {email_addr}")
                _log(f"[OCR-DEBUG]   File  : {file_path}")
                _log(f"[OCR-DEBUG]   MIME  : {mime}")
                _log(f"[OCR-DEBUG]   Queue còn lại: ~{ai_queue.qsize()} jobs")
                t_start = _time.time()
                self.process_file(email_addr, Path(file_path), mime)
                t_total = _time.time() - t_start
                _log(f"[OCR-DEBUG] ⏱ Tổng thời gian xử lý: {t_total:.2f}s")
                _log(f"[OCR-DEBUG] ───────────────────────────────────────")
                ai_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                msg = f"[AI] Lỗi xử lý file: {e}"
                _log(msg)
                state.add_ai_log(msg)

    # ── Pipeline ───────────────────────────────────────────────

    def process_file(self, email_addr: str, path: Path, mime: str):
        if not path.exists():
            _log(f"[OCR-DEBUG] ✗ File không tồn tại: {path}")
            return

        msg = f"[Tiến trình AI] Đang quét tệp... {path.name} ({mime})"
        _log(msg)
        state.add_ai_log(msg)

        # Output folders
        slug = __import__('re').sub(r'[^\w]', '_', email_addr.split("@")[0])
        docs_dir = OUTPUT_DIR / slug / "documents"
        review_dir = OUTPUT_DIR / slug / "review"
        
        # Layer 1: File Heuristics
        size_kb = path.stat().st_size / 1024
        _log(f"[OCR-DEBUG] Layer 1 — File size: {size_kb:.1f} KB (cho phép: 30KB ~ 10MB)")
        if not self._layer1_file_check(path):
            msg = f" ↳ [Bỏ qua] Kích thước file rác: {path.name} ({size_kb:.1f} KB)"
            _log(msg)
            state.add_ai_log(msg)
            self._move(path, review_dir)
            return
        _log(f"[OCR-DEBUG] Layer 1 — ✓ PASS")

        # Layer 2: Image Heuristics
        if "image" in mime:
            _log(f"[OCR-DEBUG] Layer 2 — Kiểm tra image heuristics...")
            if not self._layer2_image_check(path):
                msg = f" ↳ [Bỏ qua] Ảnh không đúng tỷ lệ ID: {path.name}"
                _log(msg)
                state.add_ai_log(msg)
                self._move(path, review_dir)
                return
            _log(f"[OCR-DEBUG] Layer 2 — ✓ PASS")
        else:
            _log(f"[OCR-DEBUG] Layer 2 — Bỏ qua (không phải image)")

        # Feature Extraction layer (Barcode / Face)
        features = self._layer2_5_features_check(path, mime)
        msg_ft = f" ↳ Tính năng AI — Mặt: {features['faces']}, Mã vạch: {features['has_barcode']}"
        _log(msg_ft)
        state.add_ai_log(msg_ft)

        # Layer 3: OCR / PDF Text Scanning
        msg = f" ↳ Đang chạy EasyOCR/PDFPlumber trích xuất text..."
        _log(msg)
        state.add_ai_log(msg)
        t_ocr = _time.time()
        text = self._layer3_extract_text(path, mime)
        ocr_elapsed = _time.time() - t_ocr
        _log(f"[OCR-DEBUG] Layer 3 — OCR xong trong {ocr_elapsed:.2f}s, trích được {len(text)} ký tự")
        
        is_valid, side = self._evaluate_text_and_features(text, features)
        if is_valid:
            prefix = side if side else "DOC"
            msg_ok = f" ↳ ✅ TÌM THẤY TÀI LIỆU HỢP LỆ ({prefix}): {path.name}"
            _log(msg_ok)
            state.add_ai_log(msg_ok)
            
            new_name = f"{prefix}_{path.name}"
            docs_dir.mkdir(parents=True, exist_ok=True)
            self._move_with_name(path, docs_dir, new_name)
            
            # Update UI state
            state.inc("documents_found")
            state.update_account(email_addr, last_file=f"✅ {prefix}: {path.name}")
        else:
            msg_no = f" ↳ ❌ Không tìm thấy thông tin/ID: {path.name}"
            _log(msg_no)
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

    def _layer2_5_features_check(self, path: Path, mime: str) -> dict:
        result = {"faces": 0, "has_barcode": False}
        if "image" not in mime:
            return result
        try:
            img = cv2.imread(str(path))
            if img is None:
                return result
                
            # Barcode check
            barcodes = pyzbar_decode(img)
            if barcodes:
                result["has_barcode"] = True
                
            # Face check (using grayscale for faster/better detection)
            if self.face_cascade:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                faces = self.face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
                result["faces"] = len(faces)
        except Exception as e:
            _log(f"[OCR-DEBUG] ✗ Lỗi trích xuất tính năng face/barcode: {e}")
        return result

    def _layer3_extract_text(self, path: Path, mime: str) -> str:
        """Extract text depending on file type"""
        text = ""
        try:
            if "pdf" in mime:
                _log(f"[OCR-DEBUG] ▶ PDFPlumber bắt đầu đọc: {path.name}")
                with pdfplumber.open(str(path)) as pdf:
                    _log(f"[OCR-DEBUG]   PDF có {len(pdf.pages)} trang")
                    for i, page in enumerate(pdf.pages):
                        if i > 2: break # Only check first 3 pages
                        ext = page.extract_text()
                        if ext:
                            _log(f"[OCR-DEBUG]   Trang {i+1}: trích được {len(ext)} ký tự")
                            text += ext.lower() + " "
                        else:
                            _log(f"[OCR-DEBUG]   Trang {i+1}: không có text")
                _log(f"[OCR-DEBUG] ◀ PDFPlumber xong: {path.name}")
            else:
                # OCR Image
                _log(f"[OCR-DEBUG] ▶ EasyOCR bắt đầu quét ảnh: {path.name} ({mime})")
                _log(f"[OCR-DEBUG]   File size: {path.stat().st_size / 1024:.1f} KB")
                t_read = _time.time()
                results = self.reader.readtext(str(path), detail=0)
                read_elapsed = _time.time() - t_read
                text = " ".join(results).lower()
                _log(f"[OCR-DEBUG]   ⏱ EasyOCR readtext mất: {read_elapsed:.2f}s")
                _log(f"[OCR-DEBUG]   EasyOCR trả về {len(results)} đoạn text")
                if results:
                    _log(f"[OCR-DEBUG]   Nội dung OCR: {text[:300]}{'...' if len(text) > 300 else ''}")
                else:
                    _log(f"[OCR-DEBUG]   ⚠ EasyOCR không đọc được text nào!")
                _log(f"[OCR-DEBUG] ◀ EasyOCR xong: {path.name}")
        except Exception as e:
            _log(f"[OCR-DEBUG] ✗ Text extraction FAILED trên {path.name}: {e}")
        
        _log(f"[OCR-DEBUG] Tổng text trích xuất: {len(text)} ký tự")
        return text

    def _evaluate_text_and_features(self, text: str, features: dict) -> tuple[bool, str]:
        # returns (is_valid, side) -> side = "FRONT" | "BACK" | ""
        if not text.strip() and not features.get('has_barcode'):
            _log(f"[OCR-DEBUG] ✗ Evaluate: rỗng text & ko barcode → bỏ qua")
            return False, ""
            
        # Reject invalid docs
        for k in self.INVALID_KWS:
            if k in text:
                _log(f"[OCR-DEBUG] ✗ Evaluate: tìm thấy '{k}' → bị loại")
                return False, ""
                
        has_text_kws = any(k in text for k in self.VALID_KWS)
        has_mrz = "<<" in text or "< <" in text
        
        # --- MẶT SAU (BACK) ---
        if features.get('has_barcode') or has_mrz:
            _log(f"[OCR-DEBUG] ✓ Evaluate: Có BARCODE / MRZ → MẶT SAU (BACK)")
            return True, "BACK"
            
        # --- MẶT TRƯỚC (FRONT) ---
        if has_text_kws:
            if features.get('faces', 0) > 0:
                _log(f"[OCR-DEBUG] ✓ Evaluate: Text hợp lệ + Có Khuôn mặt → MẶT TRƯỚC (FRONT)")
            else:
                _log(f"[OCR-DEBUG] ✓ Evaluate: Text hợp lệ (ko thấy mặt) → MẶT TRƯỚC (FRONT)")
            return True, "FRONT"
            
        _log(f"[OCR-DEBUG] ✗ Evaluate: Không đủ keyword hoặc đặc trưng → loại")
        return False, ""

    def _move(self, path: Path, dest_dir: Path):
        self._move_with_name(path, dest_dir, path.name)

    def _move_with_name(self, path: Path, dest_dir: Path, new_name: str):
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(path), str(dest_dir / new_name))
        except Exception:
            pass

# Global Singleton
classifier = ClassifierEngine()
