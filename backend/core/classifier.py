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
    from rapidocr_onnxruntime import RapidOCR
    import pdfplumber
    import cv2
    import numpy as np
    from PIL import Image
    from uniface import RetinaFace
    AI_ENABLED = True
except ImportError as e:
    AI_ENABLED = False
    _import_error = str(e)
    _import_error = str(e)

try:
    from pyzbar.pyzbar import decode as pyzbar_decode
    ZBAR_ENABLED = True
except ImportError as e:
    ZBAR_ENABLED = False
    _zbar_error = str(e)

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
        # Keywords moved into scoring engine (_evaluate_text_and_features)

    def start(self, user_state=None):
        _log(f"[OCR-DEBUG] classifier.start() được gọi — AI_ENABLED={AI_ENABLED}")
        if not AI_ENABLED:
            msg = f"❌ LỖI NGHIÊM TRỌNG: Mất kết nối môi trường AI do thiếu thư viện: {_import_error}"
            _log(msg)
            if user_state:
                user_state.add_ai_log(msg)
            return
            
        if not ZBAR_ENABLED:
            msg_warn = f"⚠️ CẢNH BÁO: Tính năng quét mã vạch (pyzbar) bị lỗi hệ thống. Lỗi chi tiết: {_zbar_error}"
            _log(msg_warn)
            if user_state:
                user_state.add_ai_log(msg_warn)
            
        if self._thread and self._thread.is_alive():
            _log("[OCR-DEBUG] AI Classifier thread vẫn đang chạy, bỏ qua bước khởi tạo lại model.")
            return

        self._stop.clear()
        
        # Initialize EasyOCR Reader once (loading models takes time)
        _log("[OCR-DEBUG] ═══════════════════════════════════════════")
        _log("[OCR-DEBUG] Khởi tạo AI (RapidONNX + RetinaFace)...")
        t0 = _time.time()
        self.reader = RapidOCR()
        self.face_detector = RetinaFace()
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
                
                # Unpack supporting 4 elements
                if len(job) == 4:
                    email_addr, file_path, mime, user_state = job
                else:
                    email_addr, file_path, mime = job
                    # Fallback to local import if user_state is missing (stale queue) 
                    from core.state import state as user_state
                
                _log(f"[OCR-DEBUG] ───────────────────────────────────────")
                _log(f"[OCR-DEBUG] 📥 Nhận job mới từ queue:")
                _log(f"[OCR-DEBUG]   Email : {email_addr}")
                _log(f"[OCR-DEBUG]   File  : {file_path}")
                _log(f"[OCR-DEBUG]   MIME  : {mime}")
                _log(f"[OCR-DEBUG]   Queue còn lại: ~{ai_queue.qsize()} jobs")
                t_start = _time.time()
                self.process_file(email_addr, Path(file_path), mime, user_state)
                t_total = _time.time() - t_start
                _log(f"[OCR-DEBUG] ⏱ Tổng thời gian xử lý: {t_total:.2f}s")
                _log(f"[OCR-DEBUG] ───────────────────────────────────────")
                ai_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                msg = f"[AI] Lỗi xử lý file: {e}"
                _log(msg)
                if 'user_state' in locals() and user_state:
                    user_state.add_ai_log(msg)

    # ── Pipeline ───────────────────────────────────────────────

    def process_file(self, email_addr: str, path: Path, mime: str, user_state):
        if not path.exists():
            _log(f"[OCR-DEBUG] ✗ File không tồn tại: {path}")
            return

        msg = f"[Tiến trình AI] Đang quét tệp... {path.name} ({mime})"
        _log(msg)
        user_state.add_ai_log(msg)

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
            user_state.add_ai_log(msg)
            self._move(path, review_dir)
            return
        _log(f"[OCR-DEBUG] Layer 1 — ✓ PASS")

        # Layer 2: Image Heuristics
        if "image" in mime:
            _log(f"[OCR-DEBUG] Layer 2 — Kiểm tra image heuristics...")
            if not self._layer2_image_check(path):
                msg = f" ↳ [Bỏ qua] Ảnh không đúng tỷ lệ ID: {path.name}"
                _log(msg)
                user_state.add_ai_log(msg)
                self._move(path, review_dir)
                return
            _log(f"[OCR-DEBUG] Layer 2 — ✓ PASS")
        else:
            _log(f"[OCR-DEBUG] Layer 2 — Bỏ qua (không phải image)")

        # Feature Extraction layer (Barcode / Face)
        features = self._layer2_5_features_check(path, mime)
        msg_ft = f" ↳ AI Đặc trưng — Khuôn mặt: {'Có' if features['faces'] else 'Ko'}, Mã vạch: {'Có' if features['has_barcode'] else 'Ko'}"
        _log(msg_ft)
        user_state.add_ai_log(msg_ft)

        # Layer 3: OCR / PDF Text Scanning
        msg = f" ↳ Đang chạy EasyOCR/PDFPlumber trích xuất text..."
        _log(msg)
        user_state.add_ai_log(msg)
        t_ocr = _time.time()
        text = self._layer3_extract_text(path, mime)
        ocr_elapsed = _time.time() - t_ocr
        _log(f"[OCR-DEBUG] Layer 3 — OCR xong trong {ocr_elapsed:.2f}s, trích được {len(text)} ký tự")
        
        is_valid, side = self._evaluate_text_and_features(text, features, mime)
        if is_valid:
            prefix = side if side else "DOC"
            msg_ok = f" ↳ ✅ TÌM THẤY TÀI LIỆU HỢP LỆ ({prefix}): {path.name}"
            _log(msg_ok)
            user_state.add_ai_log(msg_ok)
            
            new_name = f"{prefix}_{path.name}"
            docs_dir.mkdir(parents=True, exist_ok=True)
            self._move_with_name(path, docs_dir, new_name)
            
            # LƯU ẢNH FACE VÀO SUBFOLDER RIÊNG (không hiển thị trong gallery chính)
            if prefix == "FRONT" and features.get("face_crop") is not None:
                face_name = f"FACE_{path.name}"
                # Nếu ảnh gốc là pdf thì lưu face dưới dạng jpg
                if face_name.lower().endswith(".pdf"):
                    face_name = face_name[:-4] + ".jpg"
                # Lưu vào faces/ subfolder thay vì thẳng vào documents/
                faces_dir = docs_dir / "faces"
                faces_dir.mkdir(parents=True, exist_ok=True)
                face_path = faces_dir / face_name
                cv2.imwrite(str(face_path), features["face_crop"])
                _log(f"[OCR-DEBUG] ✓ Face crop lưu vào: faces/{face_name}")
                # Xóa file FACE_ cũ trong docs_dir nếu có (dọn dẹp từ phiên bản cũ)
                old_face_path = docs_dir / face_name
                if old_face_path.exists():
                    try:
                        old_face_path.unlink()
                        _log(f"[OCR-DEBUG] Đã xóa face cũ lạc chỗ: {old_face_path.name}")
                    except Exception:
                        pass
            
            # Update UI state
            user_state.inc("documents_found")
            user_state.update_account(email_addr, last_file=f"✅ {prefix}: {path.name}")
        else:
            msg_no = f" ↳ ❌ File rác / Rỗng thông tin: {path.name}"
            _log(msg_no)
            user_state.add_ai_log(msg_no)
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
                
            # Barcode check — chỉ nhận mã vạch LINEAR (1D dạng dài)
            # Loại bỏ QR code (vuông), Aztec, DataMatrix vì không phải CCCD/giấy tờ
            if ZBAR_ENABLED:
                barcodes = pyzbar_decode(img)
                linear_barcodes = [
                    b for b in barcodes
                    if b.type not in ('QRCODE', 'AZTEC', 'DATAMATRIX')
                ]
                if linear_barcodes:
                    result["has_barcode"] = True
                    _log(f"[OCR-DEBUG] Tìm thấy {len(linear_barcodes)} mã vạch 1D: {[b.type for b in linear_barcodes]}")
                elif barcodes:
                    _log(f"[OCR-DEBUG] Bỏ qua {len(barcodes)} mã 2D (QR/Aztec/DataMatrix): {[b.type for b in barcodes]}")
                
            # Face check (Uniface RetinaFace - High Precision)
            if self.face_detector:
                faces = self.face_detector.detect(img)
                if faces:
                    result["faces"] = len(faces)
                    # Lấy khuôn mặt đầu tiên
                    fb = faces[0].bbox
                    try:
                        x1, y1, x2, y2 = int(fb[0]), int(fb[1]), int(fb[2]), int(fb[3])
                        # Bắt lỗi padding nếu box lọt ngoài ảnh
                        h, w = img.shape[:2]
                        x1, y1 = max(0, x1), max(0, y1)
                        x2, y2 = min(w, x2), min(h, y2)
                        if x2 > x1 and y2 > y1:
                            result["face_crop"] = img[y1:y2, x1:x2]
                    except Exception as e:
                        pass
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
                _log(f"[OCR-DEBUG] ▶ RapidOCR bắt đầu quét ảnh: {path.name} ({mime})")
                _log(f"[OCR-DEBUG]   File size: {path.stat().st_size / 1024:.1f} KB")
                
                # Nén ảnh để OCR chạy lướt (Phương án 1 + 2)
                img_ocr = cv2.imread(str(path))
                if img_ocr is not None:
                    h, w = img_ocr.shape[:2]
                    max_dim = 1200
                    if max(h, w) > max_dim:
                        scale = max_dim / max(h, w)
                        img_ocr = cv2.resize(img_ocr, (int(w*scale), int(h*scale)))
                        _log(f"[OCR-DEBUG]   Đã ép nhỏ ảnh từ {w}x{h} xuống {img_ocr.shape[1]}x{img_ocr.shape[0]} để tối ưu tốc độ")
                
                t_read = _time.time()
                # rapidocr trả về: (results, elapse)
                # results là list: [ [box, text, score], ... ]
                ocr_results, _ = self.reader(img_ocr)
                read_elapsed = _time.time() - t_read
                
                if ocr_results:
                    txt_list = [item[1] for item in ocr_results if item[2] > 0.3] # Lọc chữ có tự tin > 30%
                    text = " ".join(txt_list).lower()
                    _log(f"[OCR-DEBUG]   ⏱ RapidOCR mất: {read_elapsed:.2f}s")
                    _log(f"[OCR-DEBUG]   Trả về {len(txt_list)} đoạn text")
                    _log(f"[OCR-DEBUG]   Nội dung OCR: {text[:300]}{'...' if len(text) > 300 else ''}")
                else:
                    _log(f"[OCR-DEBUG]   ⏱ RapidOCR mất: {read_elapsed:.2f}s")
                    _log(f"[OCR-DEBUG]   ⚠ RapidOCR không đọc được text nào!")
                _log(f"[OCR-DEBUG] ◀ RapidOCR xong: {path.name}")
        except Exception as e:
            _log(f"[OCR-DEBUG] ✗ Text extraction FAILED trên {path.name}: {e}")
        
        _log(f"[OCR-DEBUG] Tổng text trích xuất: {len(text)} ký tự")
        return text

    def _evaluate_text_and_features(self, text: str, features: dict, mime: str) -> tuple[bool, str]:
        """
        Scoring engine cho CIE (Carta d'Identit\u00e0 Elettronica) Italia.
        Score >= THRESHOLD_BACK  -> BACK
        Score >= THRESHOLD_FRONT -> FRONT
        """
        import re

        # ── Scoring thresholds ──────────────────────────────
        THRESHOLD_BACK  = 2   # d\u1ec5 h\u01a1n: MRZ + barcode l\u00e0 d\u1ea5u hi\u1ec7u r\u00f5 r\u00e0ng
        THRESHOLD_FRONT = 3   # ch\u1eb7t h\u01a1n: c\u1ea7n \u00edt nh\u1ea5t 3 d\u1ea5u hi\u1ec7u

        score_back  = 0
        score_front = 0
        reasons_back  = []
        reasons_front = []

        # ─────────────────────────────────────────────────────
        is_pdf = "pdf" in mime
        has_hard_back_signal = features.get('has_barcode') or (has_mrz_arrows and has_ita)

        # GUARD 1: PDF khong co barcode/MRZ thi KHONG the la BACK
        # Codice Fiscale co o moi giay to Y (hop dong, don, form...)
        # Chi mat sau CCCD thuc su moi co ma vach 1D hoac MRZ
        if is_pdf and not has_hard_back_signal and score_back > 0:
            _log(f"[OCR-DEBUG] x GUARD-PDF: Khong co barcode/MRZ -> xoa BACK score (Codice Fiscale thoi khong du)")
            score_back = 0

        # GUARD 2: Text qua dai ma khong co hard signal = document nhieu trang, khong phai CCCD
        # CCCD mat sau: ~500-800 ky tu toi da
        if len(text) > 2500 and not has_hard_back_signal:
            _log(f"[OCR-DEBUG] x GUARD-LENGTH: Text {len(text)} ky tu + khong co hard signal -> van ban dai, loai")
            return False, ""

        # BACK uu tien hon (score cao hon hoac barcode/MRZ ro rang)
        if score_back >= THRESHOLD_BACK and score_back >= score_front:
            _log(f"[OCR-DEBUG] + Ket qua: BACK (score={score_back})")
            return True, "BACK"

        if score_front >= THRESHOLD_FRONT:
            # FRONT bat buoc phai co face (tranh nhan nham document van ban)
            if features.get('faces', 0) == 0:
                _log(f"[OCR-DEBUG] x FRONT score={score_front} du nhung KHONG CO FACE -> loai")
                return False, ""
            _log(f"[OCR-DEBUG] + Ket qua: FRONT (score={score_front})")
            return True, "FRONT"

        _log(f"[OCR-DEBUG] x Khong du dieu kien: BACK={score_back}<{THRESHOLD_BACK}, FRONT={score_front}<{THRESHOLD_FRONT}")
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
