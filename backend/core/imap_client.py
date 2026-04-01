
import imaplib
import ssl
import base64
from typing import Optional

from core.proxy_pool import ProxyInfo, ProxyPool
from core.config import IMAP_HOST, IMAP_PORT, IMAP_TIMEOUT

class ImapClient(imaplib.IMAP4):

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
        self.sock.settimeout(timeout or IMAP_TIMEOUT)
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
        self.sock = self._raw_sock
        self.file = self.sock.makefile("rb")

    def starttls_upgrade(self):
        typ, data = self._simple_command('STARTTLS')
        if typ != 'OK':
            raise imaplib.IMAP4.error(f"STARTTLS failed: {data}")
        self.sock = self._ssl_ctx.wrap_socket(self._raw_sock, server_hostname=self.host)
        self.sock.settimeout(IMAP_TIMEOUT)
        self.file = self.sock.makefile("rb")
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
    auth_string = f"\x00{email}\x00{password}"
    encoded = base64.b64encode(auth_string.encode()).decode()

    typ, data = mail.authenticate('PLAIN', lambda x: auth_string.encode())
    return typ, data

def new_client(proxy: Optional[ProxyInfo] = None, mode: str = "ssl") -> imaplib.IMAP4:
    if mode == "starttls":
        client = ImapClientStartTLS(proxy=proxy)
        client.starttls_upgrade()
        return client
    else:
        return ImapClient(proxy=proxy)
