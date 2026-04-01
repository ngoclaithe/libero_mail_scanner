import copy
import threading
from datetime import datetime

class AppState:

    def __init__(self, user_id=0):
        self.user_id = user_id
        self._lock = threading.RLock()
        self._d    = self._blank()

    def reset(self):
        with self._lock:
            self._d = self._blank()

    def set_status(self, status: str):
        with self._lock:
            self._d["status"] = status
            if status == "running":
                self._d["started_at"] = datetime.now().isoformat()
            elif status in ("done", "stopped"):
                self._d["ended_at"] = datetime.now().isoformat()

    def init_accounts(self, emails: list):
        with self._lock:
            self._d["totals"]["accounts_total"] = len(emails)
            for email in emails:
                self._d["accounts"][email] = {
                    "status":       "pending",
                    "proxy":        None,
                    "thread":       None,
                    "total_mail":   0,
                    "processed":    0,
                    "images_found": 0,
                    "last_file":    None,
                    "error":        None,
                    "document_found": False,
                }

    def update_account(self, email: str, **kw):
        with self._lock:
            if email in self._d["accounts"]:
                self._d["accounts"][email].update(kw)

    
    def add_ai_log(self, text: str):
        with self._lock:
            ts = datetime.now().strftime("%H:%M:%S")
            self._d["ai_logs"].append(f"[{ts}] {text}")
            if len(self._d["ai_logs"]) > 100:
                self._d["ai_logs"].pop(0)

    def inc(self, key: str, amount: int = 1):
        with self._lock:
            self._d["totals"][key] = self._d["totals"].get(key, 0) + amount

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._d)

    @property
    def status(self) -> str:
        with self._lock:
            return self._d["status"]
            
    @property
    def accounts(self) -> dict:
        with self._lock:
            return self._d.get("accounts", {})

    @staticmethod
    def _blank() -> dict:
        return {
            "status":     "idle",
            "started_at": None,
            "ended_at":   None,
            "ai_logs":    [],
            "accounts":   {},
            "totals": {
                "accounts_total":  0,
                "accounts_done":   0,
                "accounts_failed": 0,
                "images_total":    0,
                "documents_found": 0,
            },
        }

state = AppState()
