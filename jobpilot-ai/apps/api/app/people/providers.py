from __future__ import annotations

# ruff: noqa: E501
import hashlib
import logging
import re
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import quote, urlsplit

import httpx

from app.core.config import settings
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
from app.people.provider_usage import (
    ProviderUsageContext,
    ProviderUsageRecorder,
    SessionFactory,
    operation_idempotency_key,
    reported_credits,
)
from app.people.schemas import (
    EmailVerificationResult,
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
PDL_DISCOVERY_STRATEGY_VERSION = "pdl-person-search-v1"
PDL_COMBINED_TITLES_PER_CATEGORY = 6
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
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.provider = provider
        self.http_status = http_status
        self.duration_ms = duration_ms
        self.safe_metadata = safe_metadata or {}


_CIRCUITS: dict[str, tuple[int, datetime | None]] = {}
_CIRCUIT_FAILURE_THRESHOLD = 3
_CIRCUIT_RESET_SECONDS = 60


class PeopleDiscoveryProvider(Protocol):
    async def search_people(self, query: PeopleSearchQuery) -> list[ProviderPerson]: ...
    async def enrich_people(self, people: list[PersonEnrichmentRequest]) -> list[ProviderPerson]: ...
    async def get_usage(self) -> ProviderUsage: ...


class WorkEmailProvider(Protocol):
    async def find_work_email(self, request: WorkEmailRequest) -> WorkEmailResult: ...
    async def verify_work_email(self, email: str) -> EmailVerificationResult: ...


class _HttpProvider:
    def __init__(self, provider_name: str) -> None:
        self.provider_name = provider_name
        self.requests = 0
        self.credits = 0
        self.last_http_status: int | None = None
        self.last_duration_ms: float | None = None
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

    def _failure(self) -> None:
        failures, _ = _CIRCUITS.get(self.provider_name, (0, None))
        failures += 1
        opened = (
            datetime.now(UTC) + timedelta(seconds=_CIRCUIT_RESET_SECONDS)
            if failures >= _CIRCUIT_FAILURE_THRESHOLD
            else None
        )
        _CIRCUITS[self.provider_name] = (failures, opened)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        operation_type: str,
        status_reason_overrides: dict[int, str] | None = None,
        malformed_response_reason: str = "provider_schema_error",
        **kwargs,
    ) -> dict:
        _, opened = _CIRCUITS.get(self.provider_name, (0, None))
        if opened and opened > datetime.now(UTC):
            raise ProviderUnavailable(
                "provider_circuit_open", provider=self.provider_name, duration_ms=0
            )
        if opened:
            # Allow one clean half-open attempt after the reset interval.
            _CIRCUITS[self.provider_name] = (0, None)
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
            self._failure()
            raise ProviderUnavailable(
                "provider_timeout",
                provider=self.provider_name,
                duration_ms=(time.monotonic() - started) * 1000,
            ) from exc
        except httpx.NetworkError as exc:
            self._finish_usage(
                usage_key,
                http_outcome="provider_network_error",
                operation_type=operation_type,
            )
            self._failure()
            raise ProviderUnavailable(
                "provider_network_error",
                provider=self.provider_name,
                duration_ms=(time.monotonic() - started) * 1000,
            ) from exc
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
        reason = (status_reason_overrides or {}).get(response.status_code) or {
            401: "provider_unauthorized",
            403: "provider_forbidden",
            429: "provider_rate_limited",
            422: "provider_schema_error",
        }.get(response.status_code)
        if reason:
            # Only transient failures contribute to the circuit. Permanent
            # credentials/access/schema failures must keep their precise reason
            # instead of being masked by provider_circuit_open on later calls.
            if response.status_code == 429:
                self._failure()
            raise ProviderUnavailable(
                reason,
                provider=self.provider_name,
                http_status=response.status_code,
                duration_ms=duration_ms,
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
            self._failure()
            raise ProviderUnavailable(
                "provider_schema_error",
                provider=self.provider_name,
                http_status=response.status_code,
                duration_ms=duration_ms,
            )
        if response.status_code >= 500:
            self._failure()
            raise ProviderUnavailable(
                "provider_unavailable",
                provider=self.provider_name,
                http_status=response.status_code,
                duration_ms=duration_ms,
            )
        if parsed_payload is None:
            self._failure()
            raise ProviderUnavailable(
                malformed_response_reason,
                provider=self.provider_name,
                http_status=response.status_code,
                duration_ms=duration_ms,
            )
        if response.status_code >= 400:
            self._failure()
            raise ProviderUnavailable(
                "provider_unavailable",
                provider=self.provider_name,
                http_status=response.status_code,
                duration_ms=duration_ms,
            )
        if not isinstance(parsed_payload, dict):
            self._failure()
            raise ProviderUnavailable(
                malformed_response_reason,
                provider=self.provider_name,
                http_status=response.status_code,
                duration_ms=duration_ms,
            )
        _CIRCUITS[self.provider_name] = (0, None)
        return parsed_payload


class ApolloPeopleProvider(_HttpProvider):
    def __init__(self, api_key: str | None = None) -> None:
        super().__init__("apollo")
        self.api_key = api_key or settings.apollo_api_key
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
            self._failure()
            raise ProviderUnavailable(
                "provider_schema_error",
                provider=self.provider_name,
                http_status=self.last_http_status,
                duration_ms=self.last_duration_ms,
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
            self._failure()
            raise ProviderUnavailable(
                "provider_schema_error",
                provider=self.provider_name,
                http_status=self.last_http_status,
                duration_ms=self.last_duration_ms,
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
        super().__init__("pdl")
        self.api_key = api_key or settings.pdl_api_key
        self._search_profiles: dict[str, ProviderPerson] = {}

    async def search_people(self, query: PeopleSearchQuery) -> list[ProviderPerson]:
        if not self.api_key:
            raise ProviderUnavailable("provider_not_configured", provider=self.provider_name)
        if not query.company_domain:
            return []
        safe_domain = _pdl_sql_value(
            _provider_company_domain(query.company_domain)
        )
        safe_company = _pdl_sql_value(query.company_name)
        safe_titles = [
            _pdl_sql_value(title)
            for title in query.titles[:20]
            if _pdl_sql_value(title)
        ]
        if not safe_domain or not safe_titles:
            return []
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
        data = await self._request(
            "POST", "https://api.peopledatalabs.com/v5/person/search",
            operation_type="people_search",
            headers={
                "X-Api-Key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "sql": sql,
                "size": min(
                    query.limit,
                    settings.people_pdl_results_per_query,
                    100,
                ),
            },
        )
        rows = data.get("data")
        if not isinstance(rows, list):
            self._failure()
            raise ProviderUnavailable(
                "provider_schema_error",
                provider=self.provider_name,
                http_status=self.last_http_status,
                duration_ms=self.last_duration_ms,
            )
        normalized = [
            person
            for row in rows
            if (person := _normalize_pdl(row))
        ]
        self.credits += len(rows)
        for person in normalized:
            self._search_profiles[person.provider_person_id] = person
        return normalized

    async def search_people_combined(
        self,
        queries: dict[str, list[PeopleSearchQuery]],
    ) -> list[ProviderPerson]:
        """Run one bounded exact-company search for all UI categories."""
        first = next(
            (
                query
                for category_queries in queries.values()
                for query in category_queries
            ),
            None,
        )
        if first is None:
            return []
        titles: list[str] = []
        for category_queries in queries.values():
            category_titles: list[str] = []
            for query in category_queries:
                for title in query.titles:
                    if (
                        title not in category_titles
                        and len(category_titles)
                        < PDL_COMBINED_TITLES_PER_CATEGORY
                    ):
                        category_titles.append(title)
            for title in category_titles:
                if title not in titles:
                    titles.append(title)
        return await self.search_people(
            PeopleSearchQuery(
                category=first.category,
                company_name=first.company_name,
                company_domain=first.company_domain,
                company_aliases=first.company_aliases,
                titles=titles,
                title_group="combined_exact_company",
                seniorities=[],
                location=first.location,
                location_filter_mode="soft",
                company_match_kind="canonical",
                role_family=first.role_family,
                department=first.department,
                limit=settings.people_pdl_results_per_query,
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
        super().__init__("hunter")
        self.api_key = api_key or settings.hunter_api_key

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
