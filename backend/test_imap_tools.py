from core.imap_client import ImapClient
from imap_tools import MailBox

class ProxyMailBox(MailBox):
    def __init__(self, host, port, proxy=None):
        self.host = host
        self.port = port
        self.client = ImapClient(host, port, proxy)

print("ProxyMailBox init successful")
