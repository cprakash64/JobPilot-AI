"""Resolve a real company logo for a job card, safely.

We never guess an unrelated domain from an ATS slug (that would show the wrong
brand). A logo is only returned when we can tie the company to a known domain:

  1. a curated official logo override where a site's favicon is not its brand
  2. a logo URL the ATS itself provided
  3. an explicit ``logo_url`` in the source catalog entry
  4. an explicit/verified official domain -> derived logo URL
  5. a curated company -> domain map (well-known employers) -> derived logo URL
  6. otherwise nothing (the frontend renders a neutral placeholder)

The derived logo URL uses Google's free, key-less favicon endpoint (Clearbit's
logo API was sunset, which is why previously-resolved logos silently 404'd and
every card fell back to an initial). If the favicon 404s, the frontend's
<img onError> falls back to the neutral placeholder, so a missing or broken
logo never breaks a card.
"""

from __future__ import annotations

import ipaddress
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Literal, TypedDict
from urllib.parse import urljoin, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.jobs.safe_fetch import (
    FetchFailedError,
    UnsafeUrlError,
    safe_fetch_html,
    safe_fetch_image,
)
from app.models.entities import CompanyBranding

Confidence = Literal["high", "medium", "low"]


class LogoResolution(TypedDict):
    company_domain: str
    company_logo_url: str
    confidence: Confidence
    provenance: str


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
    # Employers commonly present in the SimplifyJobs new-grad feed. These are
    # explicit, verified domains rather than guesses from company names.
    "kayak": "kayak.com",
    "l3harris": "l3harris.com",
    "l3harris technologies": "l3harris.com",
    "globus medical": "globusmedical.com",
    "the boeing company": "boeing.com",
    "boeing": "boeing.com",
    "rtx": "rtx.com",
    "t rowe price": "troweprice.com",
    "medtronic": "medtronic.com",
    "northrop grumman": "northropgrumman.com",
    "appian": "appian.com",
    "zello": "zello.com",
    "moog": "moog.com",
    "spacex": "spacex.com",
    "grail": "grail.com",
    "digicert": "digicert.com",
    "deloitte": "deloitte.com",
    "northwell health": "northwell.edu",
    "general dynamics mission systems": "gdmissionsystems.com",
    "copart": "copart.com",
    "schweitzer engineering laboratories": "selinc.com",
    "virtu financial": "virtu.com",
    "southwest airlines": "southwest.com",
    "crackajack digital solutions": "crackajackllc.com",
    # Public SmartRecruiters catalog employers. Their application URLs share
    # the SmartRecruiters host, so the employer domain cannot be recovered
    # from the job URL and must come from verified catalog data.
    "servicenow": "servicenow.com",
    "syngenta group": "syngenta.com",
    "bosch": "bosch.com",
    "nbcuniversal": "nbcuniversal.com",
    "ubisoft": "ubisoft.com",
    "sportradar": "sportradar.com",
    "ifs": "ifs.com",
    "nearmap": "nearmap.com",
    "experian": "experian.com",
    "nielseniq": "nielseniq.com",
    "aecom": "aecom.com",
    "h&m group": "hmgroup.com",
    "red bull": "redbull.com",
    "western digital": "westerndigital.com",
    "turner & townsend": "turnerandtownsend.com",
    "prosidian consulting": "prosidian.com",
    # Simplify identifies this employer as "Pogo Technologies, Inc."; suffix
    # normalization reduces that name to "pogo".
    "pogo": "joinpogo.com",
}

_DOMAIN_RE = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")

# Some sites publish no useful favicon. These are verified first-party raster
# assets from the employer's own domain, not third-party mirrors.
COMPANY_LOGO_URLS: dict[str, str] = {
    "crackajack digital solutions": (
        "https://www.crackajackllc.com/wp-content/uploads/2026/02/logo-3.png"
    ),
}


def is_untrusted_simplify_logo_url(value: str | None) -> bool:
    """True for Simplify's mutable third-party image mirror.

    Old code constructed these URLs from UUIDs without checking the profile.
    That produced visibly wrong logos and left bad bytes in the local cache.
    """
    try:
        parsed = urlparse(value or "")
    except ValueError:
        return False
    return (
        (parsed.hostname or "").lower() == "storage.googleapis.com"
        and parsed.path.startswith("/simplify-imgs/")
    )


