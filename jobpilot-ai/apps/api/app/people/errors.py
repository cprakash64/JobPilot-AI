"""Typed failure taxonomy for the People Who Can Help provider pipeline.

The orchestration layer historically carried free-form ``reason`` strings and
treated every one of them as evidence that the provider itself was unhealthy.
That conflated three very different things: a bad request for one company, a
successful-but-empty answer, and a real provider outage. This module gives each
failure a single typed code plus the rules that decide

* whether the failure is request-scoped or provider-wide,
* which circuit (if any) it may open,
* whether an alternate provider may be tried,
* and whether the user may retry.

Legacy ``reason`` strings stay in place as the persisted/wire representation so
stored discovery runs remain readable; :func:`code_for_reason` maps them onto the
taxonomy.
"""

from __future__ import annotations

from enum import StrEnum


class PeopleErrorCode(StrEnum):
    """Every way a people search can fail to produce provider results."""

    INVALID_INPUT = "INVALID_INPUT"
    COMPANY_DOMAIN_UNRESOLVED = "COMPANY_DOMAIN_UNRESOLVED"
    NO_RESULTS = "NO_RESULTS"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    AUTHORIZATION_FAILED = "AUTHORIZATION_FAILED"
    PROVIDER_BUDGET_EXHAUSTED = "PROVIDER_BUDGET_EXHAUSTED"
    USER_BUDGET_EXHAUSTED = "USER_BUDGET_EXHAUSTED"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_SERVER_ERROR = "PROVIDER_SERVER_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    REQUEST_CANCELLED = "REQUEST_CANCELLED"
    UNKNOWN_PROVIDER_ERROR = "UNKNOWN_PROVIDER_ERROR"


# Which circuit, if any, a code is allowed to influence. ``None`` means the
# failure is scoped to the single request that produced it and must never make
# an unrelated company's search unavailable.
CircuitKind = str  # "transient" | "configuration" | "budget"

_CIRCUIT_SCOPE: dict[PeopleErrorCode, CircuitKind | None] = {
    # Request-scoped. One malformed company must not pause the product.
    PeopleErrorCode.INVALID_INPUT: None,
    PeopleErrorCode.COMPANY_DOMAIN_UNRESOLVED: None,
    PeopleErrorCode.NO_RESULTS: None,
    PeopleErrorCode.REQUEST_CANCELLED: None,
    PeopleErrorCode.USER_BUDGET_EXHAUSTED: None,
    PeopleErrorCode.UNKNOWN_PROVIDER_ERROR: None,
    # Provider configuration. The credential or account grant needs attention;
    # retrying the same call cannot fix it, so it gets its own circuit and its
    # own operator-facing status.
    PeopleErrorCode.AUTHENTICATION_FAILED: "configuration",
    PeopleErrorCode.AUTHORIZATION_FAILED: "configuration",
    # Provider account spend. Paid calls stop until the budget window rolls.
    PeopleErrorCode.PROVIDER_BUDGET_EXHAUSTED: "budget",
    # Genuine transient provider health signals.
    PeopleErrorCode.RATE_LIMITED: "transient",
    PeopleErrorCode.PROVIDER_TIMEOUT: "transient",
    PeopleErrorCode.PROVIDER_SERVER_ERROR: "transient",
    PeopleErrorCode.NETWORK_ERROR: "transient",
}

# Rate limiting only counts toward the transient circuit once it is sustained:
# a single 429 with a Retry-After is normal traffic shaping, not an outage.
_SUSTAINED_ONLY: frozenset[PeopleErrorCode] = frozenset(
    {PeopleErrorCode.RATE_LIMITED}
)

# Codes for which trying a different people provider can plausibly help.
_FALLBACK_ELIGIBLE: frozenset[PeopleErrorCode] = frozenset(
    {
        PeopleErrorCode.RATE_LIMITED,
        PeopleErrorCode.PROVIDER_TIMEOUT,
        PeopleErrorCode.PROVIDER_SERVER_ERROR,
        PeopleErrorCode.NETWORK_ERROR,
    }
)

# Codes the user can usefully retry from the UI.
_USER_RETRYABLE: frozenset[PeopleErrorCode] = frozenset(
    {
        PeopleErrorCode.RATE_LIMITED,
        PeopleErrorCode.PROVIDER_TIMEOUT,
        PeopleErrorCode.PROVIDER_SERVER_ERROR,
        PeopleErrorCode.NETWORK_ERROR,
        PeopleErrorCode.UNKNOWN_PROVIDER_ERROR,
    }
)

