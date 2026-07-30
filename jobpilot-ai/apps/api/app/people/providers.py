from __future__ import annotations

# ruff: noqa: E501
import asyncio
import hashlib
import logging
import re
import time
from collections import defaultdict
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol
from urllib.parse import quote, urlsplit

import httpx

from app.core.config import settings
from app.people import circuit, pdl_company, pdl_query, pdl_status
from app.people.bulk_capability import (
    bulk_capability_state,
    record_bulk_request_level_422,
    record_bulk_supported,
)
from app.people.complete_person_cache import (
    cache_complete_person_error,
    cache_complete_person_not_found,
    cache_complete_person_success,
    get_complete_person,
)
from app.people.employment_validation import EMPLOYMENT_EVIDENCE_VERSION
from app.people.errors import (
    PeopleErrorCode,
    code_for_http_status,
    code_for_reason,
    is_request_scoped,
    reason_for_code,
)
from app.people.observability import metric
from app.people.pdl_status import PdlEndpoint
from app.people.provider_usage import (
    ProviderUsageContext,
    ProviderUsageRecorder,
    SessionFactory,
    operation_idempotency_key,
    reported_credits,
)
from app.people.schemas import (
    EmailVerificationResult,
    PeopleCategory,
    PeopleSearchQuery,
    PersonEnrichmentRequest,
    ProviderPerson,
    ProviderUsage,
    WorkEmailRequest,
    WorkEmailResult,
)
from app.people.security import safe_profile_url

logger = logging.getLogger("jobpilot.people.provider")

APOLLO_ENRICHMENT_STRATEGY_VERSION = (
    "apollo-enrichment-v4-complete-person"
)
APOLLO_ENRICHMENT_ADAPTER_VERSION = APOLLO_ENRICHMENT_STRATEGY_VERSION
PDL_DISCOVERY_STRATEGY_VERSION = "pdl-category-search-v2"
# Bulk request compatibility did not change with the Complete Person rollout.
# Keep its account-scoped rejection state on the existing key so a strategy
# fingerprint change cannot accidentally re-enable a known-rejected operation.
APOLLO_BULK_CAPABILITY_VERSION = "apollo-enrichment-v4"
_APOLLO_PERSON_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_APOLLO_PLACEHOLDER_IDS = frozenset(
    {"unknown", "none", "null", "placeholder", "redacted", "n/a"}
)


