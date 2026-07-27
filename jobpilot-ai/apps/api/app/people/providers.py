from __future__ import annotations

# ruff: noqa: E501
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


class ProviderUnavailable(RuntimeError):
    def __init__(
        self,
        reason: str,
        *,
        provider: str = "unknown",
        http_status: int | None = None,
        duration_ms: float | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.provider = provider
        self.http_status = http_status
        self.duration_ms = duration_ms


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
        failures, opened = _CIRCUITS.get(self.provider_name, (0, None))
        if opened and opened > datetime.now(UTC):
            raise ProviderUnavailable(
                "provider_circuit_open", provider=self.provider_name, duration_ms=0
            )
        if opened:
            # Allow one clean half-open attempt after the reset interval.
            _CIRCUITS[self.provider_name] = (0, None)
            failures = 0
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
        if len(response.content) > settings.people_provider_response_max_bytes:
            self._failure()
            raise ProviderUnavailable(
                "provider_schema_error",
                provider=self.provider_name,
                http_status=response.status_code,
                duration_ms=duration_ms,
            )
        reason = {
            401: "provider_unauthorized",
            403: "provider_forbidden",
            429: "provider_rate_limited",
        }.get(response.status_code)
        if reason:
            self._failure()
            raise ProviderUnavailable(
                reason,
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

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ProviderUnavailable("provider_not_configured", provider=self.provider_name)
        return {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "accept": "application/json",
            "Cache-Control": "no-cache",
        }

    async def search_people(self, query: PeopleSearchQuery) -> list[ProviderPerson]:
        payload = {
            "person_titles": query.titles,
            "q_organization_domains_list": (
                [query.company_domain] if query.company_domain else []
            ),
            "person_locations": [query.location] if query.location else [],
            "per_page": query.limit,
            "page": 1,
        }
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
                    row, fallback_company_domain=query.company_domain
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
        data = await self._request(
            "POST",
            "https://api.apollo.io/api/v1/people/bulk_match",
            headers=self._headers(),
            params={"reveal_personal_emails": "false", "reveal_phone_number": "false"},
            json={
                "details": [
                    {"id": item.provider_person_id}
                    for item in people[:10]
                ]
            },
        )
        rows = data.get("matches")
        if not isinstance(rows, list):
            self._failure()
            raise ProviderUnavailable(
                "provider_schema_error",
                provider=self.provider_name,
                http_status=self.last_http_status,
                duration_ms=self.last_duration_ms,
            )
        credits = data.get("credits_consumed")
        if isinstance(credits, int) and credits >= 0:
            self.credits += credits
        return [
            person for row in rows if (person := _normalize_apollo(row))
        ]

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


def _normalize_apollo(
    row: object, *, fallback_company_domain: str | None = None
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
    identifier = str(row.get("id") or "").strip()
    if not all((name, title, company, identifier)):
        return None
    linkedin_url = row.get("linkedin_url")
    if isinstance(linkedin_url, str) and linkedin_url.startswith("http://"):
        linkedin_url = f"https://{linkedin_url.removeprefix('http://')}"
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
        employment_verified_at=datetime.now(UTC),
        education=[str(v.get("school_name")) for v in row.get("education", []) if isinstance(v, dict) and v.get("school_name")],
        previous_employers=[str(v.get("organization_name")) for v in row.get("employment_history", [])[1:] if isinstance(v, dict) and v.get("organization_name")],
        evidence={"employment_source": "provider_current_employment"},
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
    return ProviderPerson(
        provider="pdl", provider_person_id=identifier, full_name=name,
        current_company_name=company, current_company_domain=str(row.get("job_company_website") or "").lower() or None,
        current_title=title, department=str(row.get("job_title_role") or "") or None,
        seniority=str(row.get("job_title_levels", [""])[0] if row.get("job_title_levels") else "") or None,
        location=str(row.get("location_name") or "") or None,
        linkedin_url=safe_profile_url(row.get("linkedin_url")),
        source_profile_url=safe_profile_url(row.get("linkedin_url")),
        source_last_updated_at=datetime.now(UTC),
        education=[str(v.get("school", {}).get("name")) for v in row.get("education", []) if isinstance(v, dict) and isinstance(v.get("school"), dict) and v["school"].get("name")],
        previous_employers=[str(v.get("company", {}).get("name")) for v in row.get("experience", [])[1:] if isinstance(v, dict) and isinstance(v.get("company"), dict) and v["company"].get("name")],
        evidence={"employment_source": "provider_current_employment"},
        field_provenance={"name": "pdl", "title": "pdl", "company": "pdl"},
    )
