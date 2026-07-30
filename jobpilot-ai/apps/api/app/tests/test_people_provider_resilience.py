"""Provider-failure isolation for People Who Can Help.

These tests encode the production incident where one company's request-scoped
failure paused people search for every other company. They use fake HTTP
transports only and never touch a real provider account or spend credits.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import settings
from app.people import circuit, providers
from app.people.coalescing import provider_search_coalescer, search_identity
from app.people.errors import PeopleErrorCode
from app.people.providers import PDLPeopleProvider, ProviderUnavailable
from app.people.schemas import PeopleSearchQuery

COMPANY_A = "company-a.example"  # request-scoped provider rejection
COMPANY_B = "company-b.example"  # successful empty response
COMPANY_C = "company-c.example"  # successful non-empty response

_PERSON_ROW = {
    "id": "pdl-person-c-1",
    "full_name": "Alex Example",
    "job_title": "Technical Recruiter",
    "job_company_name": "Company C",
    "job_company_website": COMPANY_C,
    "job_title_levels": ["manager"],
    "location_name": "Remote",
}


def _query(domain: str, company: str) -> PeopleSearchQuery:
    return PeopleSearchQuery(
        category="likely_recruiter",
        company_name=company,
        company_domain=domain,
        titles=["Technical Recruiter"],
        limit=2,
    )


class _RoutingTransport:
    """Routes by the hiring-company domain embedded in the PDL SQL filter."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _domain(self, kwargs: dict) -> str:
        sql = str((kwargs.get("json") or {}).get("sql", ""))
        for domain in (COMPANY_A, COMPANY_B, COMPANY_C):
            if domain in sql:
                return domain
        return "unknown"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def request(self, method: str, url: str, **kwargs):
        domain = self._domain(kwargs)
        self.calls.append(domain)
        request = httpx.Request(method, url)
        if domain == COMPANY_A:
            return httpx.Response(
                404, request=request, json={"error": "company not found"}
            )
        if domain == COMPANY_B:
            return httpx.Response(200, request=request, json={"data": []})
        return httpx.Response(200, request=request, json={"data": [_PERSON_ROW]})


@pytest.fixture(autouse=True)
def _clean_circuits() -> None:
    circuit.clear_local_circuits()


def _transport(monkeypatch: pytest.MonkeyPatch) -> _RoutingTransport:
    transport = _RoutingTransport()
    monkeypatch.setattr(
        providers.httpx, "AsyncClient", lambda **_kwargs: transport
    )
    return transport


def test_request_scoped_failures_do_not_pause_other_companies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repeatedly failing company must never block an unrelated company."""

    transport = _transport(monkeypatch)
    provider = PDLPeopleProvider("test-key-not-a-real-credential")

    for _ in range(3):
        with pytest.raises(ProviderUnavailable) as raised:
            asyncio.run(provider.search_people(_query(COMPANY_A, "Company A")))
        assert raised.value.code == PeopleErrorCode.INVALID_INPUT

    assert asyncio.run(provider.search_people(_query(COMPANY_B, "Company B"))) == []

    people = asyncio.run(provider.search_people(_query(COMPANY_C, "Company C")))
    assert [person.full_name for person in people] == ["Alex Example"]
    assert transport.calls.count(COMPANY_C) == 1


def test_empty_results_keep_the_provider_circuit_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _transport(monkeypatch)
    provider = PDLPeopleProvider("test-key-not-a-real-credential")

    for _ in range(5):
        assert asyncio.run(provider.search_people(_query(COMPANY_B, "Company B"))) == []

    state = circuit.circuit_state(
        provider="pdl",
        account_fingerprint=provider.account_fingerprint,
        operation="people_search",
    )
    assert state.transient == "closed"


def test_request_scoped_404_keeps_the_provider_circuit_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _transport(monkeypatch)
    provider = PDLPeopleProvider("test-key-not-a-real-credential")

    for _ in range(5):
        with pytest.raises(ProviderUnavailable):
            asyncio.run(provider.search_people(_query(COMPANY_A, "Company A")))

    state = circuit.circuit_state(
        provider="pdl",
        account_fingerprint=provider.account_fingerprint,
        operation="people_search",
    )
    assert state.transient == "closed"


class _StatusTransport:
    """Returns a scripted sequence of responses, recording every call."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = responses
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def request(self, method: str, url: str, **_kwargs):
        self.calls += 1
        response = self._responses[min(self.calls - 1, len(self._responses) - 1)]
        response.request = httpx.Request(method, url)
        return response