class ProviderUnavailable(RuntimeError):
    def __init__(
        self,
        reason: str,
        *,
        provider: str = "unknown",
        http_status: int | None = None,
        duration_ms: float | None = None,
        safe_metadata: dict[str, object] | None = None,
        code: PeopleErrorCode | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.provider = provider
        self.http_status = http_status
        self.duration_ms = duration_ms
        self.safe_metadata = safe_metadata or {}
        # The typed code is authoritative for circuit and fallback decisions.
        # ``reason`` stays as the persisted/wire representation.
        self.code = code or code_for_reason(reason)
        self.retry_after_seconds = retry_after_seconds

    @property
    def request_scoped(self) -> bool:
        return is_request_scoped(self.code)


def _retry_after_seconds(response: httpx.Response) -> int | None:
    """Honour Retry-After when the provider sends one, in either RFC format."""

    raw = (response.headers.get("Retry-After") or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return max(1, min(3600, int(raw)))
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(1, min(3600, int((when - datetime.now(UTC)).total_seconds() + 0.999)))


def account_fingerprint(api_key: str | None) -> str:
    """Non-reversible identity for a provider account.

    Circuit state must be keyed per credential so rotating a key starts from a
    clean state and two accounts cannot poison each other. The raw key is never
    stored, logged, or used as part of any key.
    """

    digest = hashlib.sha256(
        f"people-account:{api_key or 'unconfigured'}".encode()
    ).hexdigest()
    return digest[:16]


class PeopleDiscoveryProvider(Protocol):
    async def search_people(self, query: PeopleSearchQuery) -> list[ProviderPerson]: ...
    async def enrich_people(self, people: list[PersonEnrichmentRequest]) -> list[ProviderPerson]: ...
    async def get_usage(self) -> ProviderUsage: ...


class WorkEmailProvider(Protocol):
    async def find_work_email(self, request: WorkEmailRequest) -> WorkEmailResult: ...
    async def verify_work_email(self, email: str) -> EmailVerificationResult: ...


class _HttpProvider:
    def __init__(self, provider_name: str, api_key: str | None = None) -> None:
        self.provider_name = provider_name
        self.requests = 0
        self.credits = 0
        self.last_http_status: int | None = None
        self.last_duration_ms: float | None = None
        # Circuit state is scoped to this credential, never to the raw key.
        self.account_fingerprint = account_fingerprint(api_key)
        self.last_pdl_outcome: pdl_status.PdlOutcome | None = None
        self._usage_recorder: ProviderUsageRecorder | None = None
        self._usage_context: ProviderUsageContext | None = None
        self._usage_ordinals: dict[str, int] = defaultdict(int)

    def configure_usage(
        self,
        context: ProviderUsageContext,
        *,
        session_factory: SessionFactory,
    ) -> None:
        self._usage_context = context
        self._usage_recorder = ProviderUsageRecorder(
            context,
            session_factory=session_factory,
            unknown_credit_budget_units=(
                settings.people_provider_unknown_credit_budget_units
            ),
        )

    def _start_usage(self, operation_type: str) -> str | None:
        if self._usage_recorder is None or self._usage_context is None:
            return None
        self._usage_ordinals[operation_type] += 1
        key = operation_idempotency_key(
            self._usage_context,
            provider=self.provider_name,
            operation_type=operation_type,
            ordinal=self._usage_ordinals[operation_type],
        )
        if not self._usage_recorder.start(
            idempotency_key=key,
            provider=self.provider_name,
            operation_type=operation_type,
        ):
            raise ProviderUnavailable(
                "provider_operation_duplicate",
                provider=self.provider_name,
            )
        return key

    def _finish_usage(
        self,
        key: str | None,
        *,
        http_outcome: str,
        payload: object = None,
        operation_type: str | None = None,
        http_status: int | None = None,
        response_was_malformed: bool = False,
    ) -> None:
        if key is None or self._usage_recorder is None:
            return
        credits_reported = reported_credits(payload)
        credits_estimated: int | None = None
        estimate_when_unknown = True
        if operation_type == "complete_person_by_id":
            person_found = bool(
                isinstance(payload, dict)
                and _single_apollo_person(payload) is not None
            )
            if response_was_malformed:
                estimate_when_unknown = False
            elif credits_reported is None and http_status == 200 and person_found:
                credits_estimated = 1
            elif credits_reported is None and http_status in {200, 404}:
                credits_reported = 0
            elif credits_reported is None:
                estimate_when_unknown = False
        elif (
            self.provider_name == "pdl"
            and operation_type == "people_search"
            and credits_reported is None
        ):
            if (
                http_status == 200
                and isinstance(payload, dict)
                and isinstance(payload.get("data"), list)
            ):
                # PDL Person Search is record-metered. When the response does
                # not carry an explicit charge, the returned record count is a
                # bounded estimate rather than a fabricated reported value.
                credits_estimated = len(payload["data"])
            else:
                # Indeterminate failures keep a conservative budget unit but
                # never invent either a reported or estimated credit count.
                estimate_when_unknown = False
        self._usage_recorder.finish(
            idempotency_key=key,
            http_outcome=http_outcome,
            credits_reported=credits_reported,
            credits_estimated=credits_estimated,
            estimate_when_unknown=estimate_when_unknown,
        )

    def _failure(
        self,
        code: PeopleErrorCode,
        *,
        operation_type: str = "unspecified",
    ) -> None:
        """Record a failure against only the circuit its type is allowed to move.

        Request-scoped codes — bad input, an ordinary 4xx, an unresolved domain,
        a cancellation — return without touching any circuit, which is what keeps
        one malformed company from pausing every other company's search.
        """

        circuit.record_failure(
            provider=self.provider_name,
            account_fingerprint=self.account_fingerprint,
            operation=operation_type,
            code=code,
        )

    def _success(self, operation_type: str) -> None:
        """A healthy response, including a successful empty one, closes circuits."""

        circuit.record_success(
            provider=self.provider_name,
            account_fingerprint=self.account_fingerprint,
            operation=operation_type,
        )

    def _circuit_gate(self, operation_type: str) -> None:
        decision = circuit.allow(
            provider=self.provider_name,
            account_fingerprint=self.account_fingerprint,
            operation=operation_type,
        )
        if decision.allowed:
            return
        reason = {
            "configuration": "provider_configuration_circuit_open",
            "budget": "provider_budget_exceeded",
        }.get(decision.kind or "", "provider_circuit_open")
        raise ProviderUnavailable(
            reason,
            provider=self.provider_name,
            duration_ms=0,
            retry_after_seconds=decision.retry_after_seconds,
        )

    async def _request(
        self,
        method: str,
        url: str,
        *,
        operation_type: str,
        status_reason_overrides: dict[int, str] | None = None,
        malformed_response_reason: str = "provider_schema_error",
        pdl_endpoint: PdlEndpoint | None = None,
        **kwargs,
    ) -> dict:
        """Issue one provider request.

        ``pdl_endpoint`` opts into endpoint-aware classification. PDL answers a
        query that matched nothing with HTTP 404, so without it a company with
        no profiles is indistinguishable from a malformed request.
        """

        self._circuit_gate(operation_type)
        usage_key = self._start_usage(operation_type)
        self.requests += 1
        timeout = httpx.Timeout(settings.people_provider_timeout_seconds)
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                response = await client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            self._finish_usage(
                usage_key,
                http_outcome="provider_timeout",
                operation_type=operation_type,
            )
            self._failure(
                PeopleErrorCode.PROVIDER_TIMEOUT, operation_type=operation_type
            )
            raise ProviderUnavailable(
                "provider_timeout",
                provider=self.provider_name,
                duration_ms=(time.monotonic() - started) * 1000,
                code=PeopleErrorCode.PROVIDER_TIMEOUT,
            ) from exc
        except httpx.NetworkError as exc:
            self._finish_usage(
                usage_key,
                http_outcome="provider_network_error",
                operation_type=operation_type,
            )
            self._failure(
                PeopleErrorCode.NETWORK_ERROR, operation_type=operation_type
            )
            raise ProviderUnavailable(
                "provider_network_error",
                provider=self.provider_name,
                duration_ms=(time.monotonic() - started) * 1000,
                code=PeopleErrorCode.NETWORK_ERROR,
            ) from exc
        except asyncio.CancelledError:
            # A cancelled request — the client disconnected, or the caller gave
            # up — says nothing about provider health, so no failure counter
            # moves and the cancellation propagates untouched.
            self._finish_usage(
                usage_key,
                http_outcome="provider_request_cancelled",
                operation_type=operation_type,
            )
            raise
        duration_ms = (time.monotonic() - started) * 1000
        self.last_http_status = response.status_code
        self.last_duration_ms = duration_ms
        parsed_payload: object = None
        if (
            len(response.content)
            <= settings.people_provider_response_max_bytes
        ):
            try:
                parsed_payload = response.json()
            except ValueError:
                parsed_payload = None
        self._finish_usage(
            usage_key,
            http_outcome=f"http_{response.status_code}",
            payload=parsed_payload,
            operation_type=operation_type,
            http_status=response.status_code,
            response_was_malformed=parsed_payload is None,
        )
        retry_after = _retry_after_seconds(response)
        if pdl_endpoint is not None:
            return self._resolve_pdl_response(
                endpoint=pdl_endpoint,
                response=response,
                payload=parsed_payload,
                operation_type=operation_type,
                duration_ms=duration_ms,
                retry_after=retry_after,
                malformed_response_reason=malformed_response_reason,
            )
        reason = (status_reason_overrides or {}).get(response.status_code) or {
            401: "provider_unauthorized",
            403: "provider_forbidden",
            429: "provider_rate_limited",
            422: "provider_schema_error",
        }.get(response.status_code)
        if reason:
            # The typed code decides which circuit, if any, may move. A 422 or
            # other request-shaped rejection keeps its precise reason and stays
            # scoped to this request instead of pausing the provider.
            code = code_for_reason(reason)
            self._failure(code, operation_type=operation_type)
            raise ProviderUnavailable(
                reason,
                provider=self.provider_name,
                http_status=response.status_code,
                duration_ms=duration_ms,
                code=code,
                retry_after_seconds=retry_after,
                safe_metadata=(
                    (
                        {"error_types": ["validation_response_too_large"]}
                        if len(response.content)
                        > settings.people_provider_response_max_bytes
                        else _safe_apollo_validation_metadata(response)
                    )
                    if response.status_code == 422
                    else None
                ),
            )
        if len(response.content) > settings.people_provider_response_max_bytes:
            self._failure(
                PeopleErrorCode.INVALID_INPUT, operation_type=operation_type
            )
            raise ProviderUnavailable(
                "provider_schema_error",
                provider=self.provider_name,
                http_status=response.status_code,
                duration_ms=duration_ms,
                code=PeopleErrorCode.INVALID_INPUT,
            )
        if response.status_code >= 500:
            self._failure(
                PeopleErrorCode.PROVIDER_SERVER_ERROR,
                operation_type=operation_type,
            )
            raise ProviderUnavailable(
                "provider_unavailable",
                provider=self.provider_name,
                http_status=response.status_code,
                duration_ms=duration_ms,
                code=PeopleErrorCode.PROVIDER_SERVER_ERROR,
                retry_after_seconds=retry_after,
            )
        if response.status_code >= 400:
            # An ordinary 400/404 describes this request, not provider health.
            code = code_for_http_status(response.status_code)
            self._failure(code, operation_type=operation_type)
            raise ProviderUnavailable(
                reason_for_code(code),
                provider=self.provider_name,
                http_status=response.status_code,
                duration_ms=duration_ms,
                code=code,
                retry_after_seconds=retry_after,
            )
        if parsed_payload is None or not isinstance(parsed_payload, dict):
            self._failure(
                PeopleErrorCode.INVALID_INPUT, operation_type=operation_type
            )
            raise ProviderUnavailable(
                malformed_response_reason,
                provider=self.provider_name,
                http_status=response.status_code,
                duration_ms=duration_ms,
                code=PeopleErrorCode.INVALID_INPUT,
            )
        self._success(operation_type)
        return parsed_payload

    def _resolve_pdl_response(
        self,
        *,
        endpoint: PdlEndpoint,
        response: httpx.Response,
        payload: object,
        operation_type: str,
        duration_ms: float,
        retry_after: int | None,
        malformed_response_reason: str,
    ) -> dict:
        """Apply PDL's documented, endpoint-specific status semantics.

        A no-match is a *successful* answer: it closes circuits like any other
        healthy response and returns an empty result set, so the orchestration
        layer sees "nobody matched" rather than "the request was rejected".
        """

        if len(response.content) > settings.people_provider_response_max_bytes:
            self._failure(
                PeopleErrorCode.INVALID_INPUT, operation_type=operation_type
            )
            raise ProviderUnavailable(
                "provider_schema_error",
                provider=self.provider_name,
                http_status=response.status_code,
                duration_ms=duration_ms,
                code=PeopleErrorCode.INVALID_INPUT,
                safe_metadata={"provider_endpoint": endpoint},
            )
        outcome = pdl_status.classify(
            endpoint=endpoint,
            status_code=response.status_code,
            payload=payload,
        )
        self.last_pdl_outcome = outcome
        if outcome.no_match:
            self._success(operation_type)
            return {"data": [], "total": 0, "pdl_no_match": True}
        if not outcome.ok:
            assert outcome.code is not None and outcome.reason is not None
            self._failure(outcome.code, operation_type=operation_type)
            raise ProviderUnavailable(
                outcome.reason,
                provider=self.provider_name,
                http_status=response.status_code,
                duration_ms=duration_ms,
                code=outcome.code,
                retry_after_seconds=retry_after,
                safe_metadata=outcome.safe_metadata,
            )
        if payload is None or not isinstance(payload, dict):
            self._failure(
                PeopleErrorCode.INVALID_INPUT, operation_type=operation_type
            )
            raise ProviderUnavailable(
                malformed_response_reason,
                provider=self.provider_name,
                http_status=response.status_code,
                duration_ms=duration_ms,
                code=PeopleErrorCode.INVALID_INPUT,
                safe_metadata={"provider_endpoint": endpoint},
            )
        self._success(operation_type)
        return payload


class ApolloPeopleProvider(_HttpProvider):
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.apollo_api_key
        super().__init__("apollo", self.api_key)
        self._bulk_account_scope = hashlib.sha256(
            (self.api_key or "").encode()
        ).hexdigest()
        self.enrichment_rejection_reasons: dict[str, str] = {}
        self.enrichment_safe_metrics: dict[str, int] = {}
        self.search_identifier_safe_metrics: dict[str, object] = {}
        self.bulk_capability_state = bulk_capability_state(
            self.provider_name,
            APOLLO_BULK_CAPABILITY_VERSION,
            self._bulk_account_scope,
        )

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ProviderUnavailable("provider_not_configured", provider=self.provider_name)
        return {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _complete_person_headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ProviderUnavailable(
                "provider_not_configured",
                provider=self.provider_name,
            )
        return {
            "x-api-key": self.api_key,
            "Accept": "application/json",
        }

    async def search_people(self, query: PeopleSearchQuery) -> list[ProviderPerson]:
        payload = {
            "person_titles": query.titles,
            "q_organization_domains_list": (
                [query.company_domain] if query.company_domain else []
            ),
            "per_page": query.limit,
            "page": 1,
        }
        if query.seniorities:
            payload["person_seniorities"] = query.seniorities
        if query.location and query.location_filter_mode == "hard":
            payload["person_locations"] = [query.location]
        data = await self._request(
            "POST", "https://api.apollo.io/api/v1/mixed_people/api_search",
            operation_type="people_search",
            headers=self._headers(), json=payload,
        )
        rows = data.get("people")
        if not isinstance(rows, list):
            # An unexpected 200 body is an adapter/request-shape problem, not
            # evidence that the provider is down.
            self._failure(
                PeopleErrorCode.INVALID_INPUT, operation_type="people_search"
            )
            raise ProviderUnavailable(
                "provider_schema_error",
                provider=self.provider_name,
                http_status=self.last_http_status,
                duration_ms=self.last_duration_ms,
                code=PeopleErrorCode.INVALID_INPUT,
            )
        self.search_identifier_safe_metrics = _search_identifier_safe_metrics(rows)
        logger.info(
            "apollo_search_identifier_shape records_with_id=%s "
            "records_with_person_id=%s records_with_contact_id=%s accepted=%s "
            "rejected=%s id_type_distribution=%s id_length_distribution=%s "
            "adapter_version=%s",
            self.search_identifier_safe_metrics["records_with_id"],
            self.search_identifier_safe_metrics["records_with_person_id"],
            self.search_identifier_safe_metrics["records_with_contact_id"],
            self.search_identifier_safe_metrics["accepted_identifier_count"],
            self.search_identifier_safe_metrics["rejected_identifier_count"],
            self.search_identifier_safe_metrics["identifier_type_distribution"],
            self.search_identifier_safe_metrics["identifier_length_distribution"],
            APOLLO_ENRICHMENT_ADAPTER_VERSION,
        )
        normalized = [
            person
            for row in rows
            if (
                person := _normalize_apollo(
                    row,
                    fallback_company_domain=query.company_domain,
                    identifier_kind="search",
                )
            )
        ]
        if rows and not normalized:
            # An unexpected 200 body is an adapter/request-shape problem, not
            # evidence that the provider is down.
            self._failure(
                PeopleErrorCode.INVALID_INPUT, operation_type="people_search"
            )
            raise ProviderUnavailable(
                "provider_schema_error",
                provider=self.provider_name,
                http_status=self.last_http_status,
                duration_ms=self.last_duration_ms,
                code=PeopleErrorCode.INVALID_INPUT,
            )
        return normalized

    async def enrich_people(self, people: list[PersonEnrichmentRequest]) -> list[ProviderPerson]:
        if not people:
            return []
        self.enrichment_rejection_reasons = {}
        self.enrichment_safe_metrics = {}
        identifiers: list[str] = []
        seen: set[str] = set()
        for item in people:
            raw_identifier = item.provider_person_id
            identifier = _valid_apollo_person_id(raw_identifier)
            if identifier is None:
                self.enrichment_rejection_reasons[raw_identifier] = (
                    "invalid_provider_person_id"
                )
                self._increment_enrichment_metric("invalid_provider_person_id")
                self._increment_enrichment_metric(
                    _invalid_apollo_person_id_shape(raw_identifier)
                )
                continue
            if identifier in seen:
                self._increment_enrichment_metric("duplicate_provider_person_id")
                continue
            seen.add(identifier)
            identifiers.append(identifier)

        enriched: list[ProviderPerson] = []
        self.bulk_capability_state = bulk_capability_state(
            self.provider_name,
            APOLLO_BULK_CAPABILITY_VERSION,
            self._bulk_account_scope,
        )
        if self.bulk_capability_state in {
            "temporarily_rejected",
            "account_not_supported",
        }:
            self._increment_enrichment_metric("bulk_capability_skipped")
            return await self._complete_selected_people(people)
        for offset in range(0, len(identifiers), 10):
            batch = identifiers[offset : offset + 10]
            try:
                data = await self._bulk_enrichment_request(batch)
            except ProviderUnavailable as exc:
                if exc.reason != "provider_schema_error" or exc.http_status != 422:
                    raise
                self._increment_enrichment_metric("bulk_payload_validation_failed")
                _log_safe_apollo_validation(
                    endpoint="/api/v1/people/bulk_match",
                    metadata=exc.safe_metadata,
                )
                if (
                    exc.safe_metadata.get("error_scope") == "request_level"
                    and not exc.safe_metadata.get("field_paths")
                ):
                    self.bulk_capability_state = (
                        record_bulk_request_level_422(
                            self.provider_name,
                            APOLLO_BULK_CAPABILITY_VERSION,
                            self._bulk_account_scope,
                        )
                    )
                return await self._complete_selected_people(people)
            record_bulk_supported(
                self.provider_name,
                APOLLO_BULK_CAPABILITY_VERSION,
                self._bulk_account_scope,
            )
            self.bulk_capability_state = "supported"
            enriched.extend(self._normalize_bulk_matches(data, batch))
        return enriched

    def enrichment_rejection_reason(self, provider_person_id: str) -> str | None:
        return self.enrichment_rejection_reasons.get(provider_person_id)

    async def _bulk_enrichment_request(self, identifiers: list[str]) -> dict:
        if not identifiers:
            raise ValueError("Apollo bulk enrichment requires at least one identifier")
        headers = self._headers()
        payload = {"details": [{"id": identifier} for identifier in identifiers]}
        logger.info(
            "apollo_enrichment_request method=POST endpoint=/api/v1/people/bulk_match "
            "header_names=%s content_type=application/json json_transport=true "
            "top_level_keys=%s detail_count=%s detail_keys=%s query_param_names=%s "
            "adapter_version=%s",
            sorted(headers),
            sorted(payload),
            len(payload["details"]),
            ["id"],
            [],
            APOLLO_ENRICHMENT_ADAPTER_VERSION,
        )
        return await self._request(
            "POST",
            "https://api.apollo.io/api/v1/people/bulk_match",
            operation_type="bulk_enrichment",
            headers=headers,
            json=payload,
        )

    def _select_complete_person_requests(
        self,
        people: list[PersonEnrichmentRequest],
    ) -> list[PersonEnrichmentRequest]:
        category_caps = {
            "likely_recruiter": (
                settings.people_apollo_complete_person_max_recruiters
            ),
            "potential_hiring_manager": (
                settings.people_apollo_complete_person_max_managers
            ),
            "potential_referrer": (
                settings.people_apollo_complete_person_max_referrers
            ),
        }
        ranked = sorted(
            enumerate(people),
            key=lambda item: (
                -(item[1].rank_score or 0),
                item[0],
            ),
        )
        selected: list[PersonEnrichmentRequest] = []
        selected_ids: set[str] = set()
        category_counts: dict[str, int] = defaultdict(int)
        total_cap = max(
            0, settings.people_apollo_complete_person_max_per_job
        )
        for _, request in ranked:
            identifier = _valid_apollo_person_id(
                request.provider_person_id
            )
            if identifier is None:
                continue
            if identifier in selected_ids:
                continue
            category = request.category
            if category is not None:
                cap = max(0, category_caps.get(category, 0))
                if category_counts[category] >= cap:
                    continue
                category_counts[category] += 1
            if len(selected) >= total_cap:
                break
            selected.append(request)
            selected_ids.add(identifier)
        self._increment_enrichment_metric(
            "complete_person_selected", len(selected)
        )
        return selected

    async def _complete_selected_people(
        self,
        people: list[PersonEnrichmentRequest],
    ) -> list[ProviderPerson]:
        enriched: list[ProviderPerson] = []
        first_candidate_failure: ProviderUnavailable | None = None
        for request in self._select_complete_person_requests(people):
            try:
                person = await self.complete_person_by_id(request)
            except ProviderUnavailable as exc:
                if exc.reason in {
                    "provider_unauthorized",
                    "provider_master_key_required_or_forbidden",
                    "provider_rate_limited",
                    "provider_circuit_open",
                }:
                    raise
                self.enrichment_rejection_reasons[
                    request.provider_person_id
                ] = exc.reason
                self._increment_enrichment_metric(exc.reason)
                first_candidate_failure = first_candidate_failure or exc
                continue
            if person is None:
                self.enrichment_rejection_reasons[
                    request.provider_person_id
                ] = "enrichment_record_not_found"
                self._increment_enrichment_metric(
                    "enrichment_record_not_found"
                )
                continue
            enriched.append(person)
        if not enriched and first_candidate_failure is not None:
            raise first_candidate_failure
        return enriched

    async def complete_person_by_id(
        self,
        request: PersonEnrichmentRequest,
    ) -> ProviderPerson | None:
        identifier = _valid_apollo_person_id(request.provider_person_id)
        if identifier is None:
            raise ProviderUnavailable(
                "provider_request_invalid",
                provider=self.provider_name,
            )
        cached = get_complete_person(
            provider=self.provider_name,
            account_scope=self._bulk_account_scope,
            provider_person_id=identifier,
            adapter_version=APOLLO_ENRICHMENT_ADAPTER_VERSION,
            evidence_version=EMPLOYMENT_EVIDENCE_VERSION,
        )
        if cached:
            state = cached.get("state")
            if state == "success" and isinstance(
                cached.get("person"), dict
            ):
                try:
                    person = ProviderPerson.model_validate(cached["person"])
                except ValueError:
                    person = None
                if person is not None:
                    self._increment_enrichment_metric(
                        "complete_person_cache_hit"
                    )
                    return person
            if state == "not_found":
                self._increment_enrichment_metric(
                    "complete_person_cache_hit"
                )
                return None
            if state == "error":
                reason = str(
                    cached.get("reason") or "provider_unavailable"
                )
                raise ProviderUnavailable(
                    reason,
                    provider=self.provider_name,
                )
        try:
            data = await self._request(
                "GET",
                _complete_person_url(identifier),
                operation_type="complete_person_by_id",
                status_reason_overrides={
                    400: "provider_request_invalid",
                    401: "provider_unauthorized",
                    403: "provider_master_key_required_or_forbidden",
                    404: "enrichment_record_not_found",
                    422: "provider_schema_error",
                    429: "provider_rate_limited",
                },
                malformed_response_reason="provider_response_invalid",
                headers=self._complete_person_headers(),
            )
        except ProviderUnavailable as exc:
            if exc.reason == "enrichment_record_not_found":
                cache_complete_person_not_found(
                    provider=self.provider_name,
                    account_scope=self._bulk_account_scope,
                    provider_person_id=identifier,
                    adapter_version=APOLLO_ENRICHMENT_ADAPTER_VERSION,
                    evidence_version=EMPLOYMENT_EVIDENCE_VERSION,
                )
                return None
            cache_complete_person_error(
                exc.reason,
                provider=self.provider_name,
                account_scope=self._bulk_account_scope,
                provider_person_id=identifier,
                adapter_version=APOLLO_ENRICHMENT_ADAPTER_VERSION,
                evidence_version=EMPLOYMENT_EVIDENCE_VERSION,
                non_retryable=exc.reason
                in {
                    "provider_unauthorized",
                    "provider_master_key_required_or_forbidden",
                },
            )
            raise
        row = _single_apollo_person(data)
        if row is None:
            cache_complete_person_not_found(
                provider=self.provider_name,
                account_scope=self._bulk_account_scope,
                provider_person_id=identifier,
                adapter_version=APOLLO_ENRICHMENT_ADAPTER_VERSION,
                evidence_version=EMPLOYMENT_EVIDENCE_VERSION,
            )
            return None
        person = _normalize_apollo(
            row,
            identifier_kind="complete_person",
        )
        if person is None:
            cache_complete_person_error(
                "provider_response_invalid",
                provider=self.provider_name,
                account_scope=self._bulk_account_scope,
                provider_person_id=identifier,
                adapter_version=APOLLO_ENRICHMENT_ADAPTER_VERSION,
                evidence_version=EMPLOYMENT_EVIDENCE_VERSION,
                non_retryable=False,
            )
            raise ProviderUnavailable(
                "provider_response_invalid",
                provider=self.provider_name,
                http_status=self.last_http_status,
                duration_ms=self.last_duration_ms,
            )
        if person.provider_person_id != identifier:
            self._increment_enrichment_metric(
                "enrichment_correlation_failed"
            )
            cache_complete_person_error(
                "provider_response_invalid",
                provider=self.provider_name,
                account_scope=self._bulk_account_scope,
                provider_person_id=identifier,
                adapter_version=APOLLO_ENRICHMENT_ADAPTER_VERSION,
                evidence_version=EMPLOYMENT_EVIDENCE_VERSION,
                non_retryable=False,
            )
            raise ProviderUnavailable(
                "provider_response_invalid",
                provider=self.provider_name,
                http_status=self.last_http_status,
                duration_ms=self.last_duration_ms,
            )
        self._record_credits(data)
        cache_complete_person_success(
            person,
            account_scope=self._bulk_account_scope,
            adapter_version=APOLLO_ENRICHMENT_ADAPTER_VERSION,
            evidence_version=EMPLOYMENT_EVIDENCE_VERSION,
        )
        return person

    async def _single_enrichment_after_bulk_422(
        self, identifier: str
    ) -> ProviderPerson | None:
        """Compatibility shim for callers; the ID is now a GET path parameter."""
        try:
            return await self.complete_person_by_id(
                PersonEnrichmentRequest(provider_person_id=identifier)
            )
        except ProviderUnavailable as exc:
            if exc.reason == "provider_schema_error" and exc.http_status == 422:
                self.enrichment_rejection_reasons[identifier] = (
                    "single_enrichment_validation_failed"
                )
                self._increment_enrichment_metric(
                    "single_enrichment_validation_failed"
                )
                _log_safe_apollo_validation(
                    endpoint="/api/v1/people/{id}",
                    metadata=exc.safe_metadata,
                )
                return None
            raise

    def _normalize_bulk_matches(
        self, data: dict, requested_identifiers: list[str]
    ) -> list[ProviderPerson]:
        rows = _bulk_apollo_matches(data)
        if rows is None:
            raise ProviderUnavailable(
                "provider_schema_error",
                provider=self.provider_name,
                http_status=self.last_http_status,
                duration_ms=self.last_duration_ms,
            )
        self._record_credits(data)
        requested = set(requested_identifiers)
        matched: dict[str, ProviderPerson] = {}
        for row in rows:
            person = _normalize_apollo(row, identifier_kind="enrichment")
            if (
                person is None
                or person.provider_person_id not in requested
                or person.provider_person_id in matched
            ):
                self._increment_enrichment_metric("enrichment_correlation_failed")
                continue
            matched[person.provider_person_id] = person
        for identifier in requested_identifiers:
            if identifier not in matched:
                self.enrichment_rejection_reasons[identifier] = (
                    "enrichment_record_not_found"
                )
                self._increment_enrichment_metric("enrichment_record_not_found")
        return [matched[value] for value in requested_identifiers if value in matched]

    def _record_credits(self, data: dict) -> None:
        credits = data.get("credits_consumed")
        if credits is None and isinstance(data.get("data"), dict):
            credits = data["data"].get("credits_consumed")
        if isinstance(credits, int) and not isinstance(credits, bool) and credits >= 0:
            self.credits += credits

    def _increment_enrichment_metric(
        self,
        reason: str,
        value: int = 1,
    ) -> None:
        self.enrichment_safe_metrics[reason] = (
            self.enrichment_safe_metrics.get(reason, 0) + value
        )

    async def get_usage(self) -> ProviderUsage:
        return ProviderUsage(provider="apollo", credits_used=self.credits, requests=self.requests)


class PDLPeopleProvider(_HttpProvider):
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.pdl_api_key
        super().__init__("pdl", self.api_key)
        self._search_profiles: dict[str, ProviderPerson] = {}
        self.last_search_raw_count = 0
        self.last_search_normalized_count = 0
        # Bounded across every category and ladder rung of one discovery, so
        # relaxation can never multiply provider spend without a ceiling.
        self.search_calls = 0
        self.strategy_calls: list[dict[str, object]] = []

    def _headers(self) -> dict[str, str]:
        return {
            "X-Api-Key": self.api_key or "",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @property
    def call_budget_remaining(self) -> int:
        return max(
            0,
            settings.people_pdl_max_provider_calls_per_discovery - self.search_calls,
        )

    async def resolve_company(
        self,
        *,
        raw_name: str,
        normalized_name: str,
        aliases: tuple[str, ...] = (),
        verified_domain: str | None = None,
    ) -> pdl_company.PdlCompanyIdentity:
        """Resolve the hiring company to a verified PDL company id.

        Cached both ways: a resolved identity avoids re-spending an enrichment
        call, and an unresolved one avoids re-spending it to learn the same
        thing. Never guesses — an answer PDL is unsure about, or one that names
        a different organization, is rejected.
        """

        cached = pdl_company.identity_from_cache(
            raw_name=raw_name,
            normalized_name=normalized_name,
            domain=verified_domain,
        )
        if cached is not None:
            return cached

        unresolved = pdl_company.PdlCompanyIdentity(
            raw_name=raw_name,
            normalized_name=normalized_name,
            aliases=aliases,
            verified_domain=verified_domain,
        )
        if not self.api_key:
            raise ProviderUnavailable(
                "provider_not_configured",
                provider=self.provider_name,
                code=PeopleErrorCode.AUTHENTICATION_FAILED,
            )
        if not settings.people_pdl_company_resolution_enabled:
            return unresolved

        # Strongest evidence first: the verified domain, then the canonical
        # name, then a known alias.
        attempts: list[tuple[str, dict[str, str], str]] = []
        if verified_domain:
            attempts.append(
                (
                    "pdl_company_enrich_domain",
                    {"website": verified_domain},
                    raw_name,
                )
            )
        attempts.append(
            ("pdl_company_enrich_name", {"name": raw_name}, raw_name)
        )
        for alias in aliases:
            if alias and alias != raw_name:
                attempts.append(
                    ("pdl_company_enrich_alias", {"name": alias}, alias)
                )

        identity = unresolved
        for source, params, asked_name in attempts[:3]:
            if self.call_budget_remaining <= 0:
                break
            try:
                company = await self._company_enrich(params)
            except ProviderUnavailable as exc:
                if exc.code in {
                    PeopleErrorCode.AUTHENTICATION_FAILED,
                    PeopleErrorCode.AUTHORIZATION_FAILED,
                    PeopleErrorCode.PROVIDER_BUDGET_EXHAUSTED,
                }:
                    raise
                # A transient or request-scoped company-lookup failure leaves
                # the company unresolved; it never fails the whole search.
                logger.info(
                    "pdl_company_resolution_failed source=%s reason=%s",
                    source,
                    exc.reason,
                )
                continue
            if company is None:
                continue
            identity = pdl_company.build_identity(
                raw_name=raw_name,
                normalized_name=normalized_name,
                aliases=aliases,
                verified_domain=verified_domain,
                company=company,
                source=source,
                asked_name=asked_name,
            )
            if identity.resolved:
                break
        pdl_company.cache_identity(identity, domain=verified_domain)
        metric(
            "people_company_resolution_total",
            provider="pdl",
            result="resolved" if identity.resolved else "unresolved",
            source=identity.source,
        )
        logger.info(
            "pdl_company_resolution company=%r normalized=%r domain=%s "
            "pdl_company_id=%s source=%s confidence=%.2f rejected=%s",
            raw_name,
            normalized_name,
            verified_domain or "none",
            identity.pdl_company_id or "none",
            identity.source,
            identity.confidence,
            identity.rejection_reason or "none",
        )
        return identity

    async def _company_enrich(self, params: dict[str, str]) -> dict | None:
        """One Company Enrichment call. Returns ``None`` for a clean no-match."""

        self.search_calls += 1
        payload = await self._request(
            "GET",
            "https://api.peopledatalabs.com/v5/company/enrich",
            operation_type="company_enrichment",
            pdl_endpoint="company_enrichment",
            headers=self._headers(),
            params={
                **params,
                "min_likelihood": str(
                    max(1, settings.people_pdl_company_min_likelihood)
                ),
            },
        )
        if payload.get("pdl_no_match"):
            return None
        return payload

    async def search_current_company_people(
        self,
        *,
        company: pdl_company.PdlCompanyIdentity,
        category: PeopleCategory,
        role_family: str | None,
        job_location: str | None,
        limit: int,
    ) -> list[ProviderPerson]:
        """Find current employees of one company in one category.

        Walks the bounded relaxation ladder and stops at the first rung that
        returns anyone. Only title precision relaxes; every rung stays pinned
        to a verified company identity.
        """

        if not self.api_key:
            raise ProviderUnavailable(
                "provider_not_configured",
                provider=self.provider_name,
                code=PeopleErrorCode.AUTHENTICATION_FAILED,
            )
        self.last_search_raw_count = 0
        self.last_search_normalized_count = 0
        if not company.searchable:
            # No verified company evidence at all: refusing to search is the
            # only safe answer, because an unpinned query returns strangers.
            raise ProviderUnavailable(
                "company_domain_unresolved",
                provider=self.provider_name,
                code=PeopleErrorCode.COMPANY_DOMAIN_UNRESOLVED,
            )
        region, country = _split_location(job_location)
        inputs = pdl_query.LadderInputs(
            pdl_company_id=company.pdl_company_id,
            verified_domain=company.verified_domain,
            pdl_company_name=company.pdl_company_name,
            raw_company_name=company.raw_name,
            aliases=company.aliases,
            role_family=role_family,
            location_region=region,
            location_country=country,
            size=min(limit, settings.people_pdl_search_result_limit, 100),
            location_required=settings.people_pdl_location_required,
        )
        max_strategies = (
            max(1, settings.people_pdl_max_query_strategies)
            if settings.people_pdl_progressive_search_enabled
            else 1
        )
        ladder = pdl_query.build_ladder(
            category, inputs, max_strategies=max_strategies
        )
        collected: dict[str, ProviderPerson] = {}
        for strategy in ladder:
            if self.call_budget_remaining <= 0:
                logger.info(
                    "pdl_search_call_budget_exhausted category=%s calls=%s",
                    category,
                    self.search_calls,
                )
                break
            rows = await self._run_search_strategy(strategy, category)
            for person in rows:
                collected.setdefault(person.provider_person_id, person)
            if collected:
                # The ladder exists to find *someone*; once a rung does, a
                # broader rung would only add lower-precision matches.
                break
        normalized = list(collected.values())
        self.last_search_normalized_count = len(normalized)
        for person in normalized:
            self._search_profiles[person.provider_person_id] = person
        return normalized

    async def _run_search_strategy(
        self, strategy: pdl_query.PdlSearchStrategy, category: PeopleCategory
    ) -> list[ProviderPerson]:
        self.search_calls += 1
        started = time.monotonic()
        data = await self._request(
            "POST",
            "https://api.peopledatalabs.com/v5/person/search",
            operation_type="people_search",
            pdl_endpoint="person_search",
            headers=self._headers(),
            json={"sql": strategy.sql, "size": strategy.size},
        )
        rows = data.get("data")
        if not isinstance(rows, list):
            self._failure(
                PeopleErrorCode.INVALID_INPUT, operation_type="people_search"
            )
            raise ProviderUnavailable(
                "provider_schema_error",
                provider=self.provider_name,
                http_status=self.last_http_status,
                duration_ms=self.last_duration_ms,
                code=PeopleErrorCode.INVALID_INPUT,
            )
        normalized = [
            person for row in rows[: strategy.size] if (person := _normalize_pdl(row))
        ]
        self.last_search_raw_count += len(rows)
        self.credits += len(rows)
        for person in normalized:
            person.discovery_strategy = strategy.name
        self.strategy_calls.append(
            {
                "strategy": strategy.name,
                "category": category,
                "company_binding": strategy.company_binding,
                "http_status": self.last_http_status,
                "raw_count": len(rows),
                "normalized_count": len(normalized),
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
                "no_match": bool(data.get("pdl_no_match")),
            }
        )
        logger.info(
            "pdl_search_strategy category=%s strategy=%s binding=%s status=%s "
            "raw=%s normalized=%s calls=%s",
            category,
            strategy.name,
            strategy.company_binding,
            self.last_http_status,
            len(rows),
            len(normalized),
            self.search_calls,
        )
        return normalized

    async def search_people(self, query: PeopleSearchQuery) -> list[ProviderPerson]:
        self.last_search_raw_count = 0
        self.last_search_normalized_count = 0
        if not self.api_key:
            raise ProviderUnavailable(
                "provider_not_configured",
                provider=self.provider_name,
                code=PeopleErrorCode.AUTHENTICATION_FAILED,
            )
        safe_domain = _pdl_sql_value(
            _provider_company_domain(query.company_domain or "")
        )
        safe_company = _pdl_sql_value(query.company_name)
        safe_titles = [
            _pdl_sql_value(title)
            for title in query.titles[:20]
            if _pdl_sql_value(title)
        ]
        if not safe_domain:
            # An unresolved hiring-company domain is a fact about this job, not
            # a provider failure, and it must be distinguishable from a genuine
            # empty result. No provider call is made and no credit is spent.
            raise ProviderUnavailable(
                "company_domain_unresolved",
                provider=self.provider_name,
                code=PeopleErrorCode.COMPANY_DOMAIN_UNRESOLVED,
            )
        if not safe_titles:
            raise ProviderUnavailable(
                "provider_request_invalid",
                provider=self.provider_name,
                code=PeopleErrorCode.INVALID_INPUT,
            )
        company_clause = f"job_company_website='{safe_domain}'"
        if safe_company:
            company_clause = (
                f"({company_clause} OR job_company_name='{safe_company}')"
            )
        title_clause = ",".join(f"'{value}'" for value in safe_titles)
        filters = [
            company_clause,
            f"job_title IN ({title_clause})",
        ]
        safe_seniorities = [
            _pdl_sql_value(value)
            for value in query.seniorities[:10]
            if _pdl_sql_value(value)
        ]
        if safe_seniorities:
            seniority_clause = ",".join(
                f"'{value}'" for value in safe_seniorities
            )
            filters.append(
                f"job_title_levels IN ({seniority_clause})"
            )
        sql = "SELECT * FROM person WHERE " + " AND ".join(filters)
        result_limit = min(
            query.limit,
            settings.people_pdl_results_per_query,
            100,
        )
        data = await self._request(
            "POST", "https://api.peopledatalabs.com/v5/person/search",
            operation_type="people_search",
            # The broadened path reaches this adapter too, and PDL answers a
            # query that matched nothing with 404 here exactly as it does for
            # the ladder. Without this it reads as a rejected request.
            pdl_endpoint="person_search",
            headers={
                "X-Api-Key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "sql": sql,
                "size": result_limit,
            },
        )
        rows = data.get("data")
        if not isinstance(rows, list):
            # An unexpected 200 body is an adapter/request-shape problem, not
            # evidence that the provider is down.
            self._failure(
                PeopleErrorCode.INVALID_INPUT, operation_type="people_search"
            )
            raise ProviderUnavailable(
                "provider_schema_error",
                provider=self.provider_name,
                http_status=self.last_http_status,
                duration_ms=self.last_duration_ms,
                code=PeopleErrorCode.INVALID_INPUT,
            )
        normalized = [
            person
            for row in rows[:result_limit]
            if (person := _normalize_pdl(row))
        ]
        self.last_search_raw_count = len(rows)
        self.last_search_normalized_count = len(normalized)
        self.credits += len(rows)
        for person in normalized:
            self._search_profiles[person.provider_person_id] = person
        return normalized

    async def search_people_category(
        self,
        queries: list[PeopleSearchQuery],
        *,
        limit: int,
    ) -> list[ProviderPerson]:
        first = queries[0] if queries else None
        if first is None or limit <= 0:
            return []
        titles = list(
            dict.fromkeys(
                title
                for query in queries
                for title in query.titles
                if title.strip()
            )
        )[:20]
        return await self.search_people(
            PeopleSearchQuery(
                category=first.category,
                company_name=first.company_name,
                company_domain=first.company_domain,
                company_aliases=first.company_aliases,
                titles=titles,
                title_group=f"{first.category}_bounded",
                seniorities=[],
                location=first.location,
                location_filter_mode="soft",
                company_match_kind="canonical",
                role_family=first.role_family,
                department=first.department,
                limit=limit,
            )
        )

    async def enrich_people(self, people: list[PersonEnrichmentRequest]) -> list[ProviderPerson]:
        # Person Search already returns the profile and employment fields used
        # by employment-v2.1. Reusing those normalized records avoids a second
        # provider and a second metered operation.
        wanted = {
            item.provider_person_id
            for item in people
            if item.provider_person_id
        }
        return [
            person
            for identifier, person in self._search_profiles.items()
            if identifier in wanted
        ]

    async def get_usage(self) -> ProviderUsage:
        return ProviderUsage(provider="pdl", credits_used=self.credits, requests=self.requests)


class HunterEmailProvider(_HttpProvider):
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.hunter_api_key
        super().__init__("hunter", self.api_key)

    async def find_work_email(self, request: WorkEmailRequest) -> WorkEmailResult:
        if not self.api_key:
            raise ProviderUnavailable("provider_not_configured", provider=self.provider_name)
        parts = request.full_name.strip().split()
        data = await self._request(
            "GET", "https://api.hunter.io/v2/email-finder",
            operation_type="email_discovery",
            params={"domain": request.company_domain, "first_name": parts[0], "last_name": parts[-1], "api_key": self.api_key},
        )
        self.credits += 1
        email = (data.get("data") or {}).get("email")
        return WorkEmailResult(
            status="unknown" if email else "not_found",
            email=email if isinstance(email, str) else None,
            professional=bool(email),
            provider="hunter",
        )

    async def verify_work_email(self, email: str) -> EmailVerificationResult:
        if not self.api_key:
            raise ProviderUnavailable("provider_not_configured", provider=self.provider_name)
        data = await self._request(
            "GET", "https://api.hunter.io/v2/email-verifier",
            operation_type="email_verification",
            params={"email": email, "api_key": self.api_key},
        )
        self.credits += 1
        result = str((data.get("data") or {}).get("result") or "unknown").lower()
        status = {"deliverable": "verified", "accept_all": "accept_all", "risky": "risky", "undeliverable": "not_found"}.get(result, "unknown")
        return EmailVerificationResult(status=status, provider="hunter", verified_at=datetime.now(UTC))


class MockPeopleProvider:
    """Synthetic-only local/test adapter. Records are injected, never fabricated from a job."""

    def __init__(self, records: list[ProviderPerson] | None = None) -> None:
        self.records = records or []
        self.requests = 0

    async def search_people(self, query: PeopleSearchQuery) -> list[ProviderPerson]:
        self.requests += 1
        return [
            row for row in self.records
            if (not query.company_domain or row.current_company_domain == query.company_domain)
        ][: query.limit]

    async def enrich_people(self, people: list[PersonEnrichmentRequest]) -> list[ProviderPerson]:
        wanted = {item.provider_person_id for item in people}
        return [row for row in self.records if row.provider_person_id in wanted]

    async def get_usage(self) -> ProviderUsage:
        return ProviderUsage(provider="mock", credits_used=0, requests=self.requests)


def get_people_provider() -> PeopleDiscoveryProvider:
    provider = settings.people_primary_provider.strip().lower()
    if provider == "mock":
        if settings.app_env not in {"test", "development"}:
            raise ProviderUnavailable("mock_provider_forbidden")
        return MockPeopleProvider()
    if provider == "pdl":
        if not settings.people_pdl_discovery_enabled:
            raise ProviderUnavailable(
                "provider_not_configured", provider="pdl"
            )
        return PDLPeopleProvider()
    if provider == "apollo":
        if not (
            settings.people_apollo_discovery_enabled
            and settings.people_apollo_diagnostic_enabled
            and settings.people_rollout_mode == "internal"
        ):
            raise ProviderUnavailable(
                "provider_not_configured", provider="apollo"
            )
        return ApolloPeopleProvider()
    raise ProviderUnavailable(
        "provider_not_configured",
        provider=provider or "unknown",
    )


def get_email_provider() -> WorkEmailProvider:
    return HunterEmailProvider()


def _valid_apollo_person_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if value != value.strip() or not value:
        return None
    if value.lower() in _APOLLO_PLACEHOLDER_IDS:
        return None
    if "*" in value or not _APOLLO_PERSON_ID.fullmatch(value):
        return None
    return value


def _complete_person_url(identifier: str) -> str:
    return (
        "https://api.apollo.io/api/v1/people/"
        f"{quote(identifier, safe='')}"
    )


def _invalid_apollo_person_id_shape(value: object) -> str:
    if value is None:
        return "null_provider_person_id"
    if not isinstance(value, str):
        return "non_string_provider_person_id"
    if not value.strip():
        return "blank_provider_person_id"
    if "*" in value or value.strip().lower() in _APOLLO_PLACEHOLDER_IDS:
        return "placeholder_provider_person_id"
    return "malformed_provider_person_id"


def _apollo_person_id(row: dict, *, identifier_kind: str) -> str | None:
    # Apollo documents People Search's `id` as the identity to copy unchanged
    # into bulk_match details[].id. contact_id and organization/account IDs are
    # different namespaces and are never fallbacks.
    return _valid_apollo_person_id(row.get("id"))


def _identifier_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        return "string"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "other"


def _search_identifier_safe_metrics(rows: list[object]) -> dict[str, object]:
    records_with_id = 0
    records_with_person_id = 0
    records_with_contact_id = 0
    accepted = 0
    type_distribution: dict[str, int] = {}
    length_distribution: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            type_distribution["missing"] = type_distribution.get("missing", 0) + 1
            continue
        records_with_id += int("id" in row)
        records_with_person_id += int("person_id" in row)
        records_with_contact_id += int("contact_id" in row)
        value = row.get("id")
        value_type = _identifier_type(value)
        type_distribution[value_type] = type_distribution.get(value_type, 0) + 1
        if isinstance(value, str):
            length = str(len(value))
            length_distribution[length] = length_distribution.get(length, 0) + 1
        if _valid_apollo_person_id(value) is not None:
            accepted += 1
    return {
        "records_with_id": records_with_id,
        "records_with_person_id": records_with_person_id,
        "records_with_contact_id": records_with_contact_id,
        "accepted_identifier_count": accepted,
        "rejected_identifier_count": len(rows) - accepted,
        "identifier_type_distribution": dict(sorted(type_distribution.items())),
        "identifier_length_distribution": dict(sorted(length_distribution.items())),
    }


def _bulk_apollo_matches(data: dict) -> list[object] | None:
    candidates = [
        data.get("matches"),
        data.get("people"),
    ]
    nested = data.get("data")
    if isinstance(nested, dict):
        candidates.extend([nested.get("matches"), nested.get("people")])
    for value in candidates:
        if isinstance(value, list):
            return value
    return None


def _single_apollo_person(data: dict) -> object | None:
    candidates = [data.get("person"), data.get("match")]
    nested = data.get("data")
    if isinstance(nested, dict):
        candidates.extend([nested.get("person"), nested.get("match")])
        if _looks_like_apollo_person(nested):
            candidates.append(nested)
    if _looks_like_apollo_person(data):
        candidates.append(data)
    return next((value for value in candidates if isinstance(value, dict)), None)


def _looks_like_apollo_person(value: dict) -> bool:
    return bool(
        _valid_apollo_person_id(value.get("id"))
        and (
            isinstance(value.get("organization"), dict)
            or isinstance(value.get("employment_history"), list)
        )
    )


def _safe_validation_token(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    token = re.sub(r"[^A-Za-z0-9_.-]", "", value)[:80]
    return token or None


def _safe_apollo_validation_metadata(
    response: httpx.Response,
) -> dict[str, object]:
    fallback = {
        "error_types": ["provider_bulk_validation_failed"],
        "field_paths": [],
        "expected_types": [],
        "missing_required": False,
        "error_scope": "request_level",
    }
    try:
        payload = response.json()
    except ValueError:
        return fallback
    if not isinstance(payload, dict):
        return fallback
    values = payload.get("detail") or payload.get("errors") or []
    if isinstance(values, dict):
        values = [values]
    if not isinstance(values, list):
        values = []
    error_types: set[str] = set()
    field_paths: set[str] = set()
    expected_types: set[str] = set()
    missing_required = False
    for item in values[:10]:
        if not isinstance(item, dict):
            continue
        for key in ("type", "code", "error_code"):
            if token := _safe_validation_token(item.get(key)):
                error_types.add(token)
                missing_required = missing_required or "missing" in token.lower()
        location = item.get("loc") or item.get("path") or item.get("field")
        if isinstance(location, list):
            parts = [
                "*" if isinstance(part, int) else _safe_validation_token(part)
                for part in location
            ]
            if parts and all(parts):
                field_paths.add(".".join(str(part) for part in parts))
        elif token := _safe_validation_token(location):
            field_paths.add(token)
        context = item.get("ctx")
        expected = (
            context.get("expected")
            if isinstance(context, dict)
            else item.get("expected")
        )
        if token := _safe_validation_token(expected):
            expected_types.add(token)
    error_scope = (
        "record_level"
        if any(
            path.startswith("details.") or ".details." in path
            for path in field_paths
        )
        else "request_level"
    )
    result: dict[str, object] = {
        "error_types": (
            sorted(error_types)
            if field_paths
            else ["provider_bulk_validation_failed"]
        ),
        "field_paths": sorted(field_paths),
        "expected_types": sorted(expected_types),
        "missing_required": missing_required,
        "error_scope": error_scope,
    }
    return result


def _log_safe_apollo_validation(
    *, endpoint: str, metadata: dict[str, object]
) -> None:
    logger.warning(
        "apollo_enrichment_validation endpoint=%s http_status=422 error_types=%s "
        "field_paths=%s expected_types=%s missing_required=%s error_scope=%s",
        endpoint,
        metadata.get("error_types", []),
        metadata.get("field_paths", []),
        metadata.get("expected_types", []),
        bool(metadata.get("missing_required")),
        metadata.get("error_scope", "request_level"),
    )


def _normalize_apollo(
    row: object,
    *,
    fallback_company_domain: str | None = None,
    identifier_kind: str = "enrichment",
) -> ProviderPerson | None:
    if not isinstance(row, dict):
        return None
    organization = row.get("organization") if isinstance(row.get("organization"), dict) else {}
    name = str(row.get("name") or "").strip()
    if not name:
        name = " ".join(
            value
            for value in (
                str(row.get("first_name") or "").strip(),
                str(row.get("last_name_obfuscated") or "").strip(),
            )
            if value
        )
    title = str(row.get("title") or "").strip()
    company = str(organization.get("name") or "").strip()
    identifier = _apollo_person_id(row, identifier_kind=identifier_kind)
    if not all((name, title, company, identifier)):
        return None
    linkedin_url = row.get("linkedin_url")
    if isinstance(linkedin_url, str) and linkedin_url.startswith("http://"):
        linkedin_url = f"https://{linkedin_url.removeprefix('http://')}"
    observed_at = datetime.now(UTC)
    is_complete_person = identifier_kind == "complete_person"
    employment_verified = _provider_datetime(
        row.get("employment_verified_at")
    )
    employment_updated = _provider_datetime(
        row.get("employment_updated_at")
        if is_complete_person
        else (
            row.get("employment_verified_at")
            or row.get("last_refreshed_at")
            or row.get("updated_at")
        )
    )
    record_observed = _provider_datetime(
        row.get("last_refreshed_at") or row.get("updated_at")
    )
    if not record_observed:
        record_observed = observed_at
    employment_history = (
        row.get("employment_history")
        if isinstance(row.get("employment_history"), list)
        else []
    )
    previous_employers = [
        str(item.get("organization_name") or "").strip()
        for item in employment_history
        if isinstance(item, dict)
        and str(item.get("organization_name") or "").strip()
        and str(item.get("organization_name") or "").strip().lower()
        != company.lower()
    ]
    employment_source = (
        "complete_person_by_id"
        if is_complete_person
        else "provider_current_listing"
    )
    return ProviderPerson(
        provider="apollo", provider_person_id=identifier, full_name=name,
        current_company_name=company,
        current_company_domain=(
            str(organization.get("primary_domain") or "").lower()
            or fallback_company_domain
        ),
        current_title=title, department=str(row.get("departments", [""])[0] if row.get("departments") else "") or None,
        seniority=str(row.get("seniority") or "") or None,
        location=", ".join(str(row.get(k)) for k in ("city", "state", "country") if row.get(k)) or None,
        linkedin_url=safe_profile_url(linkedin_url),
        source_profile_url=safe_profile_url(linkedin_url),
        source_last_updated_at=employment_updated,
        provider_record_observed_at=record_observed,
        provider_employment_updated_at=employment_updated,
        employment_verified_at=employment_verified,
        employment_source=employment_source,
        current_role_indicator=True,
        education=[str(v.get("school_name")) for v in row.get("education", []) if isinstance(v, dict) and v.get("school_name")],
        previous_employers=previous_employers,
        evidence={
            "employment_source": employment_source,
            "current_company_name": company,
            "current_company_domain": (
                str(organization.get("primary_domain") or "").lower() or None
            ),
            "current_title": title,
            "employment_verified_at": (
                employment_verified.isoformat()
                if employment_verified
                else None
            ),
            "provider_record_observed_at": record_observed.isoformat(),
            "provider_employment_updated_at": (
                employment_updated.isoformat() if employment_updated else None
            ),
            "current_role_indicator": True,
        },
        field_provenance={"name": "apollo", "title": "apollo", "company": "apollo"},
    )


def _normalize_pdl(row: object) -> ProviderPerson | None:
    if not isinstance(row, dict):
        return None
    name = str(row.get("full_name") or "").strip()
    title = str(row.get("job_title") or "").strip()
    company = str(row.get("job_company_name") or "").strip()
    identifier = str(row.get("id") or "").strip()
    if not all((name, title, company, identifier)):
        return None
    observed_at = datetime.now(UTC)
    employment_updated = _provider_datetime(row.get("job_last_changed"))
    provider_verified = _provider_datetime(row.get("job_last_verified"))
    if (
        provider_verified
        and (
            employment_updated is None
            or provider_verified > employment_updated
        )
    ):
        employment_updated = provider_verified
    company_domain = _provider_company_domain(row.get("job_company_website"))
    experience = row.get("experience")
    experience_rows = experience if isinstance(experience, list) else []
    employment_start = row.get("job_start_date")
    employment_end = row.get("job_end_date")
    current_role_indicator = not bool(employment_end)
    conflicting_current_role = False
    previous_employers: list[str] = []
    for value in experience_rows:
        if not isinstance(value, dict):
            continue
        employer = value.get("company")
        employer_name = (
            str(employer.get("name") or "").strip()
            if isinstance(employer, dict)
            else ""
        )
        is_current = not bool(value.get("end_date"))
        if (
            normalize_provider_text(employer_name)
            == normalize_provider_text(company)
        ):
            employment_start = (
                value.get("start_date") or employment_start
            )
            employment_end = value.get("end_date") or employment_end
            current_role_indicator = not bool(employment_end)
        elif employer_name and is_current:
            conflicting_current_role = True
        if (
            employer_name
            and normalize_provider_text(employer_name)
            != normalize_provider_text(company)
        ):
            previous_employers.append(employer_name)
    return ProviderPerson(
        provider="pdl", provider_person_id=identifier, full_name=name,
        current_company_name=company, current_company_domain=company_domain,
        current_title=title, department=str(row.get("job_title_role") or "") or None,
        seniority=str(row.get("job_title_levels", [""])[0] if row.get("job_title_levels") else "") or None,
        location=str(row.get("location_name") or "") or None,
        linkedin_url=safe_profile_url(row.get("linkedin_url")),
        source_profile_url=safe_profile_url(row.get("linkedin_url")),
        source_last_updated_at=employment_updated,
        provider_record_observed_at=observed_at,
        provider_employment_updated_at=employment_updated,
        employment_verified_at=None,
        employment_source="provider_current_listing",
        current_role_indicator=current_role_indicator,
        conflicting_employer_observed_at=(
            observed_at if conflicting_current_role else None
        ),
        education=[str(v.get("school", {}).get("name")) for v in row.get("education", []) if isinstance(v, dict) and isinstance(v.get("school"), dict) and v["school"].get("name")],
        previous_employers=list(dict.fromkeys(previous_employers)),
        job_title_role=str(row.get("job_title_role") or "") or None,
        job_title_sub_role=str(row.get("job_title_sub_role") or "") or None,
        job_title_levels=[
            str(level).strip().lower()
            for level in (row.get("job_title_levels") or [])
            if str(level).strip()
        ][:6],
        provider_company_id=str(row.get("job_company_id") or "") or None,
        evidence={
            "employment_source": "provider_current_employment",
            "current_company_name": company,
            "current_company_domain": company_domain,
            "current_title": title,
            "employment_verified_at": (
                None
            ),
            "provider_record_observed_at": observed_at.isoformat(),
            "provider_employment_updated_at": (
                employment_updated.isoformat() if employment_updated else None
            ),
            "current_role_indicator": current_role_indicator,
            "employment_start_date": employment_start,
            "employment_end_date": employment_end,
            "conflicting_current_employment": conflicting_current_role,
        },
        field_provenance={"name": "pdl", "title": "pdl", "company": "pdl"},
    )


def _split_location(value: str | None) -> tuple[str | None, str | None]:
    """Split a job location into a coarse region and country.

    Only used when location filtering is explicitly enabled. City-level
    filtering is deliberately not offered: a PDL person record carries the
    person's own location, which for a distributed employer routinely differs
    from the job's city, and filtering on it removes the very people the search
    exists to find.
    """

    parts = [part.strip() for part in str(value or "").split(",") if part.strip()]
    if not parts:
        return None, None
    if len(parts) == 1:
        return None, parts[0]
    return parts[-2], parts[-1]


def _pdl_sql_value(value: object) -> str:
    cleaned = re.sub(
        r"[^A-Za-z0-9 /,&+().:_-]",
        "",
        str(value or ""),
    ).strip()[:120]
    return cleaned.replace("'", "''")


def _provider_company_domain(value: object) -> str | None:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    parsed = urlsplit(
        raw if "://" in raw else f"https://{raw}"
    )
    host = (parsed.hostname or "").strip(".")
    return host.removeprefix("www.") or None


def normalize_provider_text(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _provider_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
