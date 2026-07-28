from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Literal, TypedDict

from app.core.config import settings
from app.people.schemas import ProviderPerson

CompletePersonCacheState = Literal["success", "not_found", "error"]


class CompletePersonCacheEntry(TypedDict, total=False):
    state: CompletePersonCacheState
    person: dict[str, object]
    reason: str
    retry_at: str


_LOCAL: dict[str, tuple[datetime, str]] = {}
_LOCK = Lock()


def _cache_key(
    *,
    provider: str,
    account_scope: str,
    provider_person_id: str,
    adapter_version: str,
    evidence_version: str,
) -> str:
    identity_hash = hashlib.sha256(provider_person_id.encode()).hexdigest()
    return (
        f"people:complete-person:{provider}:{account_scope}:"
        f"{adapter_version}:{evidence_version}:{identity_hash}"
    )


def _read(key: str) -> CompletePersonCacheEntry | None:
    raw: str | None = None
    if settings.app_env != "test":
        try:
            import redis

            client = redis.Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
                decode_responses=True,
            )
            raw = client.get(key)
        except Exception:
            raw = None
    if raw is None:
        with _LOCK:
            local = _LOCAL.get(key)
            if local and local[0] > datetime.now(UTC):
                raw = local[1]
            elif local:
                _LOCAL.pop(key, None)
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write(
    key: str,
    value: CompletePersonCacheEntry,
    *,
    ttl_seconds: int,
) -> None:
    ttl = max(60, ttl_seconds)
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True)
    if settings.app_env != "test":
        try:
            import redis

            client = redis.Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
                decode_responses=True,
            )
            client.set(key, raw, ex=ttl)
            return
        except Exception:
            pass
    with _LOCK:
        _LOCAL[key] = (
            datetime.now(UTC) + timedelta(seconds=ttl),
            raw,
        )


def get_complete_person(
    *,
    provider: str,
    account_scope: str,
    provider_person_id: str,
    adapter_version: str,
    evidence_version: str,
) -> CompletePersonCacheEntry | None:
    return _read(
        _cache_key(
            provider=provider,
            account_scope=account_scope,
            provider_person_id=provider_person_id,
            adapter_version=adapter_version,
            evidence_version=evidence_version,
        )
    )


def cache_complete_person_success(
    person: ProviderPerson,
    *,
    account_scope: str,
    adapter_version: str,
    evidence_version: str,
) -> None:
    _write(
        _cache_key(
            provider=person.provider,
            account_scope=account_scope,
            provider_person_id=person.provider_person_id,
            adapter_version=adapter_version,
            evidence_version=evidence_version,
        ),
        {
            "state": "success",
            "person": person.model_dump(mode="json"),
        },
        ttl_seconds=settings.people_apollo_complete_person_cache_ttl_seconds,
    )


def cache_complete_person_not_found(
    *,
    provider: str,
    account_scope: str,
    provider_person_id: str,
    adapter_version: str,
    evidence_version: str,
) -> None:
    _write(
        _cache_key(
            provider=provider,
            account_scope=account_scope,
            provider_person_id=provider_person_id,
            adapter_version=adapter_version,
            evidence_version=evidence_version,
        ),
        {"state": "not_found"},
        ttl_seconds=settings.people_apollo_complete_person_not_found_ttl_seconds,
    )


def cache_complete_person_error(
    reason: str,
    *,
    provider: str,
    account_scope: str,
    provider_person_id: str,
    adapter_version: str,
    evidence_version: str,
    non_retryable: bool,
) -> None:
    ttl = (
        settings.people_apollo_complete_person_cache_ttl_seconds
        if non_retryable
        else settings.people_apollo_complete_person_error_ttl_seconds
    )
    retry_at = datetime.now(UTC) + timedelta(seconds=max(60, ttl))
    _write(
        _cache_key(
            provider=provider,
            account_scope=account_scope,
            provider_person_id=provider_person_id,
            adapter_version=adapter_version,
            evidence_version=evidence_version,
        ),
        {
            "state": "error",
            "reason": reason,
            "retry_at": retry_at.isoformat(),
        },
        ttl_seconds=ttl,
    )


def clear_local_complete_person_cache() -> None:
    with _LOCK:
        _LOCAL.clear()
