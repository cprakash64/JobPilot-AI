"""Resolve a real company logo for a job card, safely.

We never guess an unrelated domain from an ATS slug (that would show the wrong
brand). A logo is only returned when we can tie the company to a known domain:

  1. a logo URL the ATS itself provided
  2. an explicit ``logo_url`` in the source catalog entry
  3. an explicit ``domain`` in the source catalog entry -> derived logo URL
  4. a curated company -> domain map (well-known employers) -> derived logo URL
  5. otherwise nothing (the frontend renders an initial-letter avatar)

The derived logo URL uses Google's free, key-less favicon endpoint (Clearbit's
logo API was sunset, which is why previously-resolved logos silently 404'd and
every card fell back to an initial). If the favicon 404s the frontend's
<img onError> falls back to the initial avatar, so a missing or broken logo
never breaks a card.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal, TypedDict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import CompanyBranding

Confidence = Literal["high", "medium", "low"]


class LogoResolution(TypedDict):
    company_domain: str
    company_logo_url: str
    confidence: Confidence


# Curated, verified employer -> primary domain map. Only companies we are
# confident about belong here; an unknown company falls through to (a) safe
# website-metadata discovery when a domain can be verified, or (b) the neutral
# placeholder — never a guessed domain, never an initial-letter avatar.
#
# This list is deliberately broad (not "the five companies from the bug
# report") — it was built by cross-referencing every company name in
# sources_config.json against their real, well-known primary domain. New
# catalog entries should prefer an explicit "domain" field in
# sources_config.json (see CatalogEntry) over adding here; this dict remains
# for companies the catalog doesn't carry a domain for yet.
COMPANY_DOMAINS: dict[str, str] = {
    "openai": "openai.com",
    "deepgram": "deepgram.com",
    "plaid": "plaid.com",
    "temporal": "temporal.io",
    "temporal technologies": "temporal.io",
    "chime": "chime.com",
    "stripe": "stripe.com",
    "gitlab": "gitlab.com",
    "ramp": "ramp.com",
    "notion": "notion.so",
    "notion labs": "notion.so",
    "linear": "linear.app",
    "cohere": "cohere.com",
    "databricks": "databricks.com",
    "dropbox": "dropbox.com",
    "robinhood": "robinhood.com",
    "discord": "discord.com",
    "cloudflare": "cloudflare.com",
    "airbnb": "airbnb.com",
    "affirm": "affirm.com",
    "airtable": "airtable.com",
    "amplitude": "amplitude.com",
    "anthropic": "anthropic.com",
    "asana": "asana.com",
    "brex": "brex.com",
    "coinbase": "coinbase.com",
    "datadog": "datadoghq.com",
    "doordash": "doordash.com",
    "figma": "figma.com",
    "instacart": "instacart.com",
    "lyft": "lyft.com",
    "mongodb": "mongodb.com",
    "reddit": "reddit.com",
    "snowflake": "snowflake.com",
    "twilio": "twilio.com",
    # Expanded to cover the full source catalog (apps/api/app/jobs/sources_config.json).
    "abnormal security": "abnormalsecurity.com",
    "airops": "airops.com",
    "airbyte": "airbyte.com",
    "angellist": "angellist.com",
    "angi": "angi.com",
    "anyscale": "anyscale.com",
    "assemblyai": "assemblyai.com",
    "benchling": "benchling.com",
    "betterment": "betterment.com",
    "blend": "blend.com",
    "braze": "braze.com",
    "builder": "builder.io",
    "builder.io": "builder.io",
    "calendly": "calendly.com",
    "carta": "carta.com",
    "cerebras": "cerebras.ai",
    "chainguard": "chainguard.dev",
    "checkr": "checkr.com",
    "circleci": "circleci.com",
    "clickhouse": "clickhouse.com",
    "clickup": "clickup.com",
    "cockroachdb": "cockroachlabs.com",
    "column": "column.com",
    "confluent": "confluent.io",
    "contentful": "contentful.com",
    "coursera": "coursera.org",
    "customer.io": "customer.io",
    "cybereason": "cybereason.com",
    "descript": "descript.com",
    "drata": "drata.com",
    "dremio": "dremio.com",
    "duolingo": "duolingo.com",
    "elastic": "elastic.co",
    "elevenlabs": "elevenlabs.io",
    "faire": "faire.com",
    "fastly": "fastly.com",
    "fivetran": "fivetran.com",
    "flexport": "flexport.com",
    "greenhouse": "greenhouse.io",
    "gusto": "gusto.com",
    "handshake": "joinhandshake.com",
    "highnote": "highnote.com",
    "hightouch": "hightouch.com",
    "huntress": "huntress.com",
    "imply": "imply.io",
    "iterable": "iterable.com",
    "jumpcloud": "jumpcloud.com",
    "khan academy": "khanacademy.org",
    "kong": "konghq.com",
    "lambda": "lambdalabs.com",
    "langchain": "langchain.com",
    "launchdarkly": "launchdarkly.com",
    "lithic": "lithic.com",
    "llamaindex": "llamaindex.ai",
    "make": "make.com",
    "marqeta": "marqeta.com",
    "mercari": "mercari.com",
    "mercury": "mercury.com",
    "miro": "miro.com",
    "mistral": "mistral.ai",
    "mixpanel": "mixpanel.com",
    "modal": "modal.com",
    "modern treasury": "moderntreasury.com",
    "monzo": "monzo.com",
    "neon": "neon.tech",
    "nerdwallet": "nerdwallet.com",
    "netlify": "netlify.com",
    "nuro": "nuro.ai",
    "offerup": "offerup.com",
    "okta": "okta.com",
    "orca": "orca.security",
    "otter": "otter.ai",
    "outreach": "outreach.io",
    "oyster": "oysterhr.com",
    "pandadoc": "pandadoc.com",
    "pinecone": "pinecone.io",
    "pinterest": "pinterest.com",
    "planetscale": "planetscale.com",
    "poshmark": "poshmark.com",
    "postman": "postman.com",
    "railway": "railway.app",
    "remote": "remote.com",
    "render": "render.com",
    "runway": "runwayml.com",
    "samsara": "samsara.com",
    "sanity": "sanity.io",
    "secureframe": "secureframe.com",
    "semgrep": "semgrep.dev",
    "sentry": "sentry.io",
    "sofi": "sofi.com",
    "squarespace": "squarespace.com",
    "starburst": "starburstdata.com",
    "stockx": "stockx.com",
    "storyblok": "storyblok.com",
    "supabase": "supabase.com",
    "synthesia": "synthesia.io",
    "sysdig": "sysdig.com",
    "tailscale": "tailscale.com",
    "taskrabbit": "taskrabbit.com",
    "thumbtack": "thumbtack.com",
    "together ai": "together.ai",
    "twitch": "twitch.tv",
    "typeface": "typeface.ai",
    "udemy": "udemy.com",
    "unit": "unit.co",
    "upstart": "upstart.com",
    "vanta": "vanta.com",
    "vercel": "vercel.com",
    "verkada": "verkada.com",
    "visa": "visa.com",
    "waymo": "waymo.com",
    "wealthfront": "wealthfront.com",
    "weaviate": "weaviate.io",
    "webflow": "webflow.com",
    "workato": "workato.com",
    "writer": "writer.com",
    "zapier": "zapier.com",
    "zoox": "zoox.com",
    "n8n": "n8n.io",
}

_DOMAIN_RE = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")


def resolve_company_logo(
    company_name: str,
    *,
    source_type: str | None = None,  # noqa: ARG001 - reserved for ATS-specific rules
    application_url: str | None = None,  # noqa: ARG001 - reserved; never slug-guessed
    catalog_domain: str | None = None,
    catalog_logo_url: str | None = None,
    ats_logo_url: str | None = None,
) -> LogoResolution:
    """Best-effort logo/domain for ``company_name``. Never guesses a domain from
    an ATS slug or application URL — only explicit/curated sources are trusted."""
    if ats_logo_url:
        return {
            "company_domain": _clean_domain(catalog_domain) or _known_domain(company_name),
            "company_logo_url": ats_logo_url,
            "confidence": "high",
        }
    if catalog_logo_url:
        return {
            "company_domain": _clean_domain(catalog_domain) or _known_domain(company_name),
            "company_logo_url": catalog_logo_url,
            "confidence": "high",
        }
    catalog = _clean_domain(catalog_domain)
    if catalog:
        return {"company_domain": catalog, "company_logo_url": logo_url_for_domain(catalog), "confidence": "high"}
    known = _known_domain(company_name)
    if known:
        return {"company_domain": known, "company_logo_url": logo_url_for_domain(known), "confidence": "medium"}
    return {"company_domain": "", "company_logo_url": "", "confidence": "low"}


def logo_url_for_domain(domain: str) -> str:
    """Free, key-less, reliable favicon/logo endpoint for a company domain.

    Google's favicon service returns the site's brand mark and, unlike the old
    Clearbit logo API, is not sunset. A 404 is still handled by the frontend
    <img onError> fallback, so a missing icon never breaks a card."""
    return f"https://www.google.com/s2/favicons?domain={domain}&sz=64"


def _known_domain(company_name: str) -> str:
    return COMPANY_DOMAINS.get(_normalize_company(company_name), "")


def _normalize_company(company_name: str) -> str:
    text = (company_name or "").strip().lower()
    # Drop common corporate suffixes so "Plaid, Inc." matches "plaid".
    text = re.sub(r"[.,]", "", text)
    text = re.sub(r"\b(inc|llc|ltd|corp|co|technologies|labs)\b", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean_domain(domain: str | None) -> str:
    if not domain:
        return ""
    value = domain.strip().lower()
    value = re.sub(r"^https?://", "", value)
    value = re.sub(r"^www\.", "", value)
    value = value.split("/")[0].strip()
    return value if _DOMAIN_RE.match(value) else ""


# Public alias — the backfill command and job-ingestion service both need the
# same normalization the resolver uses internally, so a branding row saved by
# one is found by the other.
normalize_company_key = _normalize_company


# --------------------------------------------------------------------------- #
# Persisted resolution: one CompanyBranding row per employer, reused across
# every job posting from that employer instead of re-resolving per job.
# --------------------------------------------------------------------------- #
def get_or_create_company_branding(
    db: Session,
    company_name: str,
    *,
    catalog_domain: str | None = None,
    catalog_logo_url: str | None = None,
    ats_logo_url: str | None = None,
) -> CompanyBranding:
    """Resolve-once-per-employer. A row already marked ``resolved`` is reused
    as-is (this is what makes a refreshed batch of jobs from the same company
    "free" — see task Part 8). A row that previously resolved to "nothing" is
    NOT retried on every ingest (that would hammer nothing productively); it
    is retried at most once per day via ``last_verified_at``, or immediately
    when new ATS/catalog data appears that the last attempt didn't have."""
    key = _normalize_company(company_name)
    if not key:
        key = "unknown"
    existing = db.scalar(select(CompanyBranding).where(CompanyBranding.normalized_key == key))

    has_new_signal = bool(ats_logo_url or catalog_domain or catalog_logo_url)
    stale = existing is not None and existing.resolution_status != "resolved" and _is_stale(existing.last_verified_at)
    if existing is not None and existing.resolution_status == "resolved" and not has_new_signal:
        return existing
    if existing is not None and not has_new_signal and not stale:
        return existing

    resolved = resolve_company_logo(
        company_name,
        catalog_domain=catalog_domain,
        catalog_logo_url=catalog_logo_url,
        ats_logo_url=ats_logo_url,
    )
    source = "ats" if ats_logo_url else "catalog" if (catalog_domain or catalog_logo_url) else "curated" if resolved["company_logo_url"] else "none"
    now = datetime.now(UTC)
    if existing is None:
        existing = CompanyBranding(normalized_key=key, canonical_name=company_name or key)
        db.add(existing)
    existing.canonical_name = company_name or existing.canonical_name
    existing.domain = resolved["company_domain"] or None
    existing.logo_url = resolved["company_logo_url"] or None
    existing.source = source
    existing.resolution_status = "resolved" if resolved["company_logo_url"] else "unresolved"
    existing.last_verified_at = now
    db.flush()
    return existing


def _is_stale(last_verified_at: datetime | None, *, hours: int = 24) -> bool:
    if last_verified_at is None:
        return True
    checked = last_verified_at if last_verified_at.tzinfo else last_verified_at.replace(tzinfo=UTC)
    return (datetime.now(UTC) - checked).total_seconds() > hours * 3600
