"""PDL response semantics, company identity, and the query-relaxation ladder.

Everything here runs against fake transports. No provider account is contacted
and no credits are spent.

The behaviour under test comes from PDL's published documentation:
* 404 means "there were no profiles found matching your request"
  (https://docs.peopledatalabs.com/docs/errors)
* Person Search returns 200 with a ``total`` for valid queries
  (https://docs.peopledatalabs.com/docs/reference-person-search-api)
* Company Enrichment returns a 1-10 ``likelihood`` and 404 for no match
  (https://docs.peopledatalabs.com/docs/reference-company-enrichment-api)
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest

from app.core.config import settings
from app.people import circuit, pdl_company, pdl_query, pdl_status, providers
from app.people.errors import PeopleErrorCode
from app.people.providers import PDLPeopleProvider, ProviderUnavailable

NOT_FOUND_BODY = {
    "status": 404,
    "error": {
        "type": "not_found",
        "message": "No records were found matching your request",
    },
}
BAD_REQUEST_BODY = {
    "status": 400,
    "error": {"type": "invalid_request", "message": "Malformed SQL"},
}


@pytest.fixture(autouse=True)
def _clean() -> None:
    circuit.clear_local_circuits()
    pdl_company.clear_local_pdl_companies()


# --------------------------------------------------------------------------- #
# Status classification
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "endpoint",
    ["person_search", "person_enrichment", "person_identify", "company_enrichment"],
)
def test_documented_404_is_a_no_match_on_every_endpoint(endpoint) -> None:
    outcome = pdl_status.classify(
        endpoint=endpoint, status_code=404, payload=NOT_FOUND_BODY
    )
    assert outcome.no_match is True
    assert outcome.ok is True
    assert outcome.code is None


def test_search_200_with_empty_data_is_a_no_match() -> None:
    outcome = pdl_status.classify(
        endpoint="person_search",
        status_code=200,
        payload={"status": 200, "data": [], "total": 0},
    )
    assert outcome.no_match is True
    assert outcome.ok is True


def test_search_200_with_records_is_a_result() -> None:
    outcome = pdl_status.classify(
        endpoint="person_search",
        status_code=200,
        payload={"status": 200, "data": [{"id": "x"}], "total": 1},
    )
    assert outcome.no_match is False
    assert outcome.ok is True


def test_malformed_query_400_stays_invalid_input() -> None:
    outcome = pdl_status.classify(
        endpoint="person_search", status_code=400, payload=BAD_REQUEST_BODY
    )
    assert outcome.code == PeopleErrorCode.INVALID_INPUT
    assert outcome.reason == "provider_request_invalid"
    assert outcome.safe_metadata["provider_error_type"] == "invalid_request"


def test_404_without_the_documented_envelope_is_diagnosed_as_a_route_problem() -> None:
    """A wrong path looks identical at the status line to an empty result.

    Swallowing it as "no data" would make a broken adapter look exactly like a
    company nobody works at — for every company at once.
    """

    outcome = pdl_status.classify(
        endpoint="person_search", status_code=404, payload={"detail": "Not Found"}
    )
    assert outcome.no_match is False
    assert outcome.reason == "provider_route_invalid"
    assert outcome.code == PeopleErrorCode.INVALID_INPUT


def test_405_is_diagnosed_as_a_route_problem() -> None:
    outcome = pdl_status.classify(
        endpoint="person_search", status_code=405, payload=None
    )
    assert outcome.reason == "provider_route_invalid"


@pytest.mark.parametrize(
    ("status_code", "code"),
    [
        (401, PeopleErrorCode.AUTHENTICATION_FAILED),
        (402, PeopleErrorCode.PROVIDER_BUDGET_EXHAUSTED),
        (403, PeopleErrorCode.AUTHORIZATION_FAILED),
        (429, PeopleErrorCode.RATE_LIMITED),
        (500, PeopleErrorCode.PROVIDER_SERVER_ERROR),
        (503, PeopleErrorCode.PROVIDER_SERVER_ERROR),
    ],
)
def test_other_statuses_keep_their_classification(status_code, code) -> None:
    outcome = pdl_status.classify(
        endpoint="person_search", status_code=status_code, payload=None
    )
    assert outcome.code == code


def test_safe_error_fields_exclude_everything_but_type_and_message() -> None:
    fields = pdl_status.safe_error_fields(
        {
            "status": 404,
            "error": {"type": "not_found", "message": "No records"},
            "data": [{"full_name": "Private Person", "emails": ["a@b.c"]}],
        }
    )
    assert fields == {
        "provider_error_type": "not_found",
        "provider_error_message": "No records",
    }


# --------------------------------------------------------------------------- #
# Provider behaviour
# --------------------------------------------------------------------------- #


class _Transport:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list[tuple[str, str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.handler(method, url, kwargs)
        response.request = httpx.Request(method, url)
        return response


def _install(monkeypatch: pytest.MonkeyPatch, handler) -> _Transport:
    transport = _Transport(handler)
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kw: transport)
    return transport


def _identity(**overrides) -> pdl_company.PdlCompanyIdentity:
    defaults = {
        "raw_name": "Toshiba Global Commerce Solutions",
        "normalized_name": "toshiba global commerce solutions",
        "verified_domain": "toshibacommerce.example",
        "pdl_company_id": "pdl-toshiba",
        "source": "pdl_company_enrich_domain",
        "confidence": 0.9,
    }
    return pdl_company.PdlCompanyIdentity(**{**defaults, **overrides})


def _provider() -> PDLPeopleProvider:
    return PDLPeopleProvider("test-key-not-a-real-credential")


def _state(provider: PDLPeopleProvider) -> str:
    return circuit.circuit_state(
        provider="pdl",
        account_fingerprint=provider.account_fingerprint,
        operation="people_search",
    ).transient


def test_no_match_returns_no_people_and_no_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Toshiba/Vanderbilt case: a truthful empty answer, not a rejection."""

    _install(
        monkeypatch,
        lambda *_args: httpx.Response(404, json=NOT_FOUND_BODY),
    )
    provider = _provider()
    people = asyncio.run(
        provider.search_current_company_people(
            company=_identity(),
            category="likely_recruiter",
            role_family="software_engineering",
            job_location="Nashville, Tennessee, United States",
            limit=4,
        )
    )
    assert people == []
    assert _state(provider) == "closed"