def _status_transport(
    monkeypatch: pytest.MonkeyPatch, responses: list[httpx.Response]
) -> _StatusTransport:
    transport = _StatusTransport(responses)
    monkeypatch.setattr(
        providers.httpx, "AsyncClient", lambda **_kwargs: transport
    )
    return transport


def _provider() -> PDLPeopleProvider:
    return PDLPeopleProvider("test-key-not-a-real-credential")


def _state(provider: PDLPeopleProvider) -> circuit.CircuitSnapshot:
    return circuit.circuit_state(
        provider="pdl",
        account_fingerprint=provider.account_fingerprint,
        operation="people_search",
    )


def test_unresolved_company_domain_never_reaches_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _status_transport(monkeypatch, [httpx.Response(200, json={"data": []})])
    provider = _provider()

    with pytest.raises(ProviderUnavailable) as raised:
        asyncio.run(
            provider.search_people(
                PeopleSearchQuery(
                    category="likely_recruiter",
                    company_name="Unknown Co",
                    company_domain=None,
                    titles=["Technical Recruiter"],
                    limit=2,
                )
            )
        )

    assert raised.value.code == PeopleErrorCode.COMPANY_DOMAIN_UNRESOLVED
    assert transport.calls == 0, "no credit may be spent on an unresolved domain"
    assert _state(provider).transient == "closed"


def test_authentication_failure_opens_only_the_configuration_circuit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _status_transport(monkeypatch, [httpx.Response(401, json={"error": "bad key"})])
    provider = _provider()

    for _ in range(settings.people_circuit_configuration_threshold):
        with pytest.raises(ProviderUnavailable) as raised:
            asyncio.run(provider.search_people(_query(COMPANY_C, "Company C")))
        assert raised.value.code == PeopleErrorCode.AUTHENTICATION_FAILED

    state = _state(provider)
    assert state.configuration == "open"
    assert state.transient == "closed"

    with pytest.raises(ProviderUnavailable) as blocked:
        asyncio.run(provider.search_people(_query(COMPANY_C, "Company C")))
    # The blocked response must name the configuration problem, not a generic
    # transient failure.
    assert blocked.value.reason == "provider_configuration_circuit_open"


def test_rate_limit_honours_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    _status_transport(
        monkeypatch,
        [httpx.Response(429, headers={"Retry-After": "45"}, json={"error": "slow down"})],
    )
    provider = _provider()

    with pytest.raises(ProviderUnavailable) as raised:
        asyncio.run(provider.search_people(_query(COMPANY_C, "Company C")))

    assert raised.value.code == PeopleErrorCode.RATE_LIMITED
    assert raised.value.retry_after_seconds == 45
    # A single 429 is traffic shaping, not an outage.
    assert _state(provider).transient == "closed"


def test_repeated_server_errors_open_then_half_open_closes_the_circuit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failing = [httpx.Response(503, json={"error": "unavailable"})]
    transport = _status_transport(monkeypatch, failing)
    provider = _provider()

    for _ in range(settings.people_circuit_failure_threshold):
        with pytest.raises(ProviderUnavailable) as raised:
            asyncio.run(provider.search_people(_query(COMPANY_C, "Company C")))
        assert raised.value.code == PeopleErrorCode.PROVIDER_SERVER_ERROR
    assert _state(provider).transient == "open"

    calls_while_open = transport.calls
    with pytest.raises(ProviderUnavailable) as blocked:
        asyncio.run(provider.search_people(_query(COMPANY_C, "Company C")))
    assert blocked.value.reason == "provider_circuit_open"
    assert transport.calls == calls_while_open, "an open circuit must not call out"

    # Once the cooldown elapses, exactly one probe is allowed and a healthy
    # response closes the circuit again.
    _expire_circuit(provider, "transient")
    _status_transport(monkeypatch, [httpx.Response(200, json={"data": [_PERSON_ROW]})])
    people = asyncio.run(provider.search_people(_query(COMPANY_C, "Company C")))
    assert people
    assert _state(provider).transient == "closed"


