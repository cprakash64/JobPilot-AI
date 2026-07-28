"""SSRF-safe HTTP fetch for company-branding assets (logo proxy, website-
metadata discovery). Every caller that fetches a URL derived from
user/company-controlled data (a domain, a catalog entry) MUST go through this
module — never call httpx/requests directly for that purpose.

Defenses:
  - only http(s) schemes;
  - the resolved IP (post-DNS) must not be private/loopback/link-local/
    multicast/reserved, or the AWS/GCP metadata address (169.254.169.254);
  - the connection is PINNED to the exact IP that was validated, so a hostile
    resolver cannot answer the validation query with a public address and the
    connection query with 127.0.0.1 (DNS rebinding / TOCTOU);
  - redirects are followed manually, one at a time, re-validating and
    re-pinning the target on every hop (so a safe URL cannot redirect to an
    unsafe one), with loop detection and a hard hop limit;
  - a hard timeout and a hard response-size cap enforced WHILE streaming, so a
    hostile server cannot make us buffer an unbounded body;
  - the response Content-Type must be an image type for asset fetches.

Errors are deliberately coarse (UnsafeUrlError / FetchFailedError). Their
messages may embed the upstream URL, so callers must log them rather than
forward them to a client — see routes/jobs.py.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

MAX_REDIRECTS = 3
DEFAULT_TIMEOUT_SECONDS = 4.0
MAX_BYTES = 2 * 1024 * 1024  # 2MB — generous for a favicon/logo, not for a hostile payload
ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_IMAGE_CONTENT_TYPES = (
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
    "image/x-icon",
    "image/vnd.microsoft.icon",
)
# SVG is intentionally excluded from "safe to serve as-is" — it can carry
# script. Callers that accept SVG must rasterize it first.

REDIRECT_STATUSES = (301, 302, 303, 307, 308)


@dataclass
class SafeFetchResult:
    content: bytes
    content_type: str
    final_url: str


class UnsafeUrlError(ValueError):
    pass


class FetchFailedError(RuntimeError):
    pass


def _is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    ):
        return False
    # Cloud metadata endpoint — always block regardless of the above checks.
    if str(addr) == "169.254.169.254":
        return False
    # IPv6-mapped IPv4 (::ffff:127.0.0.1) is judged on the embedded v4 address,
    # which the flags above do not reliably catch.
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None and not _is_public_ip(str(mapped)):
        return False
    return True


def _resolve_public_ips(hostname: str) -> list[str]:
    """Resolve `hostname`, requiring EVERY returned address to be public.

    Rejecting outright on any non-public answer (rather than filtering down to
    the public ones) means a split-horizon or partially-poisoned DNS response
    cannot be narrowed into a usable result.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise UnsafeUrlError("DNS resolution failed") from exc
    if not infos:
        raise UnsafeUrlError("DNS resolution returned no addresses")

    ips: list[str] = []
    for info in infos:
        ip = info[4][0]
        if not _is_public_ip(ip):
            raise UnsafeUrlError(f"target resolves to a non-public address ({ip})")
        if ip not in ips:
            ips.append(ip)
    return ips


def _validate_url(url: str) -> list[str]:
    """Validate scheme + host, returning the validated public IP list.

    The module's validation entry point — tests and callers import this name.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"unsupported scheme: {parsed.scheme or '(none)'}")
    if not parsed.hostname:
        raise UnsafeUrlError("no hostname")
    return _resolve_public_ips(parsed.hostname)


def _pinned_request_url(url: str, ip: str) -> str:
    """Rewrite `url` to address the validated IP directly, preserving path and
    query.

    The original hostname still travels as the Host header and the TLS SNI /
    certificate name, so this changes only WHICH address we connect to — never
    who we claim to be talking to, and TLS verification is unaffected.
    """
    parsed = urlparse(url)
    host_part = f"[{ip}]" if ipaddress.ip_address(ip).version == 6 else ip
    netloc = f"{host_part}:{parsed.port}" if parsed.port else host_part
    path = parsed.path or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{parsed.scheme}://{netloc}{path}{query}"


def _read_capped(response: httpx.Response, *, max_bytes: int = MAX_BYTES) -> bytes:
    """Stream the body, aborting as soon as it exceeds MAX_BYTES.

    Never buffers an unbounded response, regardless of how (or whether) the
    server declares Content-Length.
    """
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise FetchFailedError("response exceeds size limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _safe_fetch(
    url: str,
    *,
    allowed_content_types: tuple[str, ...],
    max_bytes: int,
    timeout_seconds: float,
) -> SafeFetchResult:
    current = url
    seen: set[str] = set()

    with httpx.Client(follow_redirects=False, timeout=timeout_seconds) as client:
        for _ in range(MAX_REDIRECTS + 1):
            if current in seen:
                raise FetchFailedError("redirect loop")
            seen.add(current)

            ips = _validate_url(current)
            parsed = urlparse(current)
            host_header = parsed.netloc  # keeps the port when non-default
            sni_host = parsed.hostname or ""

            try:
                request = client.build_request(
                    "GET",
                    _pinned_request_url(current, ips[0]),
                    headers={"Host": host_header},
                    extensions={"sni_hostname": sni_host},
                )
                resp = client.send(request, stream=True)
            except httpx.HTTPError as exc:
                # Timeouts, connect errors, TLS failures. Only the exception
                # TYPE is reported; the original message can embed the URL.
                raise FetchFailedError(type(exc).__name__) from exc

            try:
                if resp.status_code in REDIRECT_STATUSES:
                    location = resp.headers.get("location")
                    if not location:
                        raise FetchFailedError(
                            f"redirect ({resp.status_code}) with no Location header"
                        )
                    try:
                        # Resolves relative ("/logo.png"), absolute, and
                        # protocol-relative ("//host/logo.png") targets against
                        # the CURRENT url. httpx 0.27 has no URL.human_repr();
                        # str() is the supported serialization.
                        current = str(httpx.URL(current).join(location))
                    except (httpx.InvalidURL, ValueError, TypeError) as exc:
                        raise FetchFailedError("malformed redirect Location") from exc
                    continue

                if resp.status_code != 200:
                    raise FetchFailedError(f"unexpected status {resp.status_code}")

                raw_type = resp.headers.get("content-type") or ""
                content_type = raw_type.split(";")[0].strip().lower()
                if content_type not in allowed_content_types:
                    raise FetchFailedError(f"unsupported content-type: {content_type or '(none)'}")

                content = _read_capped(resp, max_bytes=max_bytes)
                if len(content) == 0:
                    raise FetchFailedError("empty response")
                return SafeFetchResult(
                    content=content, content_type=content_type, final_url=current
                )
            finally:
                resp.close()

    raise FetchFailedError("too many redirects")


def safe_fetch_image(
    url: str, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
) -> SafeFetchResult:
    """Fetch a bounded raster image through the SSRF-safe transport."""
    return _safe_fetch(
        url,
        allowed_content_types=ALLOWED_IMAGE_CONTENT_TYPES,
        max_bytes=MAX_BYTES,
        timeout_seconds=timeout_seconds,
    )


def safe_fetch_html(
    url: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = 1024 * 1024,
) -> SafeFetchResult:
    """Fetch bounded public HTML for official-site branding discovery."""
    return _safe_fetch(
        url,
        allowed_content_types=("text/html", "application/xhtml+xml"),
        max_bytes=max_bytes,
        timeout_seconds=timeout_seconds,
    )
