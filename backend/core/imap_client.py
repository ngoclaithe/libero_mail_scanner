"""
Thread-safe IMAP4 client that connects via SOCKS5 proxy.

Root cause of WinError 10057:
  The old code globally patched `socket.socket = lambda ...`.
  With 10 concurrent threads each overwriting the global, connections
  raced and ended up with invalid/disconnected sockets.

Fix:
  Subclass imaplib.IMAP4 and override `open()` to build a per-thread,
  per-connection SOCKS5 socket — zero global state touched.
"""

import imaplib
import ssl
from typing import Optional

from core.proxy_pool import ProxyInfo, ProxyPool
from core.config import IMAP_HOST, IMAP_PORT, IMAP_TIMEOUT


class ImapClient(imaplib.IMAP4):
    """
    Drop-in replacement for imaplib.IMAP4_SSL that connects via SOCKS5.
    Each instance owns its own socket — fully thread-safe.
    """

    def __init__(
        self,
        host:        str            = IMAP_HOST,
        port:        int            = IMAP_PORT,
        proxy:       Optional[ProxyInfo] = None,
        ssl_context: Optional[ssl.SSLContext] = None,
    ):
        self._proxy   = proxy
        self._ssl_ctx = ssl_context or ssl.create_default_context()
        # super().__init__ calls self.open() internally
        super().__init__(host, port)

    # ── Override open() ───────────────────────────────────────
    # imaplib.IMAP4.__init__ calls self.open(host, port).
    # We intercept here to inject SOCKS5 + SSL without touching globals.

    def open(self, host: str, port: int = IMAP_PORT, timeout=None):
        self.host = host
        self.port = port

        raw_sock = ProxyPool.make_connection(
            host, port,
            proxy=self._proxy,
            timeout=timeout or IMAP_TIMEOUT,
        )

        # Wrap in TLS
        self.sock = self._ssl_ctx.wrap_socket(raw_sock, server_hostname=host)
        self.file = self.sock.makefile("rb")

    # ── read / readline shims (required by IMAP4) ─────────────

    def read(self, size: int) -> bytes:
        return self.file.read(size)

    def readline(self) -> bytes:
        return self.file.readline()

    def send(self, data: bytes):
        self.sock.sendall(data)

    def shutdown(self):
        try:
            self.sock.shutdown(ssl.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


def new_client(proxy: Optional[ProxyInfo] = None) -> ImapClient:
    """Factory: create a fresh ImapClient (optionally via proxy)."""
    return ImapClient(proxy=proxy)
