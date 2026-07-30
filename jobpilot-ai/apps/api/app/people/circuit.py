"""Scoped circuit breakers for the People Who Can Help providers.

The previous breaker was a single process-global counter keyed only by provider
name. Any three failures of any kind — including a 404 for one malformed
company — opened it, and once open it made *every* company's search report
"temporarily paused after repeated provider failures".

This module replaces that with three independent circuits per provider account
and operation:

``transient``
    Timeouts, network failures, provider 5xx, and *sustained* 429s. Short
    cooldown, half-open probe, closes on the first healthy response.
``configuration``
    Authentication/authorization failures. Retrying cannot fix these, so they
    get a longer cooldown and a status that explicitly names the credential or
    account-grant problem instead of a generic transient message.
``budget``
    Provider-account spend exhaustion. Paid calls stop with their own
    operational status rather than looking like an outage.

Request-scoped failures (bad input, unresolved company domain, empty results,
cancellations, per-user budget) never reach this module — see
:mod:`app.people.errors` for the classification rules.

State is keyed by ``provider + non-reversible account fingerprint + operation``
and stored under a versioned namespace so the obsolete global keys can never be
reused. Redis is the shared store when available, with a process-local fallback
so a Redis outage degrades to per-instance protection rather than no protection.
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Literal

from app.core.config import settings
from app.people.errors import (
    PeopleErrorCode,
    circuit_kind,
    requires_sustained_failures,
)

logger = logging.getLogger("jobpilot.people.circuit")

# Bump this when the stored shape or the semantics change. Old keys are then
# ignored rather than reinterpreted, which is what makes a stale "open" state
# from the previous implementation impossible to inherit.
CIRCUIT_NAMESPACE_VERSION = "v2"
CIRCUIT_KEY_PREFIX = f"people:circuit:{CIRCUIT_NAMESPACE_VERSION}"

CircuitKind = Literal["transient", "configuration", "budget"]
CircuitState = Literal["closed", "open", "half_open"]

_CIRCUIT_KINDS: tuple[CircuitKind, ...] = ("transient", "configuration", "budget")

_LOCAL: dict[str, dict[str, object]] = {}
_LOCK = Lock()


@dataclass(frozen=True)
class CircuitDecision:
    """Whether a provider call may proceed, and why not when it may not."""

    allowed: bool
    kind: CircuitKind | None = None
    state: CircuitState = "closed"
    retry_after_seconds: int | None = None
    probe: bool = False


@dataclass(frozen=True)
class CircuitSnapshot:
    """Observability view of all three circuits for one provider operation."""

    transient: CircuitState = "closed"
    configuration: CircuitState = "closed"
    budget: CircuitState = "closed"
    retry_after_seconds: int | None = None
    open_kinds: tuple[CircuitKind, ...] = field(default_factory=tuple)

    def as_label(self) -> str:
        if not self.open_kinds:
            return "closed"
        return "+".join(f"{kind}_open" for kind in self.open_kinds)


def _now() -> datetime:
    return datetime.now(UTC)


def _key(
    *, provider: str, account_fingerprint: str, operation: str, kind: CircuitKind
) -> str:
    return (
        f"{CIRCUIT_KEY_PREFIX}:{provider}:{account_fingerprint}:{operation}:{kind}"
    )


def _cooldown_seconds(kind: CircuitKind) -> int:
    if kind == "configuration":
        return max(30, settings.people_circuit_configuration_cooldown_seconds)
    if kind == "budget":
        return max(60, settings.people_circuit_budget_cooldown_seconds)
    return max(5, settings.people_circuit_cooldown_seconds)


def _state_ttl_seconds(kind: CircuitKind) -> int:
    """Bounded TTL so no circuit key can outlive its usefulness."""

    return max(
        _cooldown_seconds(kind) * 4,
        settings.people_circuit_failure_window_seconds * 2,
        60,
    )


def _redis_client():
    if settings.app_env == "test":
        return None
    try:
        import redis

        return redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
    except Exception:
        return None


def _read(key: str) -> dict[str, object]:
    client = _redis_client()
    if client is not None:
        try:
            raw = client.get(key)
            if raw:
                value = json.loads(raw)
                if isinstance(value, dict):
                    return value
            return {}
        except Exception:
            pass
    with _LOCK:
        value = dict(_LOCAL.get(key, {}))
    expires_at = value.get("expires_at")
    if isinstance(expires_at, str):
        try:
            if datetime.fromisoformat(expires_at) <= _now():
                with _LOCK:
                    _LOCAL.pop(key, None)
                return {}
        except ValueError:
            return {}
    return value


def _write(key: str, value: dict[str, object], *, ttl_seconds: int) -> None:
    stored = {
        **value,
        "expires_at": (_now() + timedelta(seconds=ttl_seconds)).isoformat(),
    }
    client = _redis_client()
    if client is not None:
        try:
            client.set(key, json.dumps(stored), ex=ttl_seconds)
            return
        except Exception:
            pass
    with _LOCK:
        _LOCAL[key] = stored


def _delete(key: str) -> None:
    client = _redis_client()
    if client is not None:
        try:
            client.delete(key)
        except Exception:
            pass
    with _LOCK:
        _LOCAL.pop(key, None)


def _opened_until(value: dict[str, object]) -> datetime | None:
    raw = value.get("opened_until")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _recent_failures(value: dict[str, object], *, now: datetime) -> list[float]:
    raw = value.get("failures")
    if not isinstance(raw, list):
        return []
    window = max(5, settings.people_circuit_failure_window_seconds)
    cutoff = now.timestamp() - window
    return [
        float(item)
        for item in raw
        if isinstance(item, int | float) and float(item) > cutoff
    ][-50:]


def _kind_state(
    value: dict[str, object], *, now: datetime
) -> tuple[CircuitState, int | None]:
    opened_until = _opened_until(value)
    if opened_until is None:
        return "closed", None
    if opened_until > now:
        remaining = max(1, int((opened_until - now).total_seconds() + 0.999))
        return "open", remaining
    # Cooldown elapsed: the next caller becomes the half-open probe.
    return "half_open", None


def _backoff_cooldown(kind: CircuitKind, consecutive_openings: int) -> int:
    """Bounded exponential backoff with jitter for repeated openings."""

    base = _cooldown_seconds(kind)
    ceiling = max(base, settings.people_circuit_max_cooldown_seconds)
    scaled = min(ceiling, base * (2 ** max(0, consecutive_openings - 1)))
    jitter = random.uniform(0.85, 1.15)
    return max(1, min(ceiling, int(scaled * jitter)))


def allow(
    *, provider: str, account_fingerprint: str, operation: str
) -> CircuitDecision:
    """Decide whether a provider call may proceed right now.

    Returns the *first* blocking circuit, checked configuration → budget →
    transient so the most actionable operational status wins.
    """

    now = _now()
    for kind in ("configuration", "budget", "transient"):
        key = _key(
            provider=provider,
            account_fingerprint=account_fingerprint,
            operation=operation,
            kind=kind,
        )
        value = _read(key)
        state, retry_after = _kind_state(value, now=now)
        if state == "open":
            return CircuitDecision(
                allowed=False,
                kind=kind,
                state="open",
                retry_after_seconds=retry_after,
            )
        if state == "half_open":
            # Exactly one controlled probe per cooldown. Claim it by clearing
            # ``opened_until`` before returning; a loser sees "closed" and is
            # allowed through only after the probe has recorded its outcome.
            _write(
                key,
                {
                    **value,
                    "opened_until": None,
                    "state": "half_open",
                    "probe_started_at": now.isoformat(),
                },
                ttl_seconds=_state_ttl_seconds(kind),
            )
            _log_transition(provider, operation, kind, "open", "half_open")
            return CircuitDecision(allowed=True, kind=kind, state="half_open", probe=True)
    return CircuitDecision(allowed=True)


def record_failure(
    *,
    provider: str,
    account_fingerprint: str,
    operation: str,
    code: PeopleErrorCode,
) -> CircuitState:
    """Record a failure against the circuit its code is allowed to influence.

    Request-scoped codes return ``"closed"`` without touching any state.
    """

    kind = circuit_kind(code)
    if kind is None:
        return "closed"
    key = _key(
        provider=provider,
        account_fingerprint=account_fingerprint,
        operation=operation,
        kind=kind,
    )
    now = _now()
    value = _read(key)
    previous_state, _ = _kind_state(value, now=now)
    failures = [*_recent_failures(value, now=now), now.timestamp()]
    threshold = _threshold_for(kind)
    if requires_sustained_failures(code):
        # A single 429 is traffic shaping; only a sustained run counts.
        threshold = max(threshold, settings.people_circuit_rate_limit_threshold)
    openings = int(value.get("consecutive_openings", 0) or 0)
    opened_until: datetime | None = None
    state: CircuitState = "closed"
    if len(failures) >= threshold:
        openings += 1
        opened_until = now + timedelta(seconds=_backoff_cooldown(kind, openings))
        state = "open"
        failures = []
    _write(
        key,
        {
            "failures": failures,
            "opened_until": opened_until.isoformat() if opened_until else None,
            "consecutive_openings": openings,
            "state": state,
            "last_code": str(code),
        },
        ttl_seconds=_state_ttl_seconds(kind),
    )
    if state == "open":
        _log_transition(provider, operation, kind, previous_state, "open")
    return state


def record_success(
    *, provider: str, account_fingerprint: str, operation: str
) -> None:
    """A healthy response — including a successful empty one — closes circuits."""

    now = _now()
    for kind in _CIRCUIT_KINDS:
        key = _key(
            provider=provider,
            account_fingerprint=account_fingerprint,
            operation=operation,
            kind=kind,
        )
        value = _read(key)
        if not value:
            continue
        previous_state, _ = _kind_state(value, now=now)
        _delete(key)
        if previous_state != "closed" or value.get("failures"):
            _log_transition(provider, operation, kind, previous_state, "closed")


def circuit_state(
    *, provider: str, account_fingerprint: str, operation: str
) -> CircuitSnapshot:
    """Read-only snapshot for logging, diagnostics, and tests."""

    now = _now()
    states: dict[str, CircuitState] = {}
    retry_after: int | None = None
    open_kinds: list[CircuitKind] = []
    for kind in _CIRCUIT_KINDS:
        value = _read(
            _key(
                provider=provider,
                account_fingerprint=account_fingerprint,
                operation=operation,
                kind=kind,
            )
        )
        state, remaining = _kind_state(value, now=now)
        states[kind] = state
        if state == "open":
            open_kinds.append(kind)
            if remaining is not None:
                retry_after = (
                    remaining if retry_after is None else max(retry_after, remaining)
                )
    return CircuitSnapshot(
        transient=states["transient"],
        configuration=states["configuration"],
        budget=states["budget"],
        retry_after_seconds=retry_after,
        open_kinds=tuple(open_kinds),
    )


def _threshold_for(kind: CircuitKind) -> int:
    if kind == "configuration":
        return max(1, settings.people_circuit_configuration_threshold)
    if kind == "budget":
        return 1
    return max(2, settings.people_circuit_failure_threshold)


def _log_transition(
    provider: str,
    operation: str,
    kind: CircuitKind,
    previous: CircuitState,
    current: CircuitState,
) -> None:
    if previous == current:
        return
    # Imported lazily: observability imports nothing from this module, but
    # keeping the dependency one-directional avoids an import cycle if that
    # ever changes.
    from app.people.observability import metric

    metric(
        "people_circuit_state_transitions_total",
        provider=provider,
        status=f"{kind}:{previous}->{current}",
    )
    logger.info(
        "people_circuit_transition provider=%s operation=%s kind=%s from=%s to=%s",
        provider,
        operation,
        kind,
        previous,
        current,
    )


def clear_local_circuits() -> None:
    """Drop process-local state. Used by tests and by local development."""

    with _LOCK:
        _LOCAL.clear()


def clear_people_circuits(*, provider: str | None = None) -> int:
    """Delete only People-provider circuit keys under the current namespace.

    Returns the number of keys removed. Never touches unrelated Redis data: the
    scan pattern is anchored to :data:`CIRCUIT_KEY_PREFIX`.
    """

    pattern = f"{CIRCUIT_KEY_PREFIX}:{provider}:*" if provider else f"{CIRCUIT_KEY_PREFIX}:*"
    removed = 0
    client = _redis_client()
    if client is not None:
        try:
            cursor = 0
            while True:
                cursor, keys = client.scan(cursor=cursor, match=pattern, count=200)
                if keys:
                    removed += int(client.delete(*keys) or 0)
                if cursor == 0:
                    break
        except Exception:
            logger.warning("people_circuit_clear_failed store=redis")
    prefix = pattern.rstrip("*")
    with _LOCK:
        local_keys = [key for key in _LOCAL if key.startswith(prefix)]
        for key in local_keys:
            _LOCAL.pop(key, None)
    removed += len(local_keys)
    logger.info(
        "people_circuit_cleared namespace=%s provider=%s removed=%s",
        CIRCUIT_NAMESPACE_VERSION,
        provider or "all",
        removed,
    )
    return removed


def monotonic_ms(started: float) -> float:
    return (time.monotonic() - started) * 1000
