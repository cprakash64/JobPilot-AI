"""Deterministic server-side ATS hint from the application URL.

This is a coarse hint used for the session record and analytics. The extension
performs the authoritative, DOM-based detection on the live page.
"""

from __future__ import annotations

from urllib.parse import urlparse

_HOST_SIGNATURES: tuple[tuple[str, str], ...] = (
    ("greenhouse.io", "greenhouse"),
    ("boards.greenhouse.io", "greenhouse"),
    ("job-boards.greenhouse.io", "greenhouse"),
    ("lever.co", "lever"),
    ("jobs.lever.co", "lever"),
    ("ashbyhq.com", "ashby"),
    ("jobs.ashbyhq.com", "ashby"),
    ("myworkdayjobs.com", "workday"),
    ("workday.com", "workday"),
)


def detect_ats_from_url(url: str | None) -> str:
    """Return one of: greenhouse, lever, ashby, workday, unknown."""
    if not url:
        return "unknown"
    host = (urlparse(url).hostname or "").lower()
    for fragment, ats in _HOST_SIGNATURES:
        if host == fragment or host.endswith("." + fragment) or fragment in host:
            return ats
    return "unknown"
