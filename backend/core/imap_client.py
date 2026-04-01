"""
Thread-safe IMAP4 client with proxy support.

Supports 3 connection modes:
  - SSL    : port 993, direct TLS wrap (default)
  - STARTTLS: port 143, plain connect then STARTTLS upgrade
  - AUTH_PLAIN: use AUTHENTICATE PLAIN instead of LOGIN

Each instance owns its own socket — fully thread-safe.
"""

import imaplib
import ssl
import base64
from typing import Optional

from core.proxy_pool import ProxyInfo, ProxyPool
from core.config import IMAP_HOST, IMAP_PORT, IMAP_TIMEOUT


class ImapClient(imaplib.IMAP4):
    """
    IMAP4 client over SSL (port 993) via HTTP CONNECT proxy.
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
        super().__init__(host, port)

    def open(self, host: str, port: int = IMAP_PORT, timeout=None):
        self.host = host
        self.port = port
        raw_sock = ProxyPool.make_connection(
            host, port,
            proxy=self._proxy,
            timeout=timeout or IMAP_TIMEOUT,
        )
        self.sock = self._ssl_ctx.wrap_socket(raw_sock, server_hostname=host)
        self.file = self.sock.makefile("rb")

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


class ImapClientStartTLS(imaplib.IMAP4):
    """
    IMAP4 client over STARTTLS (port 143) via HTTP CONNECT proxy.
    Connect plain → issue STARTTLS → upgrade to TLS.
    """

    def __init__(
        self,
        host:        str            = IMAP_HOST,
        port:        int            = 143,
        proxy:       Optional[ProxyInfo] = None,
        ssl_context: Optional[ssl.SSLContext] = None,
    ):
        self._proxy   = proxy
        self._ssl_ctx = ssl_context or ssl.create_default_context()
        self._raw_sock = None
        super().__init__(host, port)

    def open(self, host: str, port: int = 143, timeout=None):
        self.host = host
        self.port = port
        self._raw_sock = ProxyPool.make_connection(
            host, port,
            proxy=self._proxy,
            timeout=timeout or IMAP_TIMEOUT,
        )
        # Plain socket (no TLS yet)
        self.sock = self._raw_sock
        self.file = self.sock.makefile("rb")

    def starttls_upgrade(self):
        """Send STARTTLS and upgrade the connection to TLS."""
        typ, data = self._simple_command('STARTTLS')
        if typ != 'OK':
            raise imaplib.IMAP4.error(f"STARTTLS failed: {data}")
        # Upgrade socket to TLS
        self.sock = self._ssl_ctx.wrap_socket(self._raw_sock, server_hostname=self.host)
        self.file = self.sock.makefile("rb")
        # Re-read capabilities after TLS
        typ, data = self.capability()
        self._cap_result = data

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


def authenticate_plain(mail, email: str, password: str):
    """
    Use AUTHENTICATE PLAIN instead of LOGIN command.
    Some servers treat these differently for policy checks.
    """
    auth_string = f"\x00{email}\x00{password}"
    encoded = base64.b64encode(auth_string.encode()).decode()

    typ, data = mail.authenticate('PLAIN', lambda x: auth_string.encode())
    return typ, data


def new_client(proxy: Optional[ProxyInfo] = None, mode: str = "ssl") -> imaplib.IMAP4:
    """
    Factory: create a fresh IMAP client.
    mode='ssl'      → port 993, direct SSL (default)
    mode='starttls'  → port 143, STARTTLS upgrade
    """
    if mode == "starttls":
        client = ImapClientStartTLS(proxy=proxy)
        client.starttls_upgrade()
        return client
    else:
        return ImapClient(proxy=proxy)
