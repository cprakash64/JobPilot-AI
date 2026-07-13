"""Role-specific source packs.

Packs are curated tag-filtered views of the verified catalog stored as JSON in
``app/jobs/source_packs/``. The discovery service selects packs based on the
user's profile (role family + seniority) so an AI/ML candidate searches AI/ML +
software + startup boards, a backend candidate searches software + devtools +
fintech, etc.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.jobs.job_eligibility_service import selected_families

PACKS_DIR = Path(__file__).parent / "source_packs"

# Tags that define each pack (kept in sync with the generated pack JSON files).
PACK_TAGS: dict[str, set[str]] = {
    "software_ai_us": {"software", "ai"},
    "new_grad_us": {"new_grad_possible"},
    "remote_us": {"remote"},
    "startups_us": {"startup"},
    "ai_ml_us": {"ai", "ml"},
    "devtools_us": {"devtools"},
    "fintech_us": {"fintech"},
    "healthcare_tech_us": {"healthtech"},
}

# Role family -> packs to search.
_FAMILY_PACKS: dict[str, list[str]] = {
    "ai_ml": ["ai_ml_us", "software_ai_us", "startups_us"],
    "software_engineering": ["software_ai_us", "new_grad_us", "remote_us", "devtools_us"],
    "data": ["ai_ml_us", "software_ai_us", "startups_us"],
    "devops": ["devtools_us", "software_ai_us"],
    "security": ["software_ai_us", "devtools_us"],
}


def list_packs() -> list[str]:
    return sorted(PACK_TAGS)


def load_pack_file(name: str) -> dict:
    path = PACKS_DIR / f"{name}.json"
    if not path.exists():
        return {"name": name, "tags": sorted(PACK_TAGS.get(name, set())), "sources": []}
    return json.loads(path.read_text(encoding="utf-8"))


def packs_for_profile(target_roles: list[str], target_levels: list[str] | None = None) -> list[str]:
    """Choose source packs from the user's target roles + level."""
    packs: list[str] = []
    families = selected_families(target_roles or [])
    for family in families:
        for pack in _FAMILY_PACKS.get(family, []):
            if pack not in packs:
                packs.append(pack)

    levels_text = " ".join(target_levels or []).lower()
    if any(word in levels_text for word in ["new grad", "entry", "junior", "graduate"]):
        for pack in ("new_grad_us", "software_ai_us"):
            if pack not in packs:
                packs.append(pack)

    if not packs:
        packs = ["software_ai_us", "new_grad_us", "remote_us"]
    return packs


def tags_for_packs(packs: list[str]) -> set[str]:
    tags: set[str] = set()
    for pack in packs:
        tags |= PACK_TAGS.get(pack, set())
    return tags
