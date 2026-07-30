"""Endpoint-aware status classification for People Data Labs responses.

PDL's own error reference is explicit that a 404 is **not** a bad request:

    404 - Not Found
    "While technically labeled as an error in the response, this simply means
    there were no profiles found matching your request."

    {"status": 404, "error": {"type": "not_found",
                              "message": "No records were found matching your request"}}

    -- https://docs.peopledatalabs.com/docs/errors

The generic HTTP mapper treated every 4xx that was not 401/403/429/422 as
``INVALID_INPUT``, so a company with no matching profiles — Toshiba Global
Commerce, Vanderbilt Health — surfaced as "The people provider could not accept
the profile request." That is a false failure: the request was well-formed and
the provider answered it correctly.

Search additionally returns 404 when a scroll has been exhausted, and a 404 can
in principle also mean the route itself is wrong (a bad path or an API version
that no longer exists). Those look identical at the status line, so the body's
``error.type`` is what separates "no data" from "wrong endpoint". A 404 that
does not carry PDL's ``not_found`` shape is reported as a route problem rather
than silently swallowed as an empty result — otherwise a broken adapter would
look exactly like a company nobody works at.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.people.errors import PeopleErrorCode

PdlEndpoint = Literal[
    "person_search",
    "person_enrichment",
    "person_identify",
    "company_enrichment",
    "company_search",
]

# PDL error types that mean "the query was fine, there was simply nothing".
_NOT_FOUND_TYPES = frozenset({"not_found", "no_results", "empty"})


@dataclass(frozen=True)
class PdlOutcome:
    """A classified PDL response.

    ``no_match`` is a *successful* outcome: the provider answered, and the
    answer was zero records. It must never move a circuit or surface as a
    provider failure.
    """

    code: PeopleErrorCode | None
    reason: str | None
    no_match: bool = False
    # Sanitized, non-personal provider metadata worth persisting and logging.
    safe_metadata: dict[str, object] | None = None

    @property
    def ok(self) -> bool:
        return self.code is None


def safe_error_fields(payload: object) -> dict[str, object]:
    """Extract only the non-personal parts of a PDL error body.

    PDL error bodies carry ``{"status", "error": {"type", "message"}}`` and no
    personal data, but the message is still provider text: it is length-capped
    and never merged with a response that could contain person records.
    """

    if not isinstance(payload, dict):
        return {}
    error = payload.get("error")
    if not isinstance(error, dict):
        return {}
    fields: dict[str, object] = {}
    error_type = error.get("type")
    if isinstance(error_type, str) and error_type.strip():
        fields["provider_error_type"] = error_type.strip()[:64]
    message = error.get("message")
    if isinstance(message, str) and message.strip():
        fields["provider_error_message"] = message.strip()[:200]
    return fields


def _is_documented_not_found(payload: object) -> bool:
    error_type = safe_error_fields(payload).get("provider_error_type")
    if isinstance(error_type, str):
        return error_type.lower() in _NOT_FOUND_TYPES
    # A 404 with no parseable body is ambiguous. PDL always sends the typed
    # envelope, so treat a bodyless 404 as a route problem, not as no data.
    return False


def classify(
    *,
    endpoint: PdlEndpoint,
    status_code: int,
    payload: object = None,
) -> PdlOutcome:
    """Classify one PDL response for one endpoint."""

    safe = safe_error_fields(payload)
    safe["provider_endpoint"] = endpoint

    if status_code == 200:
        if endpoint == "person_search" and _empty_search_body(payload):
            # Documented Search behaviour: HTTP 200 with total == 0.
            return PdlOutcome(
                code=None, reason=None, no_match=True, safe_metadata=safe
            )
        return PdlOutcome(code=None, reason=None, safe_metadata=safe)

    if status_code == 404:
        if _is_documented_not_found(payload):
            return PdlOutcome(
                code=None, reason=None, no_match=True, safe_metadata=safe
            )
        # Same status line, entirely different meaning: the path or API version
        # is wrong. Surfacing this as "no results" would hide an adapter bug
        # behind a plausible-looking empty state for every company at once.
        return PdlOutcome(
            code=PeopleErrorCode.INVALID_INPUT,
            reason="provider_route_invalid",
            safe_metadata=safe,
        )

    if status_code in {400, 422}:
        return PdlOutcome(
            code=PeopleErrorCode.INVALID_INPUT,
            reason="provider_request_invalid",
            safe_metadata=safe,
        )
    if status_code == 401:
        return PdlOutcome(
            code=PeopleErrorCode.AUTHENTICATION_FAILED,
            reason="provider_unauthorized",
            safe_metadata=safe,
        )
    if status_code == 402:
        # "You have reached your account maximum (all matches have been used)."
        return PdlOutcome(
            code=PeopleErrorCode.PROVIDER_BUDGET_EXHAUSTED,
            reason="provider_budget_exceeded",
            safe_metadata=safe,
        )
    if status_code == 403:
        return PdlOutcome(
            code=PeopleErrorCode.AUTHORIZATION_FAILED,
            reason="provider_forbidden",
            safe_metadata=safe,
        )
    if status_code == 405:
        # Wrong HTTP method for the route — an adapter defect, and one worth
        # naming precisely rather than blaming on the request body.
        return PdlOutcome(
            code=PeopleErrorCode.INVALID_INPUT,
            reason="provider_route_invalid",
            safe_metadata=safe,
        )
    if status_code == 429:
        return PdlOutcome(
            code=PeopleErrorCode.RATE_LIMITED,
            reason="provider_rate_limited",
            safe_metadata=safe,
        )
    if status_code >= 500:
        return PdlOutcome(
            code=PeopleErrorCode.PROVIDER_SERVER_ERROR,
            reason="provider_unavailable",
            safe_metadata=safe,
        )
    return PdlOutcome(
        code=PeopleErrorCode.UNKNOWN_PROVIDER_ERROR,
        reason="provider_unavailable",
        safe_metadata=safe,
    )


def _empty_search_body(payload: object) -> bool:
    """True when a 200 Search body carries no records.

    PDL documents that Search returns 200 for any valid request and that the
    caller should read ``total``; both signals are checked because ``total`` is
    absent from some cached/proxied responses.
    """

    if not isinstance(payload, dict):
        return False
    rows = payload.get("data")
    if isinstance(rows, list) and rows:
        return False
    total = payload.get("total")
    if isinstance(total, int) and total > 0 and not isinstance(rows, list):
        # Records exist but this page carried none — not a no-match.
        return False
    return isinstance(rows, list) or total == 0
