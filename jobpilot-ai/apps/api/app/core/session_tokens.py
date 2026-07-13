"""Short-lived, scoped tokens for the assisted-apply handoff.

Two token types, both signed with the app secret (HS256), independent of the
user's main login token:

- ``launch``  — one-time, ~5 min. Minted by the web app when a session is
  created and handed to the extension. The session also stores only the SHA-256
  of this token; it is invalidated on first exchange.
- ``session`` — ~30 min, scoped to exactly one application session. The
  extension exchanges the launch token for this and uses it as a Bearer token to
  read the session, its documents, and its safe answers — nothing else.

The employer page never receives either token; they live only in the web app and
the extension's isolated context.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from app.core.config import settings

try:  # mirror app.core.security's optional jose dependency
    from jose import JWTError, jwt
except ModuleNotFoundError:  # pragma: no cover - jose is installed in this project
    JWTError = Exception
    jwt = None

ALGORITHM = "HS256"
LAUNCH_TOKEN_TTL_MINUTES = 5
SESSION_TOKEN_TTL_MINUTES = 30

TokenType = Literal["launch", "session"]


def create_launch_token(session_id: int, user_id: int) -> str:
    """One-time launch token with a random ``jti`` for hash-based invalidation."""
    return _encode(
        {
            "typ": "launch",
            "sid": int(session_id),
            "uid": int(user_id),
            "jti": secrets.token_urlsafe(16),
        },
        ttl_minutes=LAUNCH_TOKEN_TTL_MINUTES,
    )


def create_session_token(session_id: int, user_id: int) -> str:
    return _encode(
        {"typ": "session", "sid": int(session_id), "uid": int(user_id)},
        ttl_minutes=SESSION_TOKEN_TTL_MINUTES,
    )


def decode_scoped_token(token: str, expected_type: TokenType) -> dict[str, Any] | None:
    payload = _decode(token)
    if payload is None or payload.get("typ") != expected_type:
        return None
    if not isinstance(payload.get("sid"), int) or not isinstance(payload.get("uid"), int):
        return None
    return payload


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Encoding (jose when available, HMAC fallback otherwise — matches core.security)
# --------------------------------------------------------------------------- #
def _encode(claims: dict[str, Any], *, ttl_minutes: int) -> str:
    expires = datetime.now(UTC) + timedelta(minutes=ttl_minutes)
    payload = {**claims, "exp": int(expires.timestamp())}
    if jwt:
        return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)
    return _encode_hs256(payload)


def _decode(token: str) -> dict[str, Any] | None:
    if jwt:
        try:
            return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        except JWTError:
            return None
    return _decode_hs256(token)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _encode_hs256(payload: dict) -> str:
    header = {"alg": ALGORITHM, "typ": "JWT"}
    signing_input = ".".join(
        [
            _b64url(json.dumps(header, separators=(",", ":")).encode()),
            _b64url(json.dumps(payload, separators=(",", ":")).encode()),
        ]
    )
    signature = hmac.new(settings.secret_key.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


def _decode_hs256(token: str) -> dict | None:
    try:
        header_segment, payload_segment, signature_segment = token.split(".")
        signing_input = f"{header_segment}.{payload_segment}"
        expected = hmac.new(settings.secret_key.encode(), signing_input.encode(), hashlib.sha256).digest()
        actual = _b64url_decode(signature_segment)
        if not hmac.compare_digest(expected, actual):
            return None
        payload = json.loads(_b64url_decode(payload_segment))
        if int(payload.get("exp", 0)) < int(datetime.now(UTC).timestamp()):
            return None
        return payload
    except (ValueError, json.JSONDecodeError, TypeError):
        return None
