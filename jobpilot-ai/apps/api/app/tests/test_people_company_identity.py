"""Hiring-company identity resolution for People Who Can Help.

A job sourced through SimplifyJobs, Greenhouse, or Workday still belongs to the
real employer. These tests pin that down with fixtures only — no live DNS, no
provider account, no credits.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.jobs.ats_hosts import is_ats_or_aggregator_host
from app.models.entities import JobPosting
from app.people.intelligence import (
    company_aliases_for,
    normalize_company_name,
    resolve_company_identity,
)

# Fixtures for the three companies from the production incident.
CISCO = {
    "company": "Cisco Systems, Inc.",
    "domain": "cisco.com",
    "aliases": ("Cisco", "Cisco Systems", "CISCO SYSTEMS INC"),
}
HII = {
    "company": "Huntington Ingalls Industries",
    "domain": "hii.com",
    "aliases": ("HII", "Huntington Ingalls", "Huntington Ingalls Industries, Inc."),
}
L3HARRIS = {
    "company": "L3Harris Technologies",
    "domain": "l3harris.com",
    "aliases": ("L3Harris", "L3 Harris", "L3Harris Technologies, Inc."),
}


def _job(**overrides) -> JobPosting:
    defaults = {
        "id": 1,
        "company": "Cisco Systems, Inc.",
        "title": "Software Engineer",
        "company_domain": None,
        "application_url": "https://simplify.jobs/p/abc123",
        "description_clean": "Build backend services.",
        "raw_json": {},
        "required_skills": [],
        "preferred_skills": [],
        "seniority_level": None,
        "location": "Remote",
        "employment_type": "full_time",
    }
    return JobPosting(**{**defaults, **overrides})


@pytest.mark.parametrize("fixture", [CISCO, HII, L3HARRIS], ids=["cisco", "hii", "l3harris"])
def test_company_aliases_resolve_to_one_canonical_organization(fixture) -> None:
    canonical = normalize_company_name(fixture["company"])
    assert canonical
    for alias in fixture["aliases"]:
        assert normalize_company_name(alias) == canonical, alias


def test_legal_suffixes_do_not_change_the_canonical_key() -> None:
    base = normalize_company_name("Acme Robotics")
    for suffix in ("Inc.", "LLC", "Ltd.", "Corp.", "Corporation", "Co."):
        assert normalize_company_name(f"Acme Robotics {suffix}") == base


def test_known_abbreviations_expand_to_searchable_aliases() -> None:
    aliases = company_aliases_for("HII")
    assert "Huntington Ingalls Industries" in aliases
    # The raw form the job carried is always preserved, and comes first.
    assert aliases[0] == "HII"


@pytest.mark.parametrize(
    "host",
    [
        "simplify.jobs",
        "greenhouse.io",
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
        "lever.co",
        "jobs.lever.co",
        "myworkdayjobs.com",
        "cisco.wd1.myworkdayjobs.com",
        "workdayjobs.com",
        "ashbyhq.com",
        "jobs.ashbyhq.com",
        "smartrecruiters.com",
        "jobvite.com",
        "icims.com",
    ],
)
def test_ats_and_aggregator_hosts_are_rejected(host: str) -> None:
    assert is_ats_or_aggregator_host(host)


def test_employer_hosts_are_not_rejected() -> None:
    for host in ("cisco.com", "hii.com", "l3harris.com", "jobs.boeing.com"):
        assert not is_ats_or_aggregator_host(host)


def test_aggregator_apply_url_never_becomes_the_company_domain() -> None:
    identity = resolve_company_identity(
        None,
        _job(
            company=CISCO["company"],
            application_url="https://simplify.jobs/p/cisco-swe",
        ),
    )
    assert identity.canonical_domain is None
    assert identity.evidence_source == "unresolved"
    assert identity.rejected_domain == "simplify.jobs"
    assert identity.rejection_reason == "ats_or_aggregator_host"


def test_ats_apply_url_never_becomes_the_company_domain() -> None:
    identity = resolve_company_identity(
        None,
        _job(
            company=HII["company"],
            application_url="https://hii.wd1.myworkdayjobs.com/en-US/careers/job/123",
        ),
    )
    assert identity.canonical_domain is None
    assert identity.rejection_reason == "ats_or_aggregator_host"


@pytest.mark.parametrize("fixture", [CISCO, HII, L3HARRIS], ids=["cisco", "hii", "l3harris"])
def test_verified_job_company_domain_wins(fixture) -> None:
    identity = resolve_company_identity(
        None,
        _job(company=fixture["company"], company_domain=fixture["domain"]),
    )
    assert identity.canonical_domain == fixture["domain"]
    assert identity.evidence_source == "job_company_record"
    assert identity.domain_confidence >= settings.people_domain_min_confidence
    assert identity.raw_name == fixture["company"]
    assert identity.normalized_name == normalize_company_name(fixture["company"])


def test_employer_hosted_apply_url_resolves_when_no_record_exists() -> None:
    identity = resolve_company_identity(
        None,
        _job(
            company="Boeing",
            application_url="https://jobs.boeing.com/job/12345",
        ),
    )
    assert identity.canonical_domain == "jobs.boeing.com"
    assert identity.evidence_source == "official_application_hostname"


def test_domain_below_the_confidence_threshold_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The apply-URL path resolves at 0.8; raise the bar above it.
    monkeypatch.setattr(settings, "people_domain_min_confidence", 0.9)
    identity = resolve_company_identity(
        None,
        _job(company="Boeing", application_url="https://jobs.boeing.com/job/12345"),
    )
    assert identity.canonical_domain is None
    assert identity.rejection_reason == "below_confidence_threshold"
    assert identity.rejected_domain == "jobs.boeing.com"


def test_raw_company_name_is_always_preserved() -> None:
    identity = resolve_company_identity(None, _job(company="  Cisco Systems, Inc.  "))
    assert identity.raw_name == "Cisco Systems, Inc."
    # Normalization is stored separately and collapses onto the alias group's
    # canonical key, so "Cisco" and "Cisco Systems, Inc." agree.
    assert identity.normalized_name == normalize_company_name("Cisco")
