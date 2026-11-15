"""PII protection at rest.

* Phone numbers are matched, never read back: we store an HMAC-SHA256 of the canonical
  number (keyed, so the 10^9 possible numbers cannot be brute-forced without the key) plus
  the last 4 digits for report masking.
* Other PII fields (e.g. customer_name) are encrypted with Fernet (AES-128-CBC + HMAC).

Key management: LEDGER_PII_KEY (urlsafe base64, 32 bytes) if set, else a key file next to
the database, created on first use with mode 0600. The key file is a local-dev convenience;
in production the key comes from a KMS / secret manager, never from the same disk as the DB.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from pathlib import Path

from cryptography.fernet import Fernet


def _load_master_key(key_path: Path) -> bytes:
    env = os.environ.get("LEDGER_PII_KEY")
    if env:
        return base64.urlsafe_b64decode(env.encode())
    if key_path.exists():
        return base64.urlsafe_b64decode(key_path.read_bytes().strip())
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key = os.urandom(32)
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(base64.urlsafe_b64encode(key))
    return key


class PiiVault:
    def __init__(self, key_path: Path):
        master = _load_master_key(key_path)
        # domain-separated sub-keys so the HMAC key and encryption key are independent
        self._hmac_key = hmac.new(master, b"phone-hmac", hashlib.sha256).digest()
        enc_key = hmac.new(master, b"field-encryption", hashlib.sha256).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(enc_key))

    def phone_hash(self, canonical_phone: str) -> str:
        return hmac.new(self._hmac_key, canonical_phone.encode(), hashlib.sha256).hexdigest()

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()
