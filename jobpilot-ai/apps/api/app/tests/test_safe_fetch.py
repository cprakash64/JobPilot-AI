"""Section A — SSRF-safe fetch: redirects, scheme/address validation, size,
content-type, timeouts, and leak-free error reporting.

The live failure this pins: `safe_fetch_image` called `httpx.URL(...).join(...)
.human_repr()`, which does not exist in httpx 0.27. Following ANY redirect
raised AttributeError, which the logo endpoint did not catch, so a company
whose logo URL redirected produced an unhandled 500.

Every test here is hermetic: DNS is stubbed and HTTP is served by an
httpx.MockTransport, so no test touches the network.
"""

from __future__ import annotations

import httpx
import pytest

from app.jobs import safe_fetch as sf
from app.jobs.safe_fetch import (
    FetchFailedError,
    UnsafeUrlError,
    _is_public_ip,
    _pinned_request_url,
    _validate_url,
    safe_fetch_image,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64
PUBLIC_IP = "93.184.216.34"


@pytest.fixture()
def public_dns(monkeypatch):
    """Every hostname resolves to one public address."""
    monkeypatch.setattr(
        sf.socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, (PUBLIC_IP, 0))]
    )


def serve(handler, monkeypatch):
    """Route safe_fetch's httpx.Client through a MockTransport.

    The pinned URL (https://93.184.216.34/...) is what actually reaches the
    transport, so each handler asserts on the Host header to know which logical
    host was requested — which also proves pinning preserves the original host.
    """
    real_client = httpx.Client

    def make_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(sf.httpx, "Client", make_client)


def image_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "image/png"}, content=PNG)


# --------------------------------------------------------------------------- #
# Address / scheme validation (SSRF)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.10",
        "172.16.0.1",
        "169.254.169.254",  # cloud metadata
        "169.254.1.1",  # link-local
        "0.0.0.0",
        "224.0.0.1",  # multicast
        "::1",  # IPv6 loopback
        "fe80::1",  # IPv6 link-local
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
    ],
)
def test_non_public_addresses_are_rejected(ip: str) -> None:
    assert _is_public_ip(ip) is False


@pytest.mark.parametrize("ip", ["93.184.216.34", "1.1.1.1", "2606:4700::1111"])
def test_public_addresses_are_accepted(ip: str) -> None:
    assert _is_public_ip(ip) is True


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://x/1",
        "ftp://x/y",
        "data:image/png;base64,AAAA",
        "javascript:alert(1)",
    ],
)
def test_unsupported_schemes_are_rejected(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        _validate_url(url)


def test_url_without_a_hostname_is_rejected() -> None:
    with pytest.raises(UnsafeUrlError):
        _validate_url("http:///just/a/path")


def test_dns_failure_is_reported_as_unsafe(monkeypatch) -> None:
    import socket as real_socket

    def boom(*a, **k):
        raise real_socket.gaierror("nope")

    monkeypatch.setattr(sf.socket, "getaddrinfo", boom)
    with pytest.raises(UnsafeUrlError):
        _validate_url("https://example.com/logo.png")


def test_a_host_with_any_private_answer_is_rejected_entirely(monkeypatch) -> None:
    """Split-horizon / partially-poisoned DNS must not be narrowed down to the
    public answer — the whole host is refused."""
    monkeypatch.setattr(
        sf.socket,
        "getaddrinfo",
        lambda *a, **k: [
            (None, None, None, None, (PUBLIC_IP, 0)),
            (None, None, None, None, ("127.0.0.1", 0)),
        ],
    )
    with pytest.raises(UnsafeUrlError):
        _validate_url("https://rebind.example/logo.png")


def test_initial_url_is_validated_before_any_request(monkeypatch) -> None:
    called = False

    def handler(request):
        nonlocal called
        called = True
        return image_response(request)

    serve(handler, monkeypatch)
    monkeypatch.setattr(
        sf.socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("127.0.0.1", 0))]
    )
    with pytest.raises(UnsafeUrlError):
        safe_fetch_image("http://internal.example/logo.png")
    assert called is False, "no request may be issued for an unsafe target"


# --------------------------------------------------------------------------- #
# DNS rebinding: the connection is pinned to the validated address
# --------------------------------------------------------------------------- #
def test_request_is_pinned_to_the_validated_ip(public_dns, monkeypatch) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host_connected"] = request.url.host
        seen["host_header"] = request.headers["host"]
        return image_response(request)

    serve(handler, monkeypatch)
    result = safe_fetch_image("https://example.com/logo.png")

    # Connected to the address we validated, not to a name resolved again later.
    assert seen["host_connected"] == PUBLIC_IP
    # ...while still identifying as the original host (Host header + SNI), so
    # TLS verification and virtual hosting still work.
    assert seen["host_header"] == "example.com"
    assert result.content == PNG


