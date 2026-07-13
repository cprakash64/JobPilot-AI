"""Config-driven registry/catalog of real, public ATS job boards.

Sources are loaded from a JSON catalog (default: ``app/jobs/sources_config.json``,
override with the ``JOB_SOURCES_FILE`` env var). Each provider maps to a list of
company entries; entries may be a bare ``{"company", "<slug-key>"}`` or a rich
catalog entry with ``tags``, ``priority``, ``enabled``, ``config`` and
``verified_at``::

    {
      "greenhouse": [
        {"company": "Stripe", "board_token": "stripe",
         "tags": ["software", "ai", "us"], "priority": 10,
         "verified_at": "2026-07-09", "enabled": true}
      ]
    }

Additional sources can be supplied via ``JOB_SOURCE_COMPANIES`` as
"provider:slug:Display Name" entries. There are no invented in-code companies:
if nothing is configured the registry is empty and discovery shows setup guidance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings
from app.job_sources.base import JobSourceAdapter
from app.jobs.sources import ADAPTERS, SLUG_KEYS

DEFAULT_CONFIG_PATH = Path(__file__).parent / "sources_config.json"


@dataclass(frozen=True)
class SourceCompany:
    provider: str
    slug: str
    name: str
    tags: tuple[str, ...] = ()
    priority: int = 5
    enabled: bool = True
    config: tuple[tuple[str, str], ...] = ()  # frozen dict for hashability

    @property
    def config_dict(self) -> dict[str, str]:
        return dict(self.config)


def _config_path() -> Path:
    configured = getattr(settings, "job_sources_file", None)
    return Path(configured) if configured else DEFAULT_CONFIG_PATH


def _load_from_file() -> list[SourceCompany]:
    path = _config_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    companies: list[SourceCompany] = []
    for provider, key in SLUG_KEYS.items():
        for entry in data.get(provider, []) or []:
            if not isinstance(entry, dict):
                continue
            if entry.get("enabled") is False:
                continue
            slug = str(entry.get(key) or entry.get("slug") or "").strip()
            name = str(entry.get("company") or slug).strip()
            if not slug or provider not in ADAPTERS:
                continue
            companies.append(
                SourceCompany(
                    provider=provider,
                    slug=slug,
                    name=name,
                    tags=tuple(str(t).lower() for t in entry.get("tags", [])),
                    priority=int(entry.get("priority", 5)),
                    enabled=True,
                    config=tuple((k, str(v)) for k, v in (entry.get("config") or {}).items()),
                )
            )
    return companies


def _load_from_env() -> list[SourceCompany]:
    companies: list[SourceCompany] = []
    for entry in settings.job_source_companies or []:
        parts = [part.strip() for part in entry.split(":")]
        if len(parts) < 2 or parts[0] not in ADAPTERS:
            continue
        provider, slug = parts[0], parts[1]
        name = parts[2] if len(parts) > 2 and parts[2] else slug.title()
        companies.append(SourceCompany(provider=provider, slug=slug, name=name, priority=8))
    return companies


def load_registry(tags: set[str] | None = None) -> list[SourceCompany]:
    """Merge file + env sources, de-duplicated by (provider, slug), highest
    priority first. When ``tags`` is given, keep only entries matching a tag."""
    seen: set[tuple[str, str]] = set()
    registry: list[SourceCompany] = []
    for company in [*_load_from_file(), *_load_from_env()]:
        key = (company.provider, company.slug.lower())
        if key in seen:
            continue
        if tags and not (set(company.tags) & tags):
            continue
        seen.add(key)
        registry.append(company)
    registry.sort(key=lambda c: c.priority, reverse=True)
    return registry


def is_configured() -> bool:
    return bool(load_registry())


def build_adapters(limit: int | None = None, tags: set[str] | None = None) -> list[JobSourceAdapter]:
    registry = load_registry(tags=tags)
    if limit is not None:
        registry = registry[:limit]
    adapters: list[JobSourceAdapter] = []
    for company in registry:
        adapter_cls = ADAPTERS.get(company.provider)
        if adapter_cls is None:
            continue
        adapters.append(adapter_cls(company.slug, company.name, company.config_dict))
    return adapters
