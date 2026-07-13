"""Verify the source catalog against live ATS endpoints.

    python -m app.jobs.verify_sources [--source greenhouse] [--limit 50]
                                      [--write] [--report-json out/report.json]

For each configured source it calls the real ATS endpoint and checks that it
returns real jobs (or a valid empty result) with official, non-placeholder
application URLs. With ``--write`` it stamps ``verified_at`` on working sources
and marks broken ones ``enabled: false`` in the catalog file. Prints a summary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import settings
from app.jobs.job_normalization_service import validate_job
from app.jobs.source_registry import DEFAULT_CONFIG_PATH, SourceCompany, load_registry
from app.jobs.sources import ADAPTERS, SLUG_KEYS


@dataclass
class SourceCheck:
    company: SourceCompany
    ok: bool
    job_count: int
    reason: str = ""


async def _check(company: SourceCompany) -> SourceCheck:
    adapter_cls = ADAPTERS.get(company.provider)
    if adapter_cls is None:
        return SourceCheck(company, False, 0, "no adapter")
    adapter = adapter_cls(company.slug, company.name, company.config_dict)
    try:
        jobs = await asyncio.wait_for(
            adapter.fetch_recent_jobs(days=3650), timeout=settings.job_discovery_timeout_seconds
        )
    except Exception as exc:  # noqa: BLE001
        return SourceCheck(company, False, 0, f"{type(exc).__name__}")
    # A source is valid if it returns no jobs (open but empty) or jobs that pass
    # validation (real title/company + official, non-placeholder apply URL).
    invalid = [j for j in jobs if not validate_job(j)]
    if invalid:
        return SourceCheck(company, False, len(jobs), "placeholder/invalid job URLs")
    return SourceCheck(company, True, len(jobs))


async def verify(source: str | None, limit: int | None) -> list[SourceCheck]:
    registry = load_registry()
    if source:
        registry = [c for c in registry if c.provider == source]
    if limit:
        registry = registry[:limit]
    semaphore = asyncio.Semaphore(max(1, settings.job_discovery_concurrency))

    async def guarded(company: SourceCompany) -> SourceCheck:
        async with semaphore:
            return await _check(company)

    return await asyncio.gather(*[guarded(c) for c in registry])


def _write_back(checks: list[SourceCheck]) -> None:
    path = Path(settings.job_sources_file) if settings.job_sources_file else DEFAULT_CONFIG_PATH
    data = json.loads(path.read_text(encoding="utf-8"))
    status = {(c.company.provider, c.company.slug.lower()): c for c in checks}
    today = datetime.now(UTC).date().isoformat()
    for provider, key in SLUG_KEYS.items():
        for entry in data.get(provider, []) or []:
            slug = str(entry.get(key) or entry.get("slug") or "").lower()
            check = status.get((provider, slug))
            if check is None:
                continue
            if check.ok:
                entry["verified_at"] = today
                entry["enabled"] = True
            else:
                entry["enabled"] = False
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _summary(checks: list[SourceCheck]) -> dict:
    by_type: dict[str, int] = {}
    for c in checks:
        by_type[c.company.provider] = by_type.get(c.company.provider, 0) + 1
    verified = [c for c in checks if c.ok]
    broken = [c for c in checks if not c.ok]
    return {
        "total_sources": len(checks),
        "verified_sources": len(verified),
        "broken_sources": len(broken),
        "jobs_discovered": sum(c.job_count for c in checks),
        "sources_by_ats": by_type,
        "broken": [f"{c.company.provider}:{c.company.slug} ({c.reason})" for c in broken],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify job source catalog against live ATS endpoints.")
    parser.add_argument("--source", help="Only verify this provider (e.g. greenhouse)")
    parser.add_argument("--limit", type=int, help="Verify at most N sources")
    parser.add_argument("--write", action="store_true", help="Stamp verified_at / disable broken sources")
    parser.add_argument("--report-json", help="Write the JSON report to this path")
    args = parser.parse_args()

    checks = asyncio.run(verify(args.source, args.limit))
    summary = _summary(checks)
    if args.write:
        _write_back(checks)
        summary["written"] = True
    if args.report_json:
        out = Path(args.report_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps({k: v for k, v in summary.items() if k != "broken"}, indent=2))
    if summary["broken"]:
        print("\nBroken sources:")
        for line in summary["broken"]:
            print(f"  - {line}")


if __name__ == "__main__":
    main()
