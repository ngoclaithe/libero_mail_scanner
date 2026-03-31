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
        # BACK SIGNALS
        # ─────────────────────────────────────────────────────

        # [+3] MRZ pattern: 3 d\u00f2ng v\u1edbi << v\u00e0 ITA — d\u1ea5u hi\u1ec7u c\u1ef1c k\u1ef3 ch\u1eafc
        # M\u1eabu: C<ITACA29739HP2<<<<<<<<\n650303...\nFORTIGUERRA<<ANNA<MARIA
        mrz_line = bool(re.search(r'[A-Z0-9<]{20,}', text.upper()))
        has_ita   = bool(re.search(r'\bita\b|c<ita|itaca', text, re.IGNORECASE))
        has_mrz_arrows = '<<' in text or '< <' in text
        if has_mrz_arrows and has_ita:
            score_back += 3
            reasons_back.append('MRZ+ITA (+3)')
        elif has_mrz_arrows and mrz_line:
            score_back += 2
            reasons_back.append('MRZ_line (+2)')

        # [+3] Linear barcode 1D (\u0111\u00e3 filter QR \u1edf layer tr\u01b0\u1edbc)
        if features.get('has_barcode'):
            score_back += 3
            reasons_back.append('barcode_1D (+3)')

        # [+2] Codice Fiscale pattern: 6 ch\u1eef + 2 s\u1ed1 + 1 ch\u1eef + 2 s\u1ed1 + 1 ch\u1eef + 3 k/s + 1 s\u1ed1
        # M\u1eabu: FRTNMR65C43I130K  ho\u1eb7c  BLLCST86D42I628D
        codice_fiscale = re.search(
            r'\b[A-Z]{6}\d{2}[A-EHLMPRST]\d{2}[A-Z]\d{3}[A-Z]\b',
            text.upper()
        )
        if codice_fiscale:
            score_back += 2
            reasons_back.append(f'codice_fiscale:{codice_fiscale.group()} (+2)')

        # [+1] Keywords m\u1eb7t sau
        back_kws = ['codice fiscale', 'fiscal code', 'indirizzo di residenza',
                    'residence', 'madre', 'padre', 'father', 'mother',
                    'estremi atto', 'cognome e nome']
        for kw in back_kws:
            if kw in text:
                score_back += 1
                reasons_back.append(f'kw:{kw} (+1)')
                break  # ch\u1ec9 +1 cho c\u1ea3 nh\u00f3m

        # [+1] Nh\u1eef th\u1ea3 m\u1eb7t sau c\u00f3 face nh\u1ecf (\u1ea3nh \u0111\u1ecbnh danh ng\u01b0\u1eddi d\u00f9ng \u1edf g\u00f3c tr\u00ean ph\u1ea3i)
        if features.get('faces', 0) > 0:
            score_back += 1
            reasons_back.append('face_on_back (+1)')

        # ─────────────────────────────────────────────────────
        # FRONT SIGNALS
        # ─────────────────────────────────────────────────────

        # [+4] Header r\u1ea5t m\u1ea1nh: "REPUBBLICA ITALIANA" ho\u1eb7c "MINISTERO DELL'INTERNO"
        rep_italiana = bool(re.search(r'repubblica\s+italiana|ministero\s+dell', text, re.IGNORECASE))
        if rep_italiana:
            score_front += 4
            reasons_front.append('REPUBLICA_ITALIANA (+4)')

        # [+3] "CARTA DI IDENTIT\u00c0" ho\u1eb7c "IDENTITY CARD"
        carta_identita = bool(re.search(r'carta\s+di\s+identit|identity\s+card', text, re.IGNORECASE))
        if carta_identita:
            score_front += 3
            reasons_front.append('CARTA_IDENTITA (+3)')

        # [+2] S\u1ed1 th\u1ebb CIE: m\u1eabu 2 ch\u1eef + 5 s\u1ed1 + 2 ch\u1eef  (vd: CA29739HP, CA24621FY)
        cie_number = re.search(r'\b[A-Z]{2}\d{5}[A-Z]{2}\b', text.upper())
        if cie_number:
            score_front += 2
            reasons_front.append(f'CIE_number:{cie_number.group()} (+2)')

        # [+2] Khu\u00f4n m\u1eb7t r\u00f5 r\u00e0ng (m\u1eb7t tr\u01b0\u1edbc c\u00f3 face l\u1edbn)
        if features.get('faces', 0) > 0:
            score_front += 2
            reasons_front.append('face_detected (+2)')

        # [+1] Keywords m\u1eb7t tr\u01b0\u1edbc
        front_kws_hits = 0
        front_kws = ['cognome', 'surname', 'nome', 'name', 'emissione', 'issuing',
                     'scadenza', 'expiry', 'sesso', 'sex', 'statura', 'height',
                     'cittadinanza', 'nationality', 'luogo', 'nascita', 'birth',
                     'firma', 'holder', 'identita', 'identit']
        for kw in front_kws:
            if kw in text:
                front_kws_hits += 1
        if front_kws_hits >= 3:
            score_front += 2
            reasons_front.append(f'{front_kws_hits} front_kws (+2)')
        elif front_kws_hits >= 1:
            score_front += 1
            reasons_front.append(f'{front_kws_hits} front_kws (+1)')

        # [+1] Patente (b\u1eb1ng l\u00e1i xe) c\u0169ng l\u00e0 gi\u1ea5y t\u1edd h\u1ee3p l\u1ec7
        if 'patente' in text:
            score_front += 2
            reasons_front.append('patente (+2)')

        # ─────────────────────────────────────────────────────
        # BLACKLIST — lo\u1ea1i ngay n\u1ebfu c\u00f3
        # ─────────────────────────────────────────────────────
        BLACKLIST = ['contratto', 'fattura', 'bolletta', 'preventivo',
                     'catastale', 'ricevuta', 'scontrino', 'assicurazione']
        for bl in BLACKLIST:
            if bl in text:
                _log(f"[OCR-DEBUG] ✗ BLACKLIST: '{bl}' → lo\u1ea1i")
                return False, ""

        # ─────────────────────────────────────────────────────
        # QUY\u1ebeT \u0110\u1ecaNH
        # ─────────────────────────────────────────────────────
        _log(f"[OCR-DEBUG] Score BACK={score_back} {reasons_back}")
        _log(f"[OCR-DEBUG] Score FRONT={score_front} {reasons_front}")

        # BACK \u01b0u ti\u00ean h\u01a1n (score cao h\u01a1n ho\u1eb7c barcode/MRZ r\u00f5 r\u00e0ng)
        if score_back >= THRESHOLD_BACK and score_back >= score_front:
            _log(f"[OCR-DEBUG] ✓ K\u1ebft qu\u1ea3: BACK (score={score_back})")
            return True, "BACK"

        if score_front >= THRESHOLD_FRONT:
            # FRONT b\u1eaft bu\u1ed9c ph\u1ea3i c\u00f3 face (tr\u00e1nh nh\u1eadn nh\u00e0m document v\u0103n b\u1ea3n)
            if features.get('faces', 0) == 0:
                _log(f"[OCR-DEBUG] ✗ FRONT score={score_front} \u0111\u1ee7 nh\u01b0ng KH\u00d4NG C\u00d3 FACE → lo\u1ea1i")
                return False, ""
            _log(f"[OCR-DEBUG] ✓ K\u1ebft qu\u1ea3: FRONT (score={score_front})")
            return True, "FRONT"

        _log(f"[OCR-DEBUG] ✗ Kh\u00f4ng \u0111\u1ee7 \u0111i\u1ec1u ki\u1ec7n: BACK={score_back}<{THRESHOLD_BACK}, FRONT={score_front}<{THRESHOLD_FRONT}")
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