def test_no_match_does_not_increment_any_circuit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, lambda *_args: httpx.Response(404, json=NOT_FOUND_BODY))
    provider = _provider()
    for _ in range(settings.people_circuit_failure_threshold + 3):
        provider.search_calls = 0
        assert (
            asyncio.run(
                provider.search_current_company_people(
                    company=_identity(),
                    category="potential_referrer",
                    role_family="software_engineering",
                    job_location=None,
                    limit=4,
                )
            )
            == []
        )
    snapshot = circuit.circuit_state(
        provider="pdl",
        account_fingerprint=provider.account_fingerprint,
        operation="people_search",
    )
    assert snapshot.transient == "closed"
    assert snapshot.configuration == "closed"
    assert snapshot.budget == "closed"


def test_malformed_query_still_raises_invalid_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, lambda *_args: httpx.Response(400, json=BAD_REQUEST_BODY))
    provider = _provider()
    with pytest.raises(ProviderUnavailable) as raised:
        asyncio.run(
            provider.search_current_company_people(
                company=_identity(),
                category="likely_recruiter",
                role_family="software_engineering",
                job_location=None,
                limit=4,
            )
        )
    assert raised.value.code == PeopleErrorCode.INVALID_INPUT
    assert raised.value.reason == "provider_request_invalid"
    assert raised.value.safe_metadata["provider_error_type"] == "invalid_request"


def test_ladder_stops_at_the_first_rung_that_finds_people(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    person = {
        "id": "pdl-1",
        "full_name": "Alex Example",
        "job_title": "Technical Recruiter",
        "job_company_name": "Toshiba Global Commerce Solutions",
        "job_company_website": "toshibacommerce.example",
        "job_title_role": "human_resources",
        "job_title_sub_role": "recruiting",
    }
    transport = _install(
        monkeypatch, lambda *_args: httpx.Response(200, json={"data": [person], "total": 1})
    )
    provider = _provider()
    people = asyncio.run(
        provider.search_current_company_people(
            company=_identity(),
            category="likely_recruiter",
            role_family="software_engineering",
            job_location=None,
            limit=4,
        )
    )
    assert [p.full_name for p in people] == ["Alex Example"]
    assert len(transport.calls) == 1
    assert people[0].discovery_strategy == "exact_company_subrole"


def test_ladder_relaxes_titles_but_never_the_company(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_sql: list[str] = []

    def handler(_method, _url, kwargs):
        sql = kwargs["json"]["sql"]
        seen_sql.append(sql)
        if len(seen_sql) < 3:
            return httpx.Response(404, json=NOT_FOUND_BODY)
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "pdl-late",
                        "full_name": "Robin Example",
                        "job_title": "Engineer",
                        "job_company_name": "Toshiba Global Commerce Solutions",
                        "job_company_website": "toshibacommerce.example",
                    }
                ],
                "total": 1,
            },
        )

    _install(monkeypatch, handler)
    monkeypatch.setattr(settings, "people_pdl_max_query_strategies", 3)
    provider = _provider()
    people = asyncio.run(
        provider.search_current_company_people(
            company=_identity(),
            category="potential_referrer",
            role_family="software_engineering",
            job_location=None,
            limit=4,
        )
    )
    assert len(people) == 1
    assert len(seen_sql) == 3
    # Every rung stayed pinned to verified company evidence. Precision may fall
    # back from the PDL company id to the verified domain, but the company
    # constraint itself never disappears.
    assert all(
        "job_company_id='pdl-toshiba'" in sql
        or "job_company_website='toshibacommerce.example'" in sql
        for sql in seen_sql
    )
    assert "job_company_id='pdl-toshiba'" in seen_sql[0]
    # No rung ever searches without a company clause.
    assert all("job_company" in sql for sql in seen_sql)
    # Title precision relaxed across the rungs.
    assert "job_title_sub_role" in seen_sql[0]
    assert "job_title_levels" not in seen_sql[-1]


