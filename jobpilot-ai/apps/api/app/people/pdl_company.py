"""Resolve a JobPilot company to a verified People Data Labs company identity.

Searching people by display name is the weakest possible company constraint: it
depends on the job feed and the provider spelling a company the same way. PDL
exposes a stable ``job_company_id`` on every person record, so resolving the
employer once and then searching by that id is both more precise and more
forgiving of naming differences.

Resolution order, strongest evidence first:

1. an already-cached verified PDL identity for this company,
2. Company Enrichment by verified website/domain,
3. Company Enrichment by canonical name plus domain agreement,
4. Company Enrichment by a known alias,
5. unresolved.

PDL's Company Enrichment API returns a ``likelihood`` between 1 and 10 and
answers 404 when nothing matches (https://docs.peopledatalabs.com/docs/reference-company-enrichment-api).
Anything below the configured likelihood, or any answer whose own name/website
disagrees with what we asked for, is rejected rather than guessed at: a wrong
company id returns confidently wrong people, which is worse than none.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock

from app.core.config import settings
from app.people.intelligence import normalize_company_name
from app.people.observability import metric

logger = logging.getLogger("jobpilot.people.pdl_company")

PDL_COMPANY_RESOLUTION_VERSION = "pdl-company-v1"
_CACHE_PREFIX = f"people:pdl-company:{PDL_COMPANY_RESOLUTION_VERSION}"

_LOCAL: dict[str, dict[str, object]] = {}
_LOCK = Lock()


@dataclass(frozen=True)
class PdlCompanyIdentity:
    """A verified — or explicitly unresolved — PDL company identity."""

    raw_name: str
    normalized_name: str
    aliases: tuple[str, ...] = ()
    verified_domain: str | None = None
    pdl_company_id: str | None = None
    pdl_company_name: str | None = None
    pdl_company_website: str | None = None
    source: str = "unresolved"
    confidence: float = 0.0
    resolved_at: str | None = None
    provider_version: str = PDL_COMPANY_RESOLUTION_VERSION
    rejection_reason: str | None = None

    @property
    def resolved(self) -> bool:
        return bool(self.pdl_company_id)

    @property
    def searchable(self) -> bool:
        """Enough verified evidence to constrain a people search at all."""

        return bool(self.pdl_company_id or self.verified_domain)

    def safe_summary(self) -> dict[str, object]:
        """Non-personal fields safe to log and persist."""

        return {
            "raw_company_name": self.raw_name,
            "normalized_company_name": self.normalized_name,
            "verified_domain": self.verified_domain,
            "pdl_company_id": self.pdl_company_id,
            "pdl_company_name": self.pdl_company_name,
            "pdl_company_website": self.pdl_company_website,
            "company_resolution_source": self.source,
            "company_resolution_confidence": self.confidence,
            "company_resolved_at": self.resolved_at,
            "company_resolution_version": self.provider_version,
            "company_rejection_reason": self.rejection_reason,
        }


def _cache_key(normalized_name: str, domain: str | None) -> str:
    return f"{_CACHE_PREFIX}:{normalized_name}:{domain or 'no-domain'}"


def _redis_client():
    if settings.app_env == "test":
        return None
    try:
        import redis

        return redis.Redis.from_url(
            settings.redis_url, socket_connect_timeout=0.5, socket_timeout=0.5
        )
    except Exception:
        return None


def _read_cache(key: str) -> dict | None:
    client = _redis_client()
    if client is not None:
        try:
            raw = client.get(key)
            return json.loads(raw) if raw else None
        except Exception:
            pass
    with _LOCK:
        value = _LOCAL.get(key)
    if not value:
        return None
    expires_at = value.get("expires_at")
    if isinstance(expires_at, str):
        try:
            if datetime.fromisoformat(expires_at) <= datetime.now(UTC):
                with _LOCK:
                    _LOCAL.pop(key, None)
                return None
        except ValueError:
            return None
    return value


def _write_cache(key: str, value: dict) -> None:
    ttl = max(300, settings.people_pdl_company_cache_ttl_seconds)
    stored = {
        **value,
        "expires_at": (datetime.now(UTC) + timedelta(seconds=ttl)).isoformat(),
    }
    client = _redis_client()
    if client is not None:
        try:
            client.set(key, json.dumps(stored), ex=ttl)
            return
        except Exception:
            pass
    with _LOCK:
        _LOCAL[key] = stored


def clear_local_pdl_companies() -> None:
    with _LOCK:
        _LOCAL.clear()


def _normalize_website(value: object) -> str | None:
    raw = str(value or "").strip().lower()
    if not raw or "." not in raw:
        return None
    raw = raw.removeprefix("https://").removeprefix("http://").removeprefix("www.")
    return raw.split("/")[0].strip() or None


def _names_agree(asked: str, answered: str | None) -> bool:
    """Guard against PDL answering with a different organization.

    Deliberately conservative about the cases that matter operationally: a
    hospital and its university, a subsidiary and its parent, or two unrelated
    companies that merely share a token. One name must contain the other's
    normalized form, not merely overlap with it.
    """

    left = normalize_company_name(asked)
    right = normalize_company_name(answered)
    if not left or not right:
        return False
    if left == right:
        return True
    left_tokens = left.split()
    right_tokens = right.split()
    # Containment in either direction: "vanderbilt health" vs
    # "vanderbilt university medical center" does NOT pass, while
    # "cisco" vs "cisco systems" does.
    return (
        left_tokens == right_tokens[: len(left_tokens)]
        or right_tokens == left_tokens[: len(right_tokens)]
    )


def identity_from_cache(
    *, raw_name: str, normalized_name: str, domain: str | None
) -> PdlCompanyIdentity | None:
    cached = _read_cache(_cache_key(normalized_name, domain))
    if not cached:
        return None
    payload = {
        key: value
        for key, value in cached.items()
        if key in PdlCompanyIdentity.__dataclass_fields__
    }
    payload.setdefault("raw_name", raw_name)
    payload.setdefault("normalized_name", normalized_name)
    aliases = payload.get("aliases")
    payload["aliases"] = tuple(aliases) if isinstance(aliases, list) else ()
    metric(
        "people_company_resolution_total",
        provider="pdl",
        result="cache_hit",
        source=str(payload.get("source", "unknown")),
    )
    return PdlCompanyIdentity(**payload)


def cache_identity(identity: PdlCompanyIdentity, *, domain: str | None) -> None:
    """Cache both resolved and unresolved outcomes.

    Caching an unresolved company matters as much as caching a resolved one:
    without it every retry re-spends a company-enrichment call to learn the
    same thing.
    """

    _write_cache(_cache_key(identity.normalized_name, domain), asdict(identity))


def build_identity(
    *,
    raw_name: str,
    normalized_name: str,
    aliases: tuple[str, ...],
    verified_domain: str | None,
    company: dict,
    source: str,
    asked_name: str,
) -> PdlCompanyIdentity:
    """Turn one Company Enrichment response into an identity, or reject it."""

    base = {
        "raw_name": raw_name,
        "normalized_name": normalized_name,
        "aliases": aliases,
        "verified_domain": verified_domain,
        "resolved_at": datetime.now(UTC).isoformat(),
    }
    likelihood = company.get("likelihood")
    likelihood_value = int(likelihood) if isinstance(likelihood, int) else 0
    company_id = company.get("id")
    company_id = company_id.strip() if isinstance(company_id, str) else None
    answered_name = company.get("name") if isinstance(company.get("name"), str) else None
    answered_site = _normalize_website(company.get("website"))

    if not company_id:
        return PdlCompanyIdentity(
            **base, source="unresolved", rejection_reason="no_company_id"
        )
    minimum = max(1, settings.people_pdl_company_min_likelihood)
    if likelihood_value < minimum:
        # PDL itself is unsure. Guessing here is how a hospital becomes its
        # university, or a subsidiary becomes its parent.
        return PdlCompanyIdentity(
            **base,
            source="unresolved",
            rejection_reason="below_likelihood_threshold",
        )
    if source == "pdl_company_enrich_domain":
        # The domain is the evidence, so the answer only has to be about that
        # domain. PDL echoes the matched website.
        if answered_site and verified_domain and answered_site != verified_domain:
            if not _names_agree(asked_name, answered_name):
                return PdlCompanyIdentity(
                    **base,
                    source="unresolved",
                    rejection_reason="domain_answer_mismatch",
                )
    elif not _names_agree(asked_name, answered_name):
        # Name-only resolution: the answer must actually be the company we
        # asked about, not a similarly-named organization.
        return PdlCompanyIdentity(
            **base, source="unresolved", rejection_reason="ambiguous_name_match"
        )
    return PdlCompanyIdentity(
        **base,
        pdl_company_id=company_id,
        pdl_company_name=answered_name,
        pdl_company_website=answered_site,
        source=source,
        confidence=round(min(1.0, likelihood_value / 10), 2),
    )
