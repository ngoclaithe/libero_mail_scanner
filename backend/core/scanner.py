"""
Scanner: orchestrates the ThreadPoolExecutor.
Reads accounts.csv, loads ProxyPool, dispatches workers.
"""

import csv
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from core.config import MAX_WORKERS, PROXY_FILE, ACCOUNTS_FILE, OUTPUT_DIR
from core.proxy_pool import ProxyPool
from core.state import state
from core.worker import run_account
try:
    from core.classifier import classifier
except ImportError:
    pass


class Scanner:

    def __init__(self):
        self._stop      = threading.Event()
        self._thread:   Optional[threading.Thread] = None
        self.pool:      Optional[ProxyPool]        = None
        self._acc_file: str = ACCOUNTS_FILE
        self._reload_pool()

    # ── Public API ────────────────────────────────────────────

    def set_accounts_file(self, path: str):
        self._acc_file = path

    def accounts_preview(self) -> list:
        return [a["email"] for a in self._load_accounts()]

    def start(self) -> tuple[bool, str]:
        if state.status == "running":
            return False, "Already running"

        accounts = self._load_accounts()
        if not accounts:
            return False, f"No accounts found in {self._acc_file}"

        self._stop.clear()
        self._reload_pool()   # fresh proxy statuses on each run
        state.reset()
        state.set_status("running")
        state.init_accounts([a["email"] for a in accounts])
        OUTPUT_DIR.mkdir(exist_ok=True)
        try:
            classifier.start()
        except NameError:
            pass

        self._thread = threading.Thread(
            target=self._run,
            args=(accounts,),
            daemon=True,
            name="scanner-main",
        )
        self._thread.start()
        return True, f"Started — {len(accounts)} accounts, {len(self.pool)} proxies"

    def stop(self):
        self._stop.set()
        state.set_status("stopped")
        try:
            classifier.stop()
        except NameError:
            pass

    def get_state(self) -> dict:
        snap = state.snapshot()
        snap["proxies"] = self.pool.all_info() if self.pool else []
        return snap

    # ── Runner ────────────────────────────────────────────────

    def _run(self, accounts: list):
        with ThreadPoolExecutor(
            max_workers=MAX_WORKERS,
            thread_name_prefix="worker",
        ) as executor:
            futures = {
                executor.submit(run_account, acc, self.pool, self._stop): acc["email"]
                for acc in accounts
            }
            for fut in as_completed(futures):
                if self._stop.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                try:
                    fut.result()
                except Exception as e:
                    email = futures[fut]
                    state.update_account(email, status="failed", error=str(e))
                    state.inc("accounts_failed")

        if not self._stop.is_set():
            state.set_status("done")
        try:
            classifier.stop()
        except NameError:
            pass

    # ── Helpers ───────────────────────────────────────────────

    def _reload_pool(self):
        try:
            self.pool = ProxyPool(PROXY_FILE)
        except Exception as e:
            print(f"[WARN] Proxy file error: {e}")
            self.pool = None

    def _load_accounts(self) -> list:
        path = Path(self._acc_file)
        if not path.exists():
            return []
        rows = []
        with open(path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
            
        if not lines:
            return rows
            
        # Try CSV first if it has header
        first_line = lines[0].lower()
        if "email" in first_line and "password" in first_line:
            import csv
            reader = csv.DictReader(lines)
            for row in reader:
                email = (row.get("email") or row.get("Email") or "").strip()
                pwd   = (row.get("password") or row.get("Password") or "").strip()
                if email and pwd:
                    rows.append({"email": email, "password": pwd})
            return rows

        # Fallback: plain text, split by first ":" only (email:password)
        for line in lines:
            if ':' not in line:
                continue
            email, pwd = line.split(':', 1)
            email, pwd = email.strip(), pwd.strip()
            if email and pwd:
                rows.append({"email": email, "password": pwd})
        return rows


# Singleton used by FastAPI
scanner = Scanner()
