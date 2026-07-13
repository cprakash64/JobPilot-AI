"""Validate an official employer application URL before we open or store it.

Only http(s) URLs with a real host are allowed. Unsafe schemes
(``javascript:``, ``data:``, ``file:``, etc.) and the placeholder/demo hosts the
discovery layer already guards against are rejected, so a malicious or broken URL
can never be opened on the user's behalf.
"""

from __future__ import annotations

from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}

# Hosts that indicate a demo/placeholder rather than a real employer application.
_BLOCKED_HOST_FRAGMENTS = (
    "example.com",
    "example.org",
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "test.com",
    "demo.com",
    "placeholder",
)


class InvalidApplicationURL(ValueError):
    """Raised when an application URL is missing, malformed, or unsafe."""


def validate_official_url(url: str | None) -> str:
    """Return the URL if it is a safe, real http(s) application link, else raise."""
    if not url or not isinstance(url, str):
        raise InvalidApplicationURL("This job has no official application URL.")
    candidate = url.strip()
    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise InvalidApplicationURL("Application URL must use http or https.")
    host = (parsed.hostname or "").lower()
    if not host or "." not in host:
        raise InvalidApplicationURL("Application URL has no valid host.")
    low = candidate.lower()
    if any(fragment in host or fragment in low for fragment in _BLOCKED_HOST_FRAGMENTS):
        raise InvalidApplicationURL("Application URL points to a placeholder/demo host.")
    return candidate


def is_valid_official_url(url: str | None) -> bool:
    try:
        validate_official_url(url)
        return True
    except InvalidApplicationURL:
        return False
