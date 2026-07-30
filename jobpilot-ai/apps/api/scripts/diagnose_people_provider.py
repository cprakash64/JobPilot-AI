"""Explain — and optionally exercise — the people-provider path for one job.

Dry run is the default and spends nothing: it prints the company identity that
would be resolved, the query ladder that would be walked, and the maximum
provider calls and credits the run could cost.

    python -m scripts.diagnose_people_provider --job-id 7605 --provider pdl --dry-run

A live run requires BOTH an explicit ``--live`` and an explicit
``--max-calls``, so no invocation can spend provider credits by accident.

Nothing personal is ever printed: no API key, no authorization header, no full
person record, no email address, and no raw provider payload. Live output is
limited to endpoint, status, safe provider error type/message, counts, the
matching strategy, and latency.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.entities import JobPosting
from app.people import pdl_company, pdl_query
from app.people.intelligence import extract_job_people_profile
from app.people.providers import PDLPeopleProvider, ProviderUnavailable
from app.people.service import _PEOPLE_CATEGORIES

_CATEGORIES = tuple(_PEOPLE_CATEGORIES)


def _sanitized_ladder(
    identity: pdl_company.PdlCompanyIdentity, profile, *, limit: int
) -> dict[str, list[dict[str, object]]]:
    inputs = pdl_query.LadderInputs(
        pdl_company_id=identity.pdl_company_id,
        verified_domain=identity.verified_domain,
        pdl_company_name=identity.pdl_company_name,
        raw_company_name=identity.raw_name,
        aliases=identity.aliases,
        role_family=profile.role_family,
        size=limit,
        location_required=settings.people_pdl_location_required,
    )
    ladder: dict[str, list[dict[str, object]]] = {}
    for category in _CATEGORIES:
        ladder[category] = [
            strategy.safe_summary()
            for strategy in pdl_query.build_ladder(
                category,
                inputs,
                max_strategies=max(1, settings.people_pdl_max_query_strategies),
            )
        ]
    return ladder


def _plan(job: JobPosting, *, provider_name: str) -> dict[str, object]:
    profile = extract_job_people_profile(job)
    cached = pdl_company.identity_from_cache(
        raw_name=profile.company_raw_name or profile.company_name,
        normalized_name=profile.company_normalized_name,
        domain=profile.company_domain,
    )
    identity = cached or pdl_company.PdlCompanyIdentity(
        raw_name=profile.company_raw_name or profile.company_name,
        normalized_name=profile.company_normalized_name,
        aliases=tuple(profile.company_aliases),
        verified_domain=profile.company_domain,
    )
    limit = min(
        settings.people_pdl_search_result_limit,
        settings.people_pdl_max_results_per_discovery,
    )
    ladder = _sanitized_ladder(identity, profile, limit=limit)
    # The per-discovery ceiling covers company resolution *and* searches, so
    # the plan reports the same number the runtime will actually enforce.
    ceiling = settings.people_pdl_max_provider_calls_per_discovery
    company_calls = 0 if cached else min(3, ceiling)
    max_search_calls = max(
        0,
        min(
            ceiling - company_calls,
            sum(len(rungs) for rungs in ladder.values()),
        ),
    )
    return {
        "job_id": job.id,
        "provider": provider_name,
        "raw_company_name": profile.company_raw_name or profile.company_name,
        "normalized_company_name": profile.company_normalized_name,
        "verified_domain": profile.company_domain,
        "domain_source": profile.company_evidence_source,
        "domain_confidence": profile.domain_confidence,
        "role_family": profile.role_family,
        "job_location": profile.location,
        "company_identity": identity.safe_summary(),
        "company_identity_from_cache": cached is not None,
        "selected_endpoints": [
            "GET https://api.peopledatalabs.com/v5/company/enrich",
            "POST https://api.peopledatalabs.com/v5/person/search",
        ],
        "strategies": ladder,
        "searchable": identity.searchable,
        "expected_max_company_calls": company_calls,
        "expected_max_search_calls": max_search_calls,
        "expected_max_provider_calls": company_calls + max_search_calls,
        "expected_max_credits": company_calls + max_search_calls * limit,
        "config": {
            "progressive_search_enabled": (
                settings.people_pdl_progressive_search_enabled
            ),
            "max_query_strategies": settings.people_pdl_max_query_strategies,
            "location_required": settings.people_pdl_location_required,
            "search_result_limit": settings.people_pdl_search_result_limit,
            "company_min_likelihood": settings.people_pdl_company_min_likelihood,
            "max_provider_calls_per_discovery": (
                settings.people_pdl_max_provider_calls_per_discovery
            ),
        },
    }


async def _live(job: JobPosting, *, max_calls: int) -> dict[str, object]:
    """Exercise the real provider under a hard call ceiling."""

    profile = extract_job_people_profile(job)
    provider = PDLPeopleProvider()
    original_ceiling = settings.people_pdl_max_provider_calls_per_discovery
    settings.people_pdl_max_provider_calls_per_discovery = max_calls
    observations: list[dict[str, object]] = []
    try:
        try:
            identity = await provider.resolve_company(
                raw_name=profile.company_raw_name or profile.company_name,
                normalized_name=profile.company_normalized_name,
                aliases=tuple(profile.company_aliases),
                verified_domain=profile.company_domain,
            )
        except ProviderUnavailable as exc:
            return {
                "job_id": job.id,
                "stage": "company_resolution",
                "error_code": str(exc.code),
                "reason": exc.reason,
                "http_status": exc.http_status,
                "safe_provider_error": exc.safe_metadata,
                "provider_calls": provider.search_calls,
            }
        for category in _CATEGORIES:
            if provider.call_budget_remaining <= 0:
                break
            try:
                people = await provider.search_current_company_people(
                    company=identity,
                    category=category,
                    role_family=profile.role_family,
                    job_location=profile.location,
                    limit=min(
                        settings.people_pdl_search_result_limit,
                        settings.people_pdl_results_per_query,
                    ),
                )
                observations.append(
                    {
                        "category": category,
                        # Counts and the matching strategy only — never a record.
                        "result_count": len(people),
                        "matched_strategy": (
                            people[0].discovery_strategy if people else None
                        ),
                    }
                )
            except ProviderUnavailable as exc:
                observations.append(
                    {
                        "category": category,
                        "error_code": str(exc.code),
                        "reason": exc.reason,
                        "http_status": exc.http_status,
                        "safe_provider_error": exc.safe_metadata,
                    }
                )
        return {
            "job_id": job.id,
            "company_identity": identity.safe_summary(),
            "categories": observations,
            "strategy_calls": provider.strategy_calls,
            "provider_calls": provider.search_calls,
            "credits_observed": provider.credits,
        }
    finally:
        settings.people_pdl_max_provider_calls_per_discovery = original_ceiling


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument("--provider", default="pdl", choices=["pdl"])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explain the plan without contacting the provider (the default).",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Contact the real provider. Requires --max-calls.",
    )
    parser.add_argument(
        "--max-calls",
        type=int,
        default=0,
        help="Hard ceiling on provider calls for a live run.",
    )
    args = parser.parse_args()

    if args.live and args.dry_run:
        parser.error("--live and --dry-run are mutually exclusive")
    if args.live and args.max_calls <= 0:
        parser.error("--live requires an explicit positive --max-calls")

    db = SessionLocal()
    try:
        job = db.get(JobPosting, args.job_id)
        if job is None:
            print(json.dumps({"error": "job_not_found", "job_id": args.job_id}))
            sys.exit(1)
        if not args.live:
            print(json.dumps(_plan(job, provider_name=args.provider), indent=2))
            return
        print(
            json.dumps(
                asyncio.run(_live(job, max_calls=args.max_calls)),
                indent=2,
                default=str,
            )
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