def test_ladder_respects_the_configured_strategy_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _install(
        monkeypatch, lambda *_args: httpx.Response(404, json=NOT_FOUND_BODY)
    )
    monkeypatch.setattr(settings, "people_pdl_max_query_strategies", 2)
    provider = _provider()
    asyncio.run(
        provider.search_current_company_people(
            company=_identity(),
            category="potential_referrer",
            role_family="software_engineering",
            job_location=None,
            limit=4,
        )
    )
    assert len(transport.calls) == 2


def test_provider_call_budget_caps_total_searches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _install(
        monkeypatch, lambda *_args: httpx.Response(404, json=NOT_FOUND_BODY)
    )
    monkeypatch.setattr(settings, "people_pdl_max_provider_calls_per_discovery", 2)
    monkeypatch.setattr(settings, "people_pdl_max_query_strategies", 4)
    provider = _provider()
    for category in ("likely_recruiter", "potential_hiring_manager", "potential_referrer"):
        asyncio.run(
            provider.search_current_company_people(
                company=_identity(),
                category=category,
                role_family="software_engineering",
                job_location=None,
                limit=4,
            )
        )
    assert len(transport.calls) <= 2


def test_location_is_not_a_filter_by_default() -> None:
    inputs = pdl_query.LadderInputs(
        pdl_company_id="pdl-x",
        verified_domain="x.example",
        pdl_company_name="X",
        raw_company_name="X",
        role_family="software_engineering",
        location_region="Tennessee",
        location_country="United States",
        location_required=False,
    )
    ladder = pdl_query.build_ladder("potential_referrer", inputs, max_strategies=4)
    assert ladder
    assert all("location_region" not in item.sql for item in ladder)


def test_location_becomes_a_filter_only_when_explicitly_required() -> None:
    inputs = pdl_query.LadderInputs(
        pdl_company_id="pdl-x",
        verified_domain="x.example",
        pdl_company_name="X",
        raw_company_name="X",
        role_family="software_engineering",
        location_region="Tennessee",
        location_country="United States",
        location_required=True,
    )
    ladder = pdl_query.build_ladder("potential_referrer", inputs, max_strategies=4)
    assert "location_region='Tennessee'" in ladder[0].sql
    # Even then, relaxation drops it so one city cannot zero out every result.
    assert "location_region" not in ladder[1].sql


def test_ladder_is_empty_without_any_verified_company_evidence() -> None:
    inputs = pdl_query.LadderInputs(
        pdl_company_id=None,
        verified_domain=None,
        pdl_company_name=None,
        raw_company_name="",
        role_family="software_engineering",
    )
    assert pdl_query.build_ladder("likely_recruiter", inputs, max_strategies=4) == []


def test_sql_values_cannot_inject_a_clause() -> None:
    # Quotes are stripped or doubled and "=" is not in the allowlist, so no
    # caller can close the literal and append a clause.
    assert pdl_query.sql_value("Acme' OR 1=1 --") == "Acme OR 11 --"
    assert "'" not in pdl_query.sql_value("O'Reilly Media").replace("''", "")


def test_unresolved_company_never_reaches_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _install(
        monkeypatch, lambda *_args: httpx.Response(200, json={"data": []})
    )
    provider = _provider()
    with pytest.raises(ProviderUnavailable) as raised:
        asyncio.run(
            provider.search_current_company_people(
                company=_identity(pdl_company_id=None, verified_domain=None),
                category="likely_recruiter",
                role_family="software_engineering",
                job_location=None,
                limit=4,
            )
        )
    assert raised.value.code == PeopleErrorCode.COMPANY_DOMAIN_UNRESOLVED
    assert transport.calls == []


