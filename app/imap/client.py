from __future__ import annotations

import contextlib
import os
import ssl
from typing import Iterator

from imapclient import IMAPClient

from app.config import AccountConfig, Settings


@contextlib.contextmanager
def imap_connection(account: AccountConfig) -> Iterator[IMAPClient]:
    settings = Settings()
    timeout = settings.imap_timeout_seconds

    if account.imap_tls == "ssl":
        client = IMAPClient(account.imap_host, account.imap_port, ssl=True, timeout=timeout)
    else:
        client = IMAPClient(account.imap_host, account.imap_port, ssl=False, timeout=timeout)
        client.starttls(ssl_context=ssl.create_default_context())

    try:
        client.login(account.imap_username, os.environ[account.imap_password_env])
        yield client
    finally:
        try:
            client.logout()
        except Exception:
            pass
