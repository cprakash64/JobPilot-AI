from __future__ import annotations

# ruff: noqa: E501
import base64
import hashlib
import hmac
from urllib.parse import urlparse, urlunparse

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _material() -> str:
    return settings.people_data_encryption_key or settings.secret_key


def encrypt_email(email: str) -> str:
    key = base64.urlsafe_b64encode(hashlib.sha256(f"people-encryption:{_material()}".encode()).digest())
    return Fernet(key).encrypt(email.lower().strip().encode()).decode()


def decrypt_email(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    key = base64.urlsafe_b64encode(hashlib.sha256(f"people-encryption:{_material()}".encode()).digest())
    try:
        return Fernet(key).decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError, UnicodeError):
        return None


def email_hash(email: str) -> str:
    return hmac.new(
        hashlib.sha256(f"people-hash:{_material()}".encode()).digest(),
        email.lower().strip().encode(),
        hashlib.sha256,
    ).hexdigest()


def safe_profile_url(value: str | None) -> str | None:
    if not value or len(value) > 1000:
        return None
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    host = parsed.hostname.lower().strip(".")
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return None
    if not parsed.path.lower().startswith("/in/"):
        return None
    return urlunparse(("https", host, parsed.path.rstrip("/"), "", "", ""))


def is_professional_email(email: str, company_domain: str) -> bool:
    value = email.lower().strip()
    if "@" not in value:
        return False
    local, domain = value.rsplit("@", 1)
    personal = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "proton.me"}
    return bool(local) and domain == company_domain.lower() and domain not in personal
