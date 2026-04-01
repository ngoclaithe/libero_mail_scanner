import threading
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

import socks

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

    def __len__(self) -> int:
        return len(self._proxies)

    @staticmethod
    def make_connection(host: str, port: int,
                        proxy: Optional[ProxyInfo],
                        timeout: int = 30):
        if proxy:
            import socket
            import base64
            sock = socket.create_connection((proxy.host, proxy.port), timeout=timeout)
            auth = base64.b64encode(f"{proxy.username}:{proxy.password}".encode()).decode()
            req = (f"CONNECT {host}:{port} HTTP/1.1\r\n"
                   f"Host: {host}:{port}\r\n"
                   f"Proxy-Authorization: Basic {auth}\r\n\r\n")
            sock.sendall(req.encode())
            
            resp = b""
            while b"\r\n\r\n" not in resp:
                c = sock.recv(1024)
                if not c: break
                resp += c
                
            if not resp.startswith(b"HTTP/1.") or b" 200" not in resp.split(b"\r\n")[0]:
                sock.close()
                raise Exception(f"Proxy rejected (407?): {resp[:50].decode(errors='ignore')}")
        else:
            import socket
            sock = socket.create_connection((host, port), timeout=timeout)
        return sock
