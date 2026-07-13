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
from typing import Literal, TypedDict

Confidence = Literal["high", "medium", "low"]


class LogoResolution(TypedDict):
    company_domain: str
    company_logo_url: str
    confidence: Confidence


# Curated, verified employer -> primary domain map. Only companies we are
# confident about belong here; an unknown company falls back to an initial.
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
    # Additional well-known employers commonly surfaced by the catalog.
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
