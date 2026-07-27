from __future__ import annotations

# ruff: noqa: E501
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx

from app.core.config import settings
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

APOLLO_ENRICHMENT_ADAPTER_VERSION = "apollo-enrichment-v2"
_APOLLO_PERSON_ID = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)


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

    def _failure(self) -> None:
        failures, _ = _CIRCUITS.get(self.provider_name, (0, None))
        failures += 1
        opened = (
            datetime.now(UTC) + timedelta(seconds=_CIRCUIT_RESET_SECONDS)
            if failures >= _CIRCUIT_FAILURE_THRESHOLD
            else None
        )
        _CIRCUITS[self.provider_name] = (failures, opened)

    async def _request(self, method: str, url: str, **kwargs) -> dict:
        _, opened = _CIRCUITS.get(self.provider_name, (0, None))
        if opened and opened > datetime.now(UTC):
            raise ProviderUnavailable(
                "provider_circuit_open", provider=self.provider_name, duration_ms=0
            )
        if opened:
            # Allow one clean half-open attempt after the reset interval.
            _CIRCUITS[self.provider_name] = (0, None)
        self.requests += 1
        timeout = httpx.Timeout(settings.people_provider_timeout_seconds)
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                response = await client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            self._failure()
            raise ProviderUnavailable(
                "provider_timeout",
                provider=self.provider_name,
                duration_ms=(time.monotonic() - started) * 1000,
            ) from exc
        except httpx.NetworkError as exc:
            self._failure()
            raise ProviderUnavailable(
                "provider_network_error",
                provider=self.provider_name,
                duration_ms=(time.monotonic() - started) * 1000,
            ) from exc
        duration_ms = (time.monotonic() - started) * 1000
        self.last_http_status = response.status_code
        self.last_duration_ms = duration_ms
        reason = {
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
        if response.status_code >= 400:
            self._failure()
            raise ProviderUnavailable(
                "provider_unavailable",
                provider=self.provider_name,
                http_status=response.status_code,
                duration_ms=duration_ms,
            )
        try:
            data = response.json()
        except ValueError as exc:
            self._failure()
            raise ProviderUnavailable(
                "provider_schema_error",
                provider=self.provider_name,
                http_status=response.status_code,
                duration_ms=duration_ms,
            ) from exc
        if not isinstance(data, dict):
            self._failure()
            raise ProviderUnavailable(
                "provider_schema_error",
                provider=self.provider_name,
                http_status=response.status_code,
                duration_ms=duration_ms,
            )
        _CIRCUITS[self.provider_name] = (0, None)
        return data


class ApolloPeopleProvider(_HttpProvider):
    def __init__(self, api_key: str | None = None) -> None:
        super().__init__("apollo")
        self.api_key = api_key or settings.apollo_api_key
        self.enrichment_rejection_reasons: dict[str, str] = {}
        self.enrichment_safe_metrics: dict[str, int] = {}

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ProviderUnavailable("provider_not_configured", provider=self.provider_name)
        return {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
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
                for identifier in batch:
                    person = await self._single_enrichment_after_bulk_422(identifier)
                    if person is not None:
                        enriched.append(person)
                continue
            enriched.extend(self._normalize_bulk_matches(data, batch))
        return enriched

    def enrichment_rejection_reason(self, provider_person_id: str) -> str | None:
        return self.enrichment_rejection_reasons.get(provider_person_id)

    async def _bulk_enrichment_request(self, identifiers: list[str]) -> dict:
        headers = self._headers()
        params = {
            "reveal_personal_emails": "false",
            "reveal_phone_number": "false",
        }
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
            sorted(params),
            APOLLO_ENRICHMENT_ADAPTER_VERSION,
        )
        return await self._request(
            "POST",
            "https://api.apollo.io/api/v1/people/bulk_match",
            headers=headers,
            params=params,
            json=payload,
        )

    async def _single_enrichment_after_bulk_422(
        self, identifier: str
    ) -> ProviderPerson | None:
        try:
            data = await self._request(
                "POST",
                "https://api.apollo.io/api/v1/people/match",
                headers=self._headers(),
                params={
                    "id": identifier,
                    "reveal_personal_emails": "false",
                    "reveal_phone_number": "false",
                },
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
                    endpoint="/api/v1/people/match",
                    metadata=exc.safe_metadata,
                )
                return None
            raise
        self._record_credits(data)
        row = _single_apollo_person(data)
        if row is None:
            self.enrichment_rejection_reasons[identifier] = (
                "enrichment_record_not_found"
            )
            self._increment_enrichment_metric("enrichment_record_not_found")
            return None
        person = _normalize_apollo(row, identifier_kind="enrichment")
        if person is None or person.provider_person_id != identifier:
            self.enrichment_rejection_reasons[identifier] = (
                "enrichment_correlation_failed"
            )
            self._increment_enrichment_metric("enrichment_correlation_failed")
            return None
        return person

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

    def _increment_enrichment_metric(self, reason: str) -> None:
        self.enrichment_safe_metrics[reason] = (
            self.enrichment_safe_metrics.get(reason, 0) + 1
        )

    async def get_usage(self) -> ProviderUsage:
        return ProviderUsage(provider="apollo", credits_used=self.credits, requests=self.requests)


class PDLPeopleProvider(_HttpProvider):
    def __init__(self, api_key: str | None = None) -> None:
        super().__init__("pdl")
        self.api_key = api_key or settings.pdl_api_key

    async def search_people(self, query: PeopleSearchQuery) -> list[ProviderPerson]:
        if not self.api_key:
            raise ProviderUnavailable("provider_not_configured", provider=self.provider_name)
        if not query.company_domain:
            return []
        safe_titles = [
            re.sub(r"[^A-Za-z0-9 /,&+().-]", "", title)[:120]
            for title in query.titles[:10]
        ]
        sql = (
            f"SELECT * FROM person WHERE job_company_website='{query.company_domain}' "
            f"AND job_title IN ({','.join(repr(v) for v in safe_titles)})"
        )
        data = await self._request(
            "GET", "https://api.peopledatalabs.com/v5/person/search",
            headers={"X-Api-Key": self.api_key}, params={"sql": sql, "size": query.limit},
        )
        self.credits += 1
        return [person for row in (data.get("data") or []) if (person := _normalize_pdl(row))]

    async def enrich_people(self, people: list[PersonEnrichmentRequest]) -> list[ProviderPerson]:
        return []

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
    if settings.people_primary_provider == "mock":
        if settings.app_env not in {"test", "development"}:
            raise ProviderUnavailable("mock_provider_forbidden")
        return MockPeopleProvider()
    if settings.people_primary_provider == "pdl":
        return PDLPeopleProvider()
    return ApolloPeopleProvider()


def get_email_provider() -> WorkEmailProvider:
    return HunterEmailProvider()


def _valid_apollo_person_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    identifier = value.strip()
    if not identifier or not _APOLLO_PERSON_ID.fullmatch(identifier):
        return None
    return identifier.lower()


def _invalid_apollo_person_id_shape(value: object) -> str:
    if value is None:
        return "null_provider_person_id"
    if not isinstance(value, str):
        return "non_string_provider_person_id"
    if not value.strip():
        return "blank_provider_person_id"
    if "*" in value:
        return "obfuscated_provider_person_id"
    return "malformed_provider_person_id"


def _apollo_person_id(row: dict, *, identifier_kind: str) -> str | None:
    if identifier_kind == "search":
        # Apollo's current People Enrichment contract explicitly directs callers
        # to use People Search's person_id. Search-result id/contact_id values
        # are different identities and must not be sent to enrichment.
        return _valid_apollo_person_id(row.get("person_id"))
    return _valid_apollo_person_id(row.get("id"))


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
    return next((value for value in candidates if isinstance(value, dict)), None)


def _safe_validation_token(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    token = re.sub(r"[^A-Za-z0-9_.-]", "", value)[:80]
    return token or None


def _safe_apollo_validation_metadata(
    response: httpx.Response,
) -> dict[str, object]:
    try:
        payload = response.json()
    except ValueError:
        return {"error_types": ["unparseable_validation_response"]}
    if not isinstance(payload, dict):
        return {"error_types": ["unsupported_validation_response"]}
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
    result: dict[str, object] = {
        "error_types": sorted(error_types) or ["validation_error"],
        "field_paths": sorted(field_paths),
        "expected_types": sorted(expected_types),
        "missing_required": missing_required,
    }
    return result


def _log_safe_apollo_validation(
    *, endpoint: str, metadata: dict[str, object]
) -> None:
    logger.warning(
        "apollo_enrichment_validation endpoint=%s http_status=422 error_types=%s "
        "field_paths=%s expected_types=%s missing_required=%s",
        endpoint,
        metadata.get("error_types", []),
        metadata.get("field_paths", []),
        metadata.get("expected_types", []),
        bool(metadata.get("missing_required")),
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
    employment_updated = _provider_datetime(
        row.get("employment_verified_at")
        or row.get("last_refreshed_at")
        or row.get("updated_at")
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
        provider_record_observed_at=observed_at,
        provider_employment_updated_at=employment_updated,
        employment_verified_at=None,
        employment_source="provider_current_listing",
        current_role_indicator=True,
        education=[str(v.get("school_name")) for v in row.get("education", []) if isinstance(v, dict) and v.get("school_name")],
        previous_employers=[str(v.get("organization_name")) for v in row.get("employment_history", [])[1:] if isinstance(v, dict) and v.get("organization_name")],
        evidence={
            "employment_source": "provider_current_employment",
            "current_company_name": company,
            "current_company_domain": (
                str(organization.get("primary_domain") or "").lower() or None
            ),
            "current_title": title,
            "employment_verified_at": (
                None
            ),
            "provider_record_observed_at": observed_at.isoformat(),
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
    employment_updated = _provider_datetime(
        row.get("job_last_changed")
        or row.get("last_updated")
        or row.get("updated_at")
    )
    company_domain = str(row.get("job_company_website") or "").lower() or None
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
        current_role_indicator=True,
        education=[str(v.get("school", {}).get("name")) for v in row.get("education", []) if isinstance(v, dict) and isinstance(v.get("school"), dict) and v["school"].get("name")],
        previous_employers=[str(v.get("company", {}).get("name")) for v in row.get("experience", [])[1:] if isinstance(v, dict) and isinstance(v.get("company"), dict) and v["company"].get("name")],
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
            "current_role_indicator": True,
        },
        field_provenance={"name": "pdl", "title": "pdl", "company": "pdl"},
    )


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