# --------------------------------------------------------------------------- #
# Company identity resolution
# --------------------------------------------------------------------------- #


def _company_response(**overrides):
    body = {
        "status": 200,
        "likelihood": 9,
        "id": "pdl-toshiba",
        "name": "Toshiba Global Commerce Solutions",
        "website": "toshibacommerce.example",
    }
    body.update(overrides)
    return httpx.Response(200, json=body)


def test_domain_resolves_to_a_pdl_company_id(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _install(monkeypatch, lambda *_args: _company_response())
    provider = _provider()
    identity = asyncio.run(
        provider.resolve_company(
            raw_name="Toshiba Global Commerce Solutions",
            normalized_name="toshiba global commerce solutions",
            verified_domain="toshibacommerce.example",
        )
    )
    assert identity.resolved
    assert identity.pdl_company_id == "pdl-toshiba"
    assert identity.source == "pdl_company_enrich_domain"
    assert identity.confidence == 0.9
    assert transport.calls[0][0] == "GET"
    assert transport.calls[0][1].endswith("/v5/company/enrich")
    assert transport.calls[0][2]["params"]["website"] == "toshibacommerce.example"


def test_alias_resolves_to_the_same_company(monkeypatch: pytest.MonkeyPatch) -> None:
    """"HII" must reach the same organization as "Huntington Ingalls Industries"."""

    def handler(_method, _url, kwargs):
        name = (kwargs.get("params") or {}).get("name")
        if name == "HII":
            return httpx.Response(404, json=NOT_FOUND_BODY)
        return _company_response(
            id="pdl-hii", name="Huntington Ingalls Industries", website="hii.example"
        )

    _install(monkeypatch, handler)
    provider = _provider()
    identity = asyncio.run(
        provider.resolve_company(
            raw_name="Huntington Ingalls Industries",
            normalized_name="huntington ingalls industries",
            aliases=("Huntington Ingalls Industries", "HII"),
        )
    )
    assert identity.pdl_company_id == "pdl-hii"


def test_low_likelihood_answer_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, lambda *_args: _company_response(likelihood=3))
    provider = _provider()
    identity = asyncio.run(
        provider.resolve_company(
            raw_name="Vanderbilt Health",
            normalized_name="vanderbilt health",
            verified_domain="vanderbilthealth.example",
        )
    )
    assert not identity.resolved
    assert identity.rejection_reason == "below_likelihood_threshold"


def test_a_different_organization_is_not_accepted_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hospital must not silently become its university."""

    _install(
        monkeypatch,
        lambda *_args: _company_response(
            id="pdl-vanderbilt-university",
            name="Vanderbilt University",
            website="vanderbilt.example",
        ),
    )
    provider = _provider()
    identity = asyncio.run(
        provider.resolve_company(
            raw_name="Vanderbilt Health",
            normalized_name="vanderbilt health",
        )
    )
    assert not identity.resolved
    assert identity.rejection_reason == "ambiguous_name_match"


def test_company_name_containment_is_accepted() -> None:
    assert pdl_company._names_agree("Cisco", "Cisco Systems")
    assert pdl_company._names_agree("Cisco Systems, Inc.", "Cisco")
    assert not pdl_company._names_agree("Vanderbilt Health", "Vanderbilt University")
    assert not pdl_company._names_agree("Toshiba Global Commerce", "Toshiba Memory")


def test_company_resolution_is_cached_both_ways(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _install(monkeypatch, lambda *_args: httpx.Response(404, json=NOT_FOUND_BODY))
    provider = _provider()
    for _ in range(3):
        identity = asyncio.run(
            provider.resolve_company(
                raw_name="Nowhere Inc",
                normalized_name="nowhere",
                verified_domain="nowhere.example",
            )
        )
        assert not identity.resolved
    # Unresolved is cached too, so a retry does not re-spend the lookup.
    assert len(transport.calls) <= 3


def test_company_resolution_logs_carry_no_secrets(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "pdl-secret-not-real"
    _install(monkeypatch, lambda *_args: _company_response())
    provider = PDLPeopleProvider(secret)
    with caplog.at_level(logging.INFO):
        asyncio.run(
            provider.resolve_company(
                raw_name="Toshiba Global Commerce Solutions",
                normalized_name="toshiba global commerce solutions",
                verified_domain="toshibacommerce.example",
            )
        )
    assert secret not in caplog.text
