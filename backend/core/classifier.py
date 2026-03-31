import os
import sys
import time as _time
import shutil
import queue
import threading
import cv2
from pathlib import Path
from email.message import Message

cv2.setNumThreads(1)

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
        self._threads = []
        self._local = threading.local()

    @property
    def reader(self):
        if not hasattr(self._local, "reader"):
            # Limit thread usage inside ONNX so multiple workers can run nicely
            import os
            os.environ["OMP_NUM_THREADS"] = "1"
            self._local.reader = RapidOCR()
        return self._local.reader

    @property
    def face_detector(self):
        if not hasattr(self._local, "face_detector"):
            self._local.face_detector = RetinaFace()
        return self._local.face_detector

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
            
        if self._threads and any(t.is_alive() for t in self._threads):
            _log("[OCR-DEBUG] AI Classifier threads vẫn đang chạy, bỏ qua bước khởi tạo lại model.")
            return

        self._stop.clear()
        self._threads.clear()
        
        _log("[OCR-DEBUG] ═══════════════════════════════════════════")
        _log("[OCR-DEBUG] Khởi tạo AI (RapidONNX + RetinaFace)... Sẽ load độc lập trên mỗi worker thread.")
        _log("[OCR-DEBUG] ═══════════════════════════════════════════")
        
        # CPU VPS có 32GB RAM => Chắc có nhiều luồng, dùng 4 workers là an toàn và đủ nhanh
        num_workers = 4 
        for i in range(num_workers):
            t = threading.Thread(target=self._run, daemon=True, name=f"AI_Classifier_{i}")
            t.start()
            self._threads.append(t)
            
        _log(f"[OCR-DEBUG] ✓ {num_workers} AI Classifier threads đã start!")

    def stop(self):
        self._stop.set()
        # Wake up the queue threads if they are blocking
        for _ in self._threads:
            ai_queue.put(None) 
        for t in self._threads:
            if t.is_alive():
                t.join(timeout=3)

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

        # ── FAST PATH: Image có barcode 1D
        # Vẫn cần chạy OCR thu nhỏ (600px) để check BLACKLIST
        # -- hợp đồng BĐS, Marca da Bollo cũng có barcode 1D nhưng phải bị loại
        if features.get('has_barcode') and 'image' in mime:
            _log(f"[OCR-DEBUG] ⚡ FAST PATH barcode: chạy OCR nhẹ 600px để check BLACKLIST...")
            text = self._layer3_extract_text_light(path, mime)
            ocr_elapsed = 0.0
        else:
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
                    max_dim = 800  # Giảm từ 1200→800 để RapidOCR nhanh hơn ~2x
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

    def _layer3_extract_text_light(self, path: Path, mime: str) -> str:
        """OCR nhẹ 600px — chỉ dùng để check BLACKLIST khi image đã có barcode.
        Nhanh hơn ~2x so với full OCR 800px. Không cần độ chính xác cao."""
        text = ""
        try:
            img = cv2.imread(str(path))
            if img is None:
                return ""
            h, w = img.shape[:2]
            max_dim = 600
            if max(h, w) > max_dim:
                scale = max_dim / max(h, w)
                img = cv2.resize(img, (int(w*scale), int(h*scale)))
            ocr_results, _ = self.reader(img)
            if ocr_results:
                txt_list = [item[1] for item in ocr_results if item[2] > 0.3]
                text = " ".join(txt_list).lower()
                _log(f"[OCR-DEBUG]   Light OCR: {len(txt_list)} đoạn, {len(text)} ký tự")
        except Exception as e:
            _log(f"[OCR-DEBUG] ✗ Light OCR FAILED: {e}")
        return text

    def _evaluate_text_and_features(self, text: str, features: dict, mime: str) -> tuple[bool, str]:
        import re


        txt = text.lower()

        # ── BLACKLIST ưu tiên check trước ─────────────────────────
        # Bat ky tu nao xuat hien -> loai ngay, khong can check gi them
        BLACKLIST = [
            # Giay to thue / tai chinh
            'modello 730', 'redditi 20', 'agenzia entrate', 'codice fiscale del contribuente',
            'dichiarazione dei redditi', 'quadro a', 'quadro b', 'sostituto d\'imposta',
            'contratto', 'fattura', 'bolletta', 'preventivo', 'ricevuta',
            'estratto conto', 'estratto.conto', 'conto corrente', 'bonifico',
            'ordinante', 'scontrino', 'assicurazione', 'catastale', 'catasto', 'planimetria',
            'ministero delle finanze', 'totale euro',
            # INPS / ISEE / Thue (khong phai CCCD)
            'attestazione isee', 'isee ordinario', 'dichiarazione sostitutiva',
            'nucleo familiare', 'inps', 'prestazioni agevolate',
            'dati previdenziali', 'uniemens', 'percipiente', 'sostituto d\'imposta',
            # Y te / benh vien / toa thuoc
            'pronto soccorso', 'ospedale', 'laboratorio di', 'esito test',
            'antigenico', 'tampone', 'referto', 'paziente', 'medico radiologo',
            'radiolog', 'emoglobina', 'piastrine', 'glicemia', 'colesterolo',
            'ematologia', 'biochimica clinica', 'data esame', 'data risultato',
            'sistema ts', 'codice fiscale assistito', 'ricetta elettronica',
            'servizio sanitario nazionale', 'promemoria per l\'assistito', 'casa di cura',
            # Attestati / chung chi
            'attestato', 'corso di formazione', 'ha partecipato', 'si attesta che',
            # Bat dong san / hop dong (co ma vach I25 nhung KHONG phai CCCD)
            'compravendita', 'promette di vendere', 'promette di acquistare',
            'preliminare di', 'contratto preliminare', 'rogito', 'notarile',
            'immobile sito', 'particella', 'mappale',
            # Vien thong / Giao nhan / SIM (co ma vach nhung khong phai CCCD)
            'nuova sim', 'tim card', 'vodafone', 'iliad', 'wind tre', 'fastweb',
            'piano tariffario', 'offerta tariffaria', 'dhl', 'waybill', 'express worldwide',
            'spedizione', 'corriere', 'lettera di vettura',
            # Khac
            'azienda sanitaria', 'asp di', 'certificato medico',
            'dgc', 'covid-19', 'vaccinazione', 'dose',
        ]
        for bl in BLACKLIST:
            if bl in txt:
                _log(f"[OCR-DEBUG] x BLACKLIST: '{bl}' -> loai")
                return False, ""

        THRESHOLD_BACK  = 4   # can hard signal (barcode/MRZ) + them gi do nua
        THRESHOLD_FRONT = 3

        score_back  = 0
        score_front = 0
        reasons_back  = []
        reasons_front = []

        # ── BACK SIGNALS ─────────────────────────────────────────

        # [+4] MRZ chinh xac: co << va ITA
        has_ita        = bool(re.search(r'\bita\b|c<ita|itaca', txt))
        has_mrz_arrows = '<<' in text
        mrz_line       = bool(re.search(r'[A-Z0-9<]{20,}', text.upper()))

        if has_mrz_arrows and has_ita:
            score_back += 4
            reasons_back.append('MRZ+ITA (+4)')
        elif has_mrz_arrows and mrz_line:
            score_back += 3
            reasons_back.append('MRZ_line (+3)')

        # [+4] Linear barcode 1D (da loc QR o layer 2.5)
        if features.get('has_barcode'):
            score_back += 4
            reasons_back.append('barcode_1D (+4)')

        # [+1] Codice Fiscale regex -- CHI tinh neu da co hard signal truoc
        # (Codice Fiscale co trong moi giay to Y, khong phai dau hieu rieng CCCD)
        has_hard = features.get('has_barcode') or (has_mrz_arrows and has_ita)
        if has_hard:
            codice_fiscale = re.search(
                r'\b[A-Z]{6}\d{2}[A-EHLMPRST]\d{2}[A-Z]\d{3}[A-Z]\b',
                text.upper()
            )
            if codice_fiscale:
                score_back += 1
                reasons_back.append(f'codice_fiscale:{codice_fiscale.group()} (+1)')

        # [+1] Keywords dac trung mat sau CCCD (chi tinh 1 tu)
        back_kws = ['indirizzo di residenza', 'estremi atto', 'comune di nascita',
                    'luogo di nascita', 'madre', 'padre']
        for kw in back_kws:
            if kw in txt:
                score_back += 1
                reasons_back.append(f'back_kw:{kw} (+1)')
                break

        # ── FRONT SIGNALS ────────────────────────────────────────

        # [+4] "REPUBLICA ITALIANA" / "MINISTERO DELL'INTERNO"
        if re.search(r'repubblica\s+italiana|ministero\s+dell', txt):
            score_front += 4
            reasons_front.append('REPUBLICA_ITALIANA (+4)')

        # [+3] "CARTA DI IDENTITA" / "IDENTITY CARD"
        if re.search(r'carta\s+di\s+identit|identity\s+card', txt):
            score_front += 3
            reasons_front.append('CARTA_IDENTITA (+3)')

        # [+2] So the CIE: CA29739HP (2 chu + 5 so + 2 chu)
        cie_number = re.search(r'\b[A-Z]{2}\d{5}[A-Z]{2}\b', text.upper())
        if cie_number:
            score_front += 2
            reasons_front.append(f'CIE_number:{cie_number.group()} (+2)')

        # [+2] Patente di guida
        if 'patente' in txt and ('guida' in txt or 'driving' in txt):
            score_front += 2
            reasons_front.append('patente_guida (+2)')

        # [+2] Face (mat truoc co anh the)
        if features.get('faces', 0) > 0:
            score_front += 2
            reasons_front.append('face_detected (+2)')

        # [+1/+2] Keywords dac trung mat truoc CCCD/Patente
        # (chi tinh cac kw THAT SU dac trung, khong phai generic)
        front_kws = ['emissione', 'issuing authority', 'scadenza', 'expiry',
                     'sesso', 'statura', 'cittadinanza', 'nationality',
                     'titolare', 'holder', 'valida fino', 'data di nascita']
        hits = sum(1 for kw in front_kws if kw in txt)
        if hits >= 3:
            score_front += 2
            reasons_front.append(f'{hits} front_kws (+2)')
        elif hits >= 1:
            score_front += 1
            reasons_front.append(f'{hits} front_kws (+1)')

        # ── GUARD ────────────────────────────────────────────────
        _log(f"[OCR-DEBUG] Score BACK={score_back} {reasons_back}")
        _log(f"[OCR-DEBUG] Score FRONT={score_front} {reasons_front}")

        is_pdf = "pdf" in mime

        # GUARD 1: PDF khong co barcode/MRZ -> khong the la BACK
        if is_pdf and not has_hard and score_back > 0:
            _log(f"[OCR-DEBUG] x GUARD-PDF: khong co barcode/MRZ -> reset BACK score")
            score_back = 0

        # GUARD 2: Text qua dai + khong co hard signal = van ban dai
        if len(text) > 1500 and not has_hard:
            _log(f"[OCR-DEBUG] x GUARD-LENGTH: {len(text)} ky tu, khong co barcode/MRZ -> loai")
            return False, ""

        # ── QUYET DINH ───────────────────────────────────────────
        if score_back >= THRESHOLD_BACK and score_back >= score_front:
            _log(f"[OCR-DEBUG] + Ket qua: BACK (score={score_back})")
            return True, "BACK"

        if score_front >= THRESHOLD_FRONT:
            if features.get('faces', 0) == 0:
                _log(f"[OCR-DEBUG] x FRONT score={score_front} du nhung KHONG CO FACE -> loai")
                return False, ""
            _log(f"[OCR-DEBUG] + Ket qua: FRONT (score={score_front})")
            return True, "FRONT"

        _log(f"[OCR-DEBUG] x Khong du: BACK={score_back}<{THRESHOLD_BACK}, FRONT={score_front}<{THRESHOLD_FRONT}")
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