# Persisted/wire ``reason`` strings mapped onto the taxonomy. Unknown reasons
# fall back to UNKNOWN_PROVIDER_ERROR, which is deliberately request-scoped:
# an unclassified failure must not be able to pause the whole feature.
_REASON_TO_CODE: dict[str, PeopleErrorCode] = {
    "provider_not_configured": PeopleErrorCode.AUTHENTICATION_FAILED,
    "provider_unauthorized": PeopleErrorCode.AUTHENTICATION_FAILED,
    "provider_forbidden": PeopleErrorCode.AUTHORIZATION_FAILED,
    "provider_master_key_required_or_forbidden": (
        PeopleErrorCode.AUTHORIZATION_FAILED
    ),
    "provider_rate_limited": PeopleErrorCode.RATE_LIMITED,
    "provider_timeout": PeopleErrorCode.PROVIDER_TIMEOUT,
    "provider_network_error": PeopleErrorCode.NETWORK_ERROR,
    "provider_unavailable": PeopleErrorCode.PROVIDER_SERVER_ERROR,
    "provider_circuit_open": PeopleErrorCode.PROVIDER_SERVER_ERROR,
    "provider_configuration_circuit_open": (
        PeopleErrorCode.AUTHENTICATION_FAILED
    ),
    "provider_request_invalid": PeopleErrorCode.INVALID_INPUT,
    "provider_response_invalid": PeopleErrorCode.INVALID_INPUT,
    "provider_schema_error": PeopleErrorCode.INVALID_INPUT,
    # A 404 that is not the provider's documented "no records" envelope: the
    # route or API version is wrong. Request-scoped so a deploy mistake cannot
    # pause the provider, but named distinctly so it is visible in logs.
    "provider_route_invalid": PeopleErrorCode.INVALID_INPUT,
    "provider_operation_duplicate": PeopleErrorCode.REQUEST_CANCELLED,
    "provider_request_cancelled": PeopleErrorCode.REQUEST_CANCELLED,
    "provider_budget_exceeded": PeopleErrorCode.PROVIDER_BUDGET_EXHAUSTED,
    "provider_user_limit_exceeded": PeopleErrorCode.USER_BUDGET_EXHAUSTED,
    "company_domain_unresolved": PeopleErrorCode.COMPANY_DOMAIN_UNRESOLVED,
    "no_results": PeopleErrorCode.NO_RESULTS,
}

# The reverse direction, used when a typed code needs a persisted reason.
_CODE_TO_REASON: dict[PeopleErrorCode, str] = {
    PeopleErrorCode.INVALID_INPUT: "provider_request_invalid",
    PeopleErrorCode.COMPANY_DOMAIN_UNRESOLVED: "company_domain_unresolved",
    PeopleErrorCode.NO_RESULTS: "no_results",
    PeopleErrorCode.AUTHENTICATION_FAILED: "provider_unauthorized",
    PeopleErrorCode.AUTHORIZATION_FAILED: "provider_forbidden",
    PeopleErrorCode.PROVIDER_BUDGET_EXHAUSTED: "provider_budget_exceeded",
    PeopleErrorCode.USER_BUDGET_EXHAUSTED: "provider_user_limit_exceeded",
    PeopleErrorCode.RATE_LIMITED: "provider_rate_limited",
    PeopleErrorCode.PROVIDER_TIMEOUT: "provider_timeout",
    PeopleErrorCode.PROVIDER_SERVER_ERROR: "provider_unavailable",
    PeopleErrorCode.NETWORK_ERROR: "provider_network_error",
    PeopleErrorCode.REQUEST_CANCELLED: "provider_request_cancelled",
    PeopleErrorCode.UNKNOWN_PROVIDER_ERROR: "provider_unavailable",
}


def code_for_reason(reason: str | None) -> PeopleErrorCode:
    """Map a persisted/wire reason string onto its typed code."""

    return _REASON_TO_CODE.get(
        (reason or "").strip(), PeopleErrorCode.UNKNOWN_PROVIDER_ERROR
    )


def reason_for_code(code: PeopleErrorCode) -> str:
    """Map a typed code back to the persisted/wire reason string."""

    return _CODE_TO_REASON.get(code, "provider_unavailable")


def code_for_http_status(status_code: int) -> PeopleErrorCode:
    """Classify a provider HTTP status that has no adapter-specific override."""

    if status_code == 401:
        return PeopleErrorCode.AUTHENTICATION_FAILED
    if status_code in {402, 403}:
        # 402 Payment Required is the usual provider-account spend signal.
        return (
            PeopleErrorCode.PROVIDER_BUDGET_EXHAUSTED
            if status_code == 402
            else PeopleErrorCode.AUTHORIZATION_FAILED
        )
    if status_code == 429:
        return PeopleErrorCode.RATE_LIMITED
    if status_code >= 500:
        return PeopleErrorCode.PROVIDER_SERVER_ERROR
    if status_code >= 400:
        # Ordinary 4xx responses describe this request, not provider health.
        return PeopleErrorCode.INVALID_INPUT
    return PeopleErrorCode.UNKNOWN_PROVIDER_ERROR


def circuit_kind(code: PeopleErrorCode) -> CircuitKind | None:
    """Which circuit this failure may influence, or ``None`` if request-scoped."""

    return _CIRCUIT_SCOPE.get(code)


def is_request_scoped(code: PeopleErrorCode) -> bool:
    return circuit_kind(code) is None


def requires_sustained_failures(code: PeopleErrorCode) -> bool:
    """True when a single occurrence must not move the circuit at all."""

    return code in _SUSTAINED_ONLY


def is_fallback_eligible(code: PeopleErrorCode) -> bool:
    """Whether an alternate people provider may be attempted for this failure."""

    return code in _FALLBACK_ELIGIBLE


def is_user_retryable(code: PeopleErrorCode) -> bool:
    """Whether the UI should offer a Retry control for this failure."""

    return code in _USER_RETRYABLE