def _expire_circuit(provider: PDLPeopleProvider, kind: str) -> None:
    """Fast-forward a circuit past its cooldown without sleeping."""
    from datetime import UTC, datetime, timedelta

    key = circuit._key(
        provider="pdl",
        account_fingerprint=provider.account_fingerprint,
        operation="people_search",
        kind=kind,
    )
    value = circuit._read(key)
    value["opened_until"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    circuit._write(key, value, ttl_seconds=300)


def test_cancelled_request_increments_no_failure_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CancellingTransport:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, url: str, **_kwargs):
            raise asyncio.CancelledError

    monkeypatch.setattr(
        providers.httpx, "AsyncClient", lambda **_kwargs: CancellingTransport()
    )
    provider = _provider()

    for _ in range(settings.people_circuit_failure_threshold + 2):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(provider.search_people(_query(COMPANY_C, "Company C")))

    # Cancellations must leave every circuit untouched.
    state = _state(provider)
    assert state.transient == "closed"
    assert state.configuration == "closed"
    assert state.budget == "closed"


def test_circuit_state_is_scoped_per_provider_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _status_transport(monkeypatch, [httpx.Response(503, json={"error": "down"})])
    first = PDLPeopleProvider("account-one")
    second = PDLPeopleProvider("account-two")

    for _ in range(settings.people_circuit_failure_threshold):
        with pytest.raises(ProviderUnavailable):
            asyncio.run(first.search_people(_query(COMPANY_C, "Company C")))

    assert _state(first).transient == "open"
    assert _state(second).transient == "closed"
    assert first.account_fingerprint != second.account_fingerprint


def test_account_fingerprint_never_contains_the_raw_key() -> None:
    secret = "super-secret-key-value"
    fingerprint = providers.account_fingerprint(secret)
    assert secret not in fingerprint
    assert len(fingerprint) == 16
    key = circuit._key(
        provider="pdl",
        account_fingerprint=fingerprint,
        operation="people_search",
        kind="transient",
    )
    assert secret not in key
    assert key.startswith("people:circuit:")


def test_clear_people_circuits_only_touches_the_people_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _status_transport(monkeypatch, [httpx.Response(503, json={"error": "down"})])
    provider = _provider()
    for _ in range(settings.people_circuit_failure_threshold):
        with pytest.raises(ProviderUnavailable):
            asyncio.run(provider.search_people(_query(COMPANY_C, "Company C")))
    assert _state(provider).transient == "open"

    circuit._LOCAL["unrelated:key"] = {"state": "keep me"}
    removed = circuit.clear_people_circuits()

    assert removed >= 1
    assert _state(provider).transient == "closed"
    assert circuit._LOCAL.get("unrelated:key") == {"state": "keep me"}
    circuit._LOCAL.pop("unrelated:key", None)


def test_provider_failure_logs_carry_no_secrets_or_payloads(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "pdl-secret-key-not-real"
    _status_transport(
        monkeypatch,
        [
            httpx.Response(
                500,
                json={"data": [{"full_name": "Private Person", "emails": ["a@b.c"]}]},
            )
        ],
    )
    provider = PDLPeopleProvider(secret)

    with caplog.at_level(logging.INFO):
        with pytest.raises(ProviderUnavailable):
            asyncio.run(provider.search_people(_query(COMPANY_C, "Company C")))

    assert secret not in caplog.text
    assert "Private Person" not in caplog.text
    assert "a@b.c" not in caplog.text


def test_identical_concurrent_searches_produce_one_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def slow_search(_queries, *, limit):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return [f"person-{limit}"]

    provider = SimpleNamespace(provider_name="pdl", search_people_category=slow_search)

    async def scenario() -> list:
        provider_search_coalescer.clear()
        key = search_identity(
            provider="pdl",
            adapter_version="v1",
            company_domain=COMPANY_C,
            company_name="company c",
            role_family="software_engineering",
            category="likely_recruiter",
        )
        return await asyncio.gather(
            *(
                provider_search_coalescer.run(
                    key,
                    "pdl",
                    lambda: provider.search_people_category([], limit=3),
                )
                for _ in range(10)
            )
        )

    results = asyncio.run(scenario())
    assert calls == 1
    assert all(result == ["person-3"] for result in results)


def test_concurrent_different_companies_respect_the_concurrency_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    peak = 0

    async def tracked(_queries, *, limit):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return []

    provider = SimpleNamespace(provider_name="pdl", search_people_category=tracked)

    async def scenario() -> None:
        provider_search_coalescer.clear()
        await asyncio.gather(
            *(
                provider_search_coalescer.run(
                    f"distinct-company-{index}",
                    "pdl",
                    lambda: provider.search_people_category([], limit=2),
                )
                for index in range(8)
            )
        )

    asyncio.run(scenario())
    assert peak <= settings.people_provider_max_concurrent_calls
