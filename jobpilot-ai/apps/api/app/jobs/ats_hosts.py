"""Single registry of ATS, aggregator, and shared job-hosting domains.

A job sourced through SimplifyJobs, Greenhouse, or Workday still belongs to the
real hiring company. Any pipeline that infers an employer domain from a URL —
company branding, logos, people search — must reject these hosts, so the list
lives in one place instead of drifting between copies.

Expand this list rather than adding a parallel one.
"""

from __future__ import annotations

# Aggregators and job marketplaces.
_AGGREGATOR_HOSTS: frozenset[str] = frozenset(
    {
        "simplify.jobs",
        "indeed.com",
        "glassdoor.com",
        "linkedin.com",
        "ziprecruiter.com",
        "dice.com",
        "monster.com",
        "wellfound.com",
        "angel.co",
        "angellist.com",
        "builtin.com",
        "otta.com",
        "levels.fyi",
        "hiring.cafe",
        "jobright.ai",
        "google.com",
        "github.com",
    }
)

# Applicant tracking systems and hosted careers platforms.
_ATS_HOSTS: frozenset[str] = frozenset(
    {
        "greenhouse.io",
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
        "lever.co",
        "jobs.lever.co",
        "myworkdayjobs.com",
        "workdayjobs.com",
        "workday.com",
        "wd1.myworkdaysite.com",
        "myworkdaysite.com",
        "ashbyhq.com",
        "jobs.ashbyhq.com",
        "smartrecruiters.com",
        "jobvite.com",
        "icims.com",
        "taleo.net",
        "successfactors.com",
        "brassring.com",
        "workable.com",
        "breezy.hr",
        "recruitee.com",
        "teamtailor.com",
        "applytojob.com",
        "bamboohr.com",
        "paylocity.com",
        "paycom.com",
        "adp.com",
        "ultipro.com",
        "oraclecloud.com",
        "eightfold.ai",
        "phenompeople.com",
        "avature.net",
        "rippling.com",
        "gem.com",
        "polymer.co",
        "hire.withgoogle.com",
    }
)

# Documentation and test placeholders. A synthetic fixture must never look like
# successfully resolved production branding.
_PLACEHOLDER_HOSTS: frozenset[str] = frozenset(
    {"example.com", "example.org", "example.net", "localhost", "test.com"}
)

# Consumer mail and social hosts, which are never an employer's domain.
_PERSONAL_HOSTS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "yahoo.com",
        "outlook.com",
        "hotmail.com",
        "icloud.com",
        "proton.me",
        "protonmail.com",
        "facebook.com",
        "twitter.com",
        "x.com",
    }
)

ATS_AND_AGGREGATOR_HOSTS: frozenset[str] = (
    _AGGREGATOR_HOSTS | _ATS_HOSTS | _PLACEHOLDER_HOSTS | _PERSONAL_HOSTS
)


def is_ats_or_aggregator_host(host: str | None) -> bool:
    """True when ``host`` (or any parent of it) is a shared job-hosting domain.

    Subdomain matching matters: ``acme.myworkdayjobs.com`` and
    ``boards.greenhouse.io/acme`` are the ATS's domain, not Acme's.
    """

    value = (host or "").strip().lower().strip(".").removeprefix("www.")
    if not value:
        return False
    return any(
        value == excluded or value.endswith(f".{excluded}")
        for excluded in ATS_AND_AGGREGATOR_HOSTS
    )
