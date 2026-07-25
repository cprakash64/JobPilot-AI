"""Encryption boundary for optional employer-portal credentials.

Plaintext is accepted only by the dedicated write endpoint and returned only to
an authenticated, session-scoped Workday extension request. It is never placed
in profile JSON, logs, application snapshots, or reusable answer-vault rows.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _fernet() -> Fernet:
    material = settings.workday_credentials_encryption_key or settings.secret_key
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_workday_password(password: str) -> str:
    value = password.strip()
    if not value:
        raise ValueError("Password cannot be blank")
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_workday_password(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        return None