def is_safe_logo_url(value: str | None) -> bool:
    """Structural allowlist for externally supplied logo URLs.

    DNS/IP validation still happens in ``safe_fetch_image`` before the backend
    fetches anything. This check prevents unsafe or non-HTTPS URLs from being
    persisted or handed directly to the browser.
    """
    try:
        parsed = urlparse(value or "")
        hostname = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or hostname == "localhost"
        or hostname.endswith((".localhost", ".local", ".internal"))
        or is_untrusted_simplify_logo_url(value)
    ):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    )


class _OfficialLogoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.logo_images: list[str] = []
        self.icons: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        values = {key.lower(): (value or "") for key, value in attrs}
        if tag.lower() == "img" and values.get("src"):
            identity = " ".join(
                (
                    values.get("alt", ""),
                    values.get("class", ""),
                    values.get("id", ""),
                )
            ).lower()
            if "logo" in identity or "brand" in identity:
                self.logo_images.append(values["src"])
        if tag.lower() == "link" and values.get("href"):
            rel = values.get("rel", "").lower()
            if "icon" in rel:
                self.icons.append(values["href"])


def discover_official_logo_url(domain: str) -> str:
    """Resolve and verify one raster logo from the canonical employer site.

    Candidates must be declared by the official site, remain on the canonical
    host/subdomains, use HTTPS, and pass the SSRF-safe image fetch. The result
    is intended for persisted CompanyBranding metadata, never per-card fetches.
    """
    clean_domain = _clean_domain(domain)
    if not clean_domain:
        return ""
    homepage = f"https://{clean_domain}/"
    try:
        page = safe_fetch_html(homepage)
    except (UnsafeUrlError, FetchFailedError):
        return ""
    parser = _OfficialLogoParser()
    parser.feed(page.content.decode("utf-8", errors="replace"))
    for raw_candidate in [*parser.logo_images, *parser.icons]:
        candidate = urljoin(page.final_url, raw_candidate)
        parsed = urlparse(candidate)
        hostname = (parsed.hostname or "").lower()
        if not (
            is_safe_logo_url(candidate)
            and (
                hostname == clean_domain
                or hostname.endswith(f".{clean_domain}")
            )
        ):
            continue
        try:
            verified = safe_fetch_image(candidate)
        except (UnsafeUrlError, FetchFailedError):
            continue
        return verified.final_url
    return ""


def resolve_company_logo(
    company_name: str,
    *,
    source_type: str | None = None,  # noqa: ARG001 - reserved for ATS-specific rules
    application_url: str | None = None,
    catalog_domain: str | None = None,
    catalog_logo_url: str | None = None,
    ats_logo_url: str | None = None,
) -> LogoResolution:
    """Best-effort logo/domain for ``company_name``. Never guesses a domain from
    an ATS slug or application URL — only explicit/curated sources are trusted."""
    known = _known_domain(company_name)
    official_logo = COMPANY_LOGO_URLS.get(_normalize_company(company_name), "")
    if official_logo:
        return {
            "company_domain": _clean_domain(catalog_domain) or known,
            "company_logo_url": official_logo,
            "confidence": "high",
            "provenance": "curated",
        }
    if is_safe_logo_url(ats_logo_url):
        return {
            "company_domain": _clean_domain(catalog_domain) or _known_domain(company_name),
            "company_logo_url": ats_logo_url,
            "confidence": "high",
            "provenance": "ats",
        }
    if is_safe_logo_url(catalog_logo_url) and not (
        source_type == "simplifyjobs"
        and is_untrusted_simplify_logo_url(catalog_logo_url)
    ):
        return {
            "company_domain": _clean_domain(catalog_domain) or _known_domain(company_name),
            "company_logo_url": catalog_logo_url,
            "confidence": "high",
            "provenance": "catalog_asset",
        }
    catalog = _clean_domain(catalog_domain)
    if catalog:
        return {
            "company_domain": catalog,
            "company_logo_url": logo_url_for_domain(catalog),
            "confidence": "high",
            "provenance": "domain_favicon",
        }
    if known:
        return {
            "company_domain": known,
            "company_logo_url": logo_url_for_domain(known),
            "confidence": "medium",
            "provenance": "domain_favicon",
        }
    # A direct employer-owned application URL is useful evidence, but shared ATS
    # hosts (Workday, Greenhouse, Lever, etc.) are deliberately excluded: using
    # their favicon would show the ATS logo as though it were the employer.
    direct = _employer_domain_from_application_url(application_url)
    if direct:
        return {
            "company_domain": direct,
            "company_logo_url": logo_url_for_domain(direct),
            "confidence": "medium",
            "provenance": "domain_favicon",
        }
    return {
        "company_domain": "",
        "company_logo_url": "",
        "confidence": "low",
        "provenance": "none",
    }


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


