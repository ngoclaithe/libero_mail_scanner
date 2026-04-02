import threading
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class ProxyStatus(str, Enum):
    ACTIVE       = "active"
    RATE_LIMITED = "rate_limited"
    BLOCKED      = "blocked"
    DEAD         = "dead"

@dataclass
class ProxyInfo:
    host:     str
    port:     int
    username: str
    password: str
    status:   ProxyStatus      = ProxyStatus.ACTIVE
    used_by:  Optional[str]    = None
    requests: int              = 0
    errors:   int              = 0
    last_error: Optional[str]  = None
    last_used: Optional[datetime] = None

    @property
    def id(self) -> str:
        return f"{self.host}:{self.port}"

    def to_dict(self) -> dict:
        return {
            "id":         self.id,
            "host":       self.host,
            "port":       self.port,
            "status":     self.status.value,
            "used_by":    self.used_by,
            "requests":   self.requests,
            "errors":     self.errors,
            "last_error": self.last_error,
            "last_used":  self.last_used.isoformat() if self.last_used else None,
        }

class ProxyPool:

    def __init__(self, filepath: str):
        self._lock    = threading.Lock()
        self._proxies: list[ProxyInfo] = []
        self._idx     = 0
        self._parse(filepath)

    def _parse(self, path: str):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(":")
                if len(parts) >= 4:
                    host, port, user, pwd = parts[0], parts[1], parts[2], parts[3]
                    self._proxies.append(
                        ProxyInfo(host=host, port=int(port),
                                  username=user, password=pwd)
                    )

    def acquire(self, account: str) -> Optional[ProxyInfo]:
        with self._lock:
            available = [
                p for p in self._proxies
                if p.status in (ProxyStatus.ACTIVE, ProxyStatus.RATE_LIMITED)
                and p.used_by is None
            ]
            if not available:
                return None
            proxy = available[self._idx % len(available)]
            self._idx += 1
            proxy.used_by   = account
            proxy.requests += 1
            proxy.last_used = datetime.now()
            return proxy

    def release(self, proxy: ProxyInfo):
        with self._lock:
            proxy.used_by = None

    def mark_rate_limited(self, proxy: ProxyInfo, error: str = ""):
        self._mark(proxy, ProxyStatus.RATE_LIMITED, error)

    def mark_blocked(self, proxy: ProxyInfo, error: str = ""):
        self._mark(proxy, ProxyStatus.BLOCKED, error)

    def mark_dead(self, proxy: ProxyInfo, error: str = ""):
        self._mark(proxy, ProxyStatus.DEAD, error)

    def _mark(self, proxy: ProxyInfo, status: ProxyStatus, error: str):
        with self._lock:
            proxy.status     = status
            proxy.errors    += 1
            proxy.last_error = error
            proxy.used_by    = None

    def all_info(self) -> list[dict]:
        with self._lock:
            return [p.to_dict() for p in self._proxies]

    def acquire_multiple(self, account: str, count: int = 1) -> list:
        with self._lock:
            available = [
                p for p in self._proxies
                if p.status in (ProxyStatus.ACTIVE, ProxyStatus.RATE_LIMITED)
                and p.used_by is None
            ]
            acquired = []
            for p in available[:count]:
                p.used_by = account
                p.requests += 1
                p.last_used = datetime.now()
                acquired.append(p)
            return acquired

    def count_free(self) -> int:
        with self._lock:
            return sum(1 for p in self._proxies
                       if p.status in (ProxyStatus.ACTIVE, ProxyStatus.RATE_LIMITED)
                       and p.used_by is None)

    def __len__(self) -> int:
        return len(self._proxies)