def test_pinned_url_preserves_path_query_and_port() -> None:
    assert (
        _pinned_request_url("https://ex.com/a/b.png?v=2", PUBLIC_IP)
        == f"https://{PUBLIC_IP}/a/b.png?v=2"
    )
    assert (
        _pinned_request_url("http://ex.com:8080/l.png", PUBLIC_IP)
        == f"http://{PUBLIC_IP}:8080/l.png"
    )
    assert _pinned_request_url("https://ex.com", PUBLIC_IP) == f"https://{PUBLIC_IP}/"


def test_pinned_url_brackets_ipv6() -> None:
    assert (
        _pinned_request_url("https://ex.com/l.png", "2606:4700::1111")
        == "https://[2606:4700::1111]/l.png"
    )


# --------------------------------------------------------------------------- #
# Redirects — the regression that took the endpoint down
# --------------------------------------------------------------------------- #
def test_relative_redirect_location_is_resolved(public_dns, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/logo.png":
            return httpx.Response(302, headers={"location": "/assets/real.png"})
        assert request.url.path == "/assets/real.png"
        return image_response(request)

    serve(handler, monkeypatch)
    result = safe_fetch_image("https://example.com/logo.png")
    assert result.content == PNG
    assert result.final_url == "https://example.com/assets/real.png"


def test_absolute_redirect_location_is_followed(public_dns, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "example.com":
            return httpx.Response(301, headers={"location": "https://cdn.example.org/real.png"})
        return image_response(request)

    serve(handler, monkeypatch)
    result = safe_fetch_image("https://example.com/logo.png")
    assert result.final_url == "https://cdn.example.org/real.png"


def test_protocol_relative_redirect_location_is_followed(public_dns, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "example.com":
            return httpx.Response(302, headers={"location": "//cdn.example.org/real.png"})
        return image_response(request)

    serve(handler, monkeypatch)
    # Inherits the original scheme rather than becoming a schemeless URL.
    assert (
        safe_fetch_image("https://example.com/logo.png").final_url
        == "https://cdn.example.org/real.png"
    )


def test_http_to_https_redirect_is_followed(public_dns, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == "http":
            return httpx.Response(301, headers={"location": "https://example.com/logo.png"})
        return image_response(request)

    serve(handler, monkeypatch)
    assert (
        safe_fetch_image("http://example.com/logo.png").final_url == "https://example.com/logo.png"
    )


def test_redirect_to_a_private_address_is_rejected(monkeypatch) -> None:
    """The whole point of re-validating every hop."""

    def resolve(host, *a, **k):
        ip = "127.0.0.1" if host == "internal.example" else PUBLIC_IP
        return [(None, None, None, None, (ip, 0))]

    monkeypatch.setattr(sf.socket, "getaddrinfo", resolve)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://internal.example/secret"})

    serve(handler, monkeypatch)
    with pytest.raises(UnsafeUrlError):
        safe_fetch_image("https://example.com/logo.png")


def test_redirect_to_an_unsupported_scheme_is_rejected(public_dns, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "file:///etc/passwd"})

    serve(handler, monkeypatch)
    with pytest.raises(UnsafeUrlError):
        safe_fetch_image("https://example.com/logo.png")


def test_redirect_without_a_location_header_fails_cleanly(public_dns, monkeypatch) -> None:
    serve(lambda request: httpx.Response(302), monkeypatch)
    with pytest.raises(FetchFailedError, match="no Location"):
        safe_fetch_image("https://example.com/logo.png")


def test_malformed_redirect_location_fails_cleanly(public_dns, monkeypatch) -> None:
    serve(lambda request: httpx.Response(302, headers={"location": "http://["}), monkeypatch)
    with pytest.raises(FetchFailedError):
        safe_fetch_image("https://example.com/logo.png")


def test_redirect_loop_is_detected(public_dns, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/logo.png"})

    serve(handler, monkeypatch)
    with pytest.raises(FetchFailedError, match="redirect loop"):
        safe_fetch_image("https://example.com/logo.png")


def test_redirect_limit_is_enforced(public_dns, monkeypatch) -> None:
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(
            302, headers={"location": f"https://example.com/hop{counter['n']}.png"}
        )

    serve(handler, monkeypatch)
    with pytest.raises(FetchFailedError, match="too many redirects"):
        safe_fetch_image("https://example.com/logo.png")
    assert counter["n"] <= sf.MAX_REDIRECTS + 1


# --------------------------------------------------------------------------- #
# Response constraints
# --------------------------------------------------------------------------- #
def test_non_image_content_type_is_rejected(public_dns, monkeypatch) -> None:
    serve(
        lambda r: httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>"),
        monkeypatch,
    )
    with pytest.raises(FetchFailedError, match="unsupported content-type"):
        safe_fetch_image("https://example.com/logo.png")


def test_svg_is_rejected_because_it_can_carry_script(public_dns, monkeypatch) -> None:
    serve(
        lambda r: httpx.Response(200, headers={"content-type": "image/svg+xml"}, content=b"<svg/>"),
        monkeypatch,
    )
    with pytest.raises(FetchFailedError):
        safe_fetch_image("https://example.com/logo.svg")


def test_missing_content_type_is_rejected(public_dns, monkeypatch) -> None:
    serve(lambda r: httpx.Response(200, content=PNG), monkeypatch)
    with pytest.raises(FetchFailedError):
        safe_fetch_image("https://example.com/logo.png")


def test_oversized_response_is_rejected(public_dns, monkeypatch) -> None:
    big = b"x" * (sf.MAX_BYTES + 1)
    serve(
        lambda r: httpx.Response(200, headers={"content-type": "image/png"}, content=big),
        monkeypatch,
    )
    with pytest.raises(FetchFailedError, match="size limit"):
        safe_fetch_image("https://example.com/logo.png")


def test_oversized_streamed_response_is_aborted_without_buffering_it_all(
    public_dns, monkeypatch
) -> None:
    """A server that never declares a length must still not be able to make us
    buffer an unbounded body."""
    chunks_yielded = {"n": 0}

    def endless():
        while True:
            chunks_yielded["n"] += 1
            yield b"x" * 65536

    serve(
        lambda r: httpx.Response(200, headers={"content-type": "image/png"}, content=endless()),
        monkeypatch,
    )
    with pytest.raises(FetchFailedError, match="size limit"):
        safe_fetch_image("https://example.com/logo.png")
    # Stopped near the cap rather than reading forever.
    assert chunks_yielded["n"] <= (sf.MAX_BYTES // 65536) + 2


def test_empty_response_is_rejected(public_dns, monkeypatch) -> None:
    serve(
        lambda r: httpx.Response(200, headers={"content-type": "image/png"}, content=b""),
        monkeypatch,
    )
    with pytest.raises(FetchFailedError, match="empty"):
        safe_fetch_image("https://example.com/logo.png")


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 418, 500, 502, 503])
def test_non_200_statuses_are_rejected(public_dns, monkeypatch, status_code: int) -> None:
    serve(lambda r: httpx.Response(status_code), monkeypatch)
    with pytest.raises(FetchFailedError, match="unexpected status"):
        safe_fetch_image("https://example.com/logo.png")


def test_a_valid_image_is_returned(public_dns, monkeypatch) -> None:
    serve(image_response, monkeypatch)
    result = safe_fetch_image("https://example.com/logo.png")
    assert result.content == PNG
    assert result.content_type == "image/png"
    assert result.final_url == "https://example.com/logo.png"


# --------------------------------------------------------------------------- #
# Network failures
# --------------------------------------------------------------------------- #
def test_timeout_becomes_a_fetch_failure(public_dns, monkeypatch) -> None:
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    serve(handler, monkeypatch)
    with pytest.raises(FetchFailedError) as exc:
        safe_fetch_image("https://example.com/logo.png")
    assert "ConnectTimeout" in str(exc.value)


def test_connection_error_becomes_a_fetch_failure(public_dns, monkeypatch) -> None:
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    serve(handler, monkeypatch)
    with pytest.raises(FetchFailedError):
        safe_fetch_image("https://example.com/logo.png")


def test_network_errors_do_not_leak_the_upstream_url_or_body(public_dns, monkeypatch) -> None:
    """The message is the exception TYPE only, so a caller that logs it cannot
    accidentally record an internal URL or upstream response text."""

    def handler(request):
        raise httpx.ConnectError(
            "failed connecting to https://secret-internal.example/x", request=request
        )

    serve(handler, monkeypatch)
    with pytest.raises(FetchFailedError) as exc:
        safe_fetch_image("https://example.com/logo.png")
    assert "secret-internal" not in str(exc.value)
    assert str(exc.value) == "ConnectError"