clean_company_domain = _clean_domain


_SHARED_JOB_HOSTS = (
    # Documentation/test placeholders are never evidence of an employer-owned
    # application host. Treating them as a real company domain made a synthetic
    # fixture look like successfully resolved production branding.
    "example.com",
    "example.org",
    "example.net",
    "myworkdayjobs.com",
    "workday.com",
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "smartrecruiters.com",
    "workable.com",
    "breezy.hr",
    "recruitee.com",
    "teamtailor.com",
    "applytojob.com",
    "simplify.jobs",
)


def _employer_domain_from_application_url(application_url: str | None) -> str:
    """Return a domain only when the application is hosted by the employer.

    This is intentionally conservative. It improves branding for direct links
    such as ``jobs.boeing.com`` while refusing shared ATS/aggregator domains.
    """
    if not application_url:
        return ""
    try:
        host = (urlparse(application_url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""
    if not host or not _DOMAIN_RE.match(host):
        return ""
    if any(host == shared or host.endswith(f".{shared}") for shared in _SHARED_JOB_HOSTS):
        return ""
    parts = host.split(".")
    if len(parts) > 2 and parts[-2:] not in (["co", "uk"], ["com", "au"]):
        return ".".join(parts[-2:])
    return host


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
    application_url: str | None = None,
    source_type: str | None = None,
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

    legacy_untrusted = bool(
        existing is not None and is_untrusted_simplify_logo_url(existing.logo_url)
    )
    application_signal = bool(
        application_url and (existing is None or existing.resolution_status != "resolved")
    )
    has_new_signal = bool(ats_logo_url or catalog_domain or catalog_logo_url or application_signal)
    stale = (
        existing is not None
        and existing.resolution_status != "resolved"
        and _is_stale(existing.last_verified_at)
    )
    if (
        existing is not None
        and existing.resolution_status == "resolved"
        and not has_new_signal
        and not legacy_untrusted
    ):
        return existing
    if existing is not None and not has_new_signal and not stale and not legacy_untrusted:
        return existing

    resolved = resolve_company_logo(
        company_name,
        source_type=source_type,
        catalog_domain=catalog_domain,
        catalog_logo_url=catalog_logo_url,
        ats_logo_url=ats_logo_url,
        application_url=application_url,
    )
    source = resolved["provenance"]
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


def refresh_official_company_logo(
    db: Session,
    branding: CompanyBranding,
    *,
    force: bool = False,
) -> CompanyBranding:
    """Upgrade derived favicon metadata from the canonical official website.

    This is called by the existing backend backfill process, not by card
    rendering. A fresh verified official-site result is reused.
    """
    if (
        not force
        and branding.source == "official_site"
        and branding.resolution_status == "resolved"
        and not _is_stale(branding.last_verified_at)
    ):
        return branding
    if not branding.domain:
        return branding
    logo_url = discover_official_logo_url(branding.domain)
    branding.last_verified_at = datetime.now(UTC)
    if logo_url:
        branding.logo_url = logo_url
        branding.source = "official_site"
        branding.resolution_status = "resolved"
    db.flush()
    return branding


def _is_stale(last_verified_at: datetime | None, *, hours: int = 24) -> bool:
    if last_verified_at is None:
        return True
    checked = last_verified_at if last_verified_at.tzinfo else last_verified_at.replace(tzinfo=UTC)
    return (datetime.now(UTC) - checked).total_seconds() > hours * 3600
