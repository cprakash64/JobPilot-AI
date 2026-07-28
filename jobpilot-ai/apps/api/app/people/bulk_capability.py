from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Literal

from app.core.config import settings

BulkCapabilityState = Literal[
    "unknown",
    "supported",
    "temporarily_rejected",
    "account_not_supported",
]

_LOCAL: dict[str, dict[str, object]] = {}
_LOCK = Lock()


def _key(
    provider: str,
    adapter_version: str,
    account_scope: str,
) -> str:
    return (
        f"people:bulk-capability:{provider}:{account_scope}:"
        f"{adapter_version}"
    )


def _read(
    provider: str,
    adapter_version: str,
    account_scope: str,
) -> dict[str, object]:
    key = _key(provider, adapter_version, account_scope)
    if settings.app_env != "test":
        try:
            import redis

            client = redis.Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
            )
            raw = client.get(key)
            if raw:
                value = json.loads(raw)
                if isinstance(value, dict):
                    return value
        except Exception:
            pass
    with _LOCK:
        value = dict(_LOCAL.get(key, {}))
    expires_at = value.get("expires_at")
    if isinstance(expires_at, str):
        try:
            if datetime.fromisoformat(expires_at) <= datetime.now(UTC):
                with _LOCK:
                    _LOCAL.pop(key, None)
                return {}
        except ValueError:
            return {}
    return value


def _write(
    provider: str,
    adapter_version: str,
    account_scope: str,
    value: dict[str, object],
) -> None:
    ttl = max(60, settings.people_apollo_bulk_capability_ttl_seconds)
    stored = {
        **value,
        "expires_at": (datetime.now(UTC) + timedelta(seconds=ttl)).isoformat(),
    }
    key = _key(provider, adapter_version, account_scope)
    if settings.app_env != "test":
        try:
            import redis

            client = redis.Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
            )
            client.set(key, json.dumps(stored), ex=ttl)
            return
        except Exception:
            pass
    with _LOCK:
        _LOCAL[key] = stored


def bulk_capability_state(
    provider: str,
    adapter_version: str,
    account_scope: str,
) -> BulkCapabilityState:
    if not settings.people_apollo_bulk_capability_enabled:
        return "unknown"
    value = _read(provider, adapter_version, account_scope)
    state = value.get("state", "unknown")
    if state in {
        "unknown",
        "supported",
        "temporarily_rejected",
        "account_not_supported",
    }:
        return state
    return "unknown"


def record_bulk_supported(
    provider: str,
    adapter_version: str,
    account_scope: str,
) -> None:
    if settings.people_apollo_bulk_capability_enabled:
        _write(
            provider,
            adapter_version,
            account_scope,
            {"state": "supported", "request_level_422_count": 0},
        )


def record_bulk_request_level_422(
    provider: str,
    adapter_version: str,
    account_scope: str,
) -> BulkCapabilityState:
    if not settings.people_apollo_bulk_capability_enabled:
        return "unknown"
    current = _read(provider, adapter_version, account_scope)
    count = int(current.get("request_level_422_count", 0)) + 1
    threshold = max(2, settings.people_apollo_bulk_rejection_threshold)
    state: BulkCapabilityState = (
        "temporarily_rejected" if count >= threshold else "unknown"
    )
    _write(
        provider,
        adapter_version,
        account_scope,
        {"state": state, "request_level_422_count": count},
    )
    return state


def set_account_not_supported(
    provider: str,
    adapter_version: str,
    account_scope: str,
) -> None:
    """Explicit internal action only; never inferred from provider responses."""

    _write(
        provider,
        adapter_version,
        account_scope,
        {
            "state": "account_not_supported",
            "request_level_422_count": 0,
        },
    )


def reset_bulk_capability(
    provider: str,
    adapter_version: str,
    account_scope: str,
) -> None:
    key = _key(provider, adapter_version, account_scope)
    if settings.app_env != "test":
        try:
            import redis

            client = redis.Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
            )
            client.delete(key)
        except Exception:
            pass
    with _LOCK:
        _LOCAL.pop(key, None)


def clear_local_bulk_capabilities() -> None:
    with _LOCK:
        _LOCAL.clear()
