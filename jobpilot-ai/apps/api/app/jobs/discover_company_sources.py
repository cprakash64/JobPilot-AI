"""Detect which public ATS a company uses (safe helper, not a scraper).

    python -m app.jobs.discover_company_sources [--seed FILE ...] [--write]

For each company slug in the seed files it probes the *known public ATS URL
patterns* (Greenhouse/Lever/Ashby/SmartRecruiters/Recruitee/Workable/Breezy) and
records which one returns real jobs. It never logs in, never scrapes restricted
boards, and never bypasses robots/captcha. Proposed sources are written to
``proposed_source_catalog.json``; nothing is added to the live catalog unless
``--write`` is passed (and even then only entries that pass verification).

Seed files live in ``seed_companies/*.txt`` (one company slug per line).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from app.core.config import settings

SEED_DIR = Path(__file__).parent / "seed_companies"

# provider -> (probe URL template, function to count jobs in the JSON payload)
PROBES = {
    "greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{s}/jobs", lambda d: len(d.get("jobs", []))),
    "lever": ("https://api.lever.co/v0/postings/{s}?mode=json", lambda d: len(d) if isinstance(d, list) else 0),
    "ashby": ("https://api.ashbyhq.com/posting-api/job-board/{s}", lambda d: len(d.get("jobs", []))),
    "smartrecruiters": ("https://api.smartrecruiters.com/v1/companies/{s}/postings?limit=1", lambda d: d.get("totalFound", 0)),
    "recruitee": ("https://{s}.recruitee.com/api/offers/", lambda d: len(d.get("offers", []))),
    "workable": ("https://apply.workable.com/api/v1/widget/accounts/{s}", lambda d: len(d.get("jobs", []))),
    "breezy": ("https://{s}.breezy.hr/json", lambda d: len(d) if isinstance(d, list) else 0),
}
_KEY = {"greenhouse": "board_token", "lever": "site", "ashby": "board", "smartrecruiters": "company_id",
        "recruitee": "company", "workable": "account", "breezy": "company"}


async def _detect(client: httpx.AsyncClient, slug: str) -> tuple[str, str, int] | None:
    for provider, (template, counter) in PROBES.items():
        try:
            r = await client.get(template.format(s=slug), timeout=8.0, follow_redirects=True)
            if r.status_code != 200:
                continue
            count = counter(r.json())
            if count and count > 0:
                return provider, slug, int(count)
        except Exception:  # noqa: BLE001
            continue
    return None


async def discover(slugs: list[str]) -> list[tuple[str, str, int]]:
    semaphore = asyncio.Semaphore(max(1, settings.job_discovery_concurrency))
    async with httpx.AsyncClient() as client:
        async def guarded(slug: str) -> tuple[str, str, int] | None:
            async with semaphore:
                return await _detect(client, slug)

        results = await asyncio.gather(*[guarded(s) for s in slugs])
    return [r for r in results if r]


def _load_seeds(paths: list[str]) -> list[str]:
    slugs: list[str] = []
    files = [Path(p) for p in paths] if paths else sorted(SEED_DIR.glob("*.txt"))
    for path in files:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            slug = line.strip().lower()
            if slug and not slug.startswith("#"):
                slugs.append(slug)
    return list(dict.fromkeys(slugs))


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect public ATS sources for seed companies.")
    parser.add_argument("--seed", action="append", help="Seed file(s); defaults to seed_companies/*.txt")
    parser.add_argument("--write", action="store_true", help="(reserved) append verified sources to the catalog")
    parser.add_argument("--out", default="proposed_source_catalog.json")
    args = parser.parse_args()

    slugs = _load_seeds(args.seed or [])
    found = asyncio.run(discover(slugs))

    proposed: dict[str, list[dict]] = {}
    for provider, slug, count in sorted(found):
        proposed.setdefault(provider, []).append({"company": slug.title(), _KEY[provider]: slug, "jobs": count})

    Path(args.out).write_text(json.dumps(proposed, indent=2), encoding="utf-8")
    print(f"Probed {len(slugs)} companies; detected {len(found)} ATS sources.")
    print({p: len(v) for p, v in proposed.items()})
    print(f"Wrote proposal to {args.out}. Run `python -m app.jobs.verify_sources` before enabling.")


if __name__ == "__main__":
    main()
