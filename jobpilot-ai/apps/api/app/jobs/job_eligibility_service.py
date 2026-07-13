"""Hard eligibility filtering that runs BEFORE fit scoring.

Fit scoring only ranks jobs that are already eligible. Location, role family and
seniority are hard filters by default -- a job with a great skill overlap is
still hidden if it is in the wrong country, the wrong seniority band, or an
unrelated role family.

Entry point: ``evaluate_eligibility(profile, job, include_unknown_location=...)``
returns an :class:`EligibilityResult` with ``eligible``, human ``reasons``,
machine ``reason_codes`` and per-dimension ``flags``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.jobs.job_matching_service import JobView, ProfileView

# --------------------------------------------------------------------------- #
# Location knowledge
# --------------------------------------------------------------------------- #
US_STATE_ABBRS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
    "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT",
    "VA", "WA", "WV", "WI", "WY", "DC",
}
US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa",
    "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan",
    "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire",
    "new jersey", "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington", "west virginia",
    "wisconsin", "wyoming",
}
US_CITIES = {
    "san francisco", "new york", "new york city", "los angeles", "seattle", "austin", "boston",
    "chicago", "denver", "atlanta", "dallas", "houston", "san jose", "san diego", "washington",
    "washington, d.c.", "washington dc", "portland", "phoenix", "philadelphia", "miami",
    "minneapolis", "pittsburgh", "raleigh", "nashville", "charlotte", "columbus", "detroit",
    "salt lake city", "san mateo", "palo alto", "mountain view", "sunnyvale", "cupertino",
    "menlo park", "redmond", "bellevue", "brooklyn", "cambridge", "irvine", "santa monica",
}
US_PHRASES = [
    "united states", "u.s.a", "u.s.a.", "usa", "u.s.", "us-remote", "us remote", "remote us",
    "remote - us", "remote, us", "remote-us", "united states remote", "us only", "u.s only",
    "anywhere in the us", "remote (us)", "(us)", "us-based", "us based",
]

# Non-US countries/regions -> keywords. Order-independent; substring matched on a
# lowercased location string.
NON_US_KEYWORDS: dict[str, list[str]] = {
    "canada": ["canada", "toronto", "vancouver", "montreal", "ontario", "ottawa", "calgary",
               "quebec", "ca-remote", "ca - remote", "canada remote", "remote canada"],
    "india": ["india", "bengaluru", "bangalore", "hyderabad", "pune", "mumbai", "delhi",
              "gurgaon", "gurugram", "noida", "chennai", "kolkata", "remote india"],
    "emea": ["emea"],
    "europe": ["europe", "united kingdom", "u.k.", " uk", "uk-", "england", "london", "germany",
               "berlin", "munich", "france", "paris", "netherlands", "amsterdam", "ireland",
               "dublin", "spain", "madrid", "barcelona", "poland", "warsaw", "portugal", "lisbon",
               "sweden", "stockholm", "switzerland", "zurich", "italy", "romania", "bucharest"],
    "apac": ["singapore", "australia", "sydney", "melbourne", "japan", "tokyo", "china",
             "shanghai", "beijing", "hong kong", "korea", "seoul", "philippines", "manila",
             "indonesia", "jakarta", "malaysia", "kuala lumpur", "new zealand", "vietnam",
             "taiwan", "thailand", "bangkok"],
    "latam": ["brazil", "sao paulo", "mexico", "argentina", "buenos aires", "colombia",
              "bogota", "chile", "santiago", "costa rica", "peru", "uruguay"],
    "mena": ["dubai", "u.a.e", "uae", "abu dhabi", "israel", "tel aviv", "saudi", "riyadh",
             "egypt", "cairo", "turkey", "istanbul", "nigeria", "lagos", "kenya", "nairobi",
             "south africa"],
}
# Non-US city names used to disambiguate "City, CA" (California) from Canada, etc.
_NON_US_CITY_TOKENS = {kw for kws in NON_US_KEYWORDS.values() for kw in kws}
_WORLDWIDE = ["worldwide", "global", "anywhere", "fully distributed", "remote - anywhere"]


@dataclass
class LocationInfo:
    raw: str
    countries: set[str] = field(default_factory=set)  # includes "us" when US-detected
    remote: bool = False
    worldwide: bool = False
    confidence: float = 0.0

    @property
    def is_us(self) -> bool:
        return "us" in self.countries

    @property
    def is_known(self) -> bool:
        return bool(self.countries) or self.worldwide


def normalize_location(location: str | None, workplace_type: str | None = None) -> LocationInfo:
    raw = (location or "").strip()
    low = f" {raw.lower()} "
    info = LocationInfo(raw=raw)
    info.remote = "remote" in low or "distributed" in low or (workplace_type or "").lower() == "remote"
    info.worldwide = any(word in low for word in _WORLDWIDE)

    for country, keywords in NON_US_KEYWORDS.items():
        if any(kw in low for kw in keywords):
            info.countries.add(country)

    if _detect_us(low, raw):
        info.countries.add("us")

    if info.countries:
        info.confidence = 0.9
    elif info.worldwide:
        info.confidence = 0.5
    elif info.remote:
        info.confidence = 0.2
    return info


def _detect_us(low: str, raw: str) -> bool:
    if any(phrase in low for phrase in US_PHRASES):
        return True
    if any(f" {name} " in low or f"{name}," in low for name in US_STATE_NAMES):
        return True
    if any(city in low for city in US_CITIES):
        return True
    # standalone "US" token
    if re.search(r"\b(u\.?s\.?a?)\b", low) and "aus" not in low:
        return True
    # "City, ST" — treat CA as California only when the city is not a non-US city.
    for match in re.finditer(r"([a-z .'\-]+),\s*([a-z]{2})\b", low):
        city = match.group(1).strip()
        st = match.group(2).upper()
        if st not in US_STATE_ABBRS:
            continue
        if st == "CA" and any(token in city for token in _NON_US_CITY_TOKENS):
            continue  # e.g. "Toronto, CA" -> Canada, handled by NON_US
        return True
    return False


def _allowed_countries(user_locations: list[str]) -> set[str]:
    """Countries the user explicitly allows. Empty means no geo restriction."""
    allowed: set[str] = set()
    for entry in user_locations or []:
        low = f" {entry.lower()} "
        if any(p in low for p in [" united states ", " usa ", " us ", " u.s. ", " america "]) or entry.strip().lower() == "us":
            allowed.add("us")
        for country, keywords in NON_US_KEYWORDS.items():
            if any(kw.strip() in low for kw in keywords):
                allowed.add(country)
    return allowed


def is_location_eligible(
    job_location: str | None,
    workplace_type: str | None,
    user_locations: list[str],
    *,
    include_unknown_location: bool = False,
) -> tuple[bool, str | None, str | None]:
    allowed = _allowed_countries(user_locations)
    if not allowed:
        # The user did not restrict geography -> do not hard-filter by location.
        return True, None, None

    info = normalize_location(job_location, workplace_type)

    if info.worldwide:
        return True, None, None  # worldwide/global includes the user's country
    if info.countries:
        if info.countries & allowed:
            return True, None, None
        pretty = ", ".join(sorted(info.countries)).upper()
        return False, "location:non_us", f"Location “{info.raw or pretty}” is outside your selected locations"

    # No country could be determined.
    if include_unknown_location:
        return True, None, None
    if info.remote:
        return False, "location:unknown", "Remote role with an unspecified country"
    return False, "location:unknown", f"Location “{info.raw or 'unknown'}” could not be confirmed"


# --------------------------------------------------------------------------- #
# Seniority knowledge
# --------------------------------------------------------------------------- #
# Titles that make a role too senior for an entry/junior candidate.
SENIOR_TITLE_TOKENS = [
    "senior", "sr.", "sr ", " sr", "staff", "principal", "lead", "manager", "director",
    "head of", "head,", "vp", "vice president", "architect", "distinguished", "fellow",
    "executive",
]
# Explicit entry/junior signals in a title.
JUNIOR_TITLE_TOKENS = [
    "new grad", "new-grad", "early career", "entry level", "entry-level", "junior", "associate",
    "university grad", "grad ", "graduate", "apprentice", "intern",
    "engineer i", "engineer 1", "software engineer i", "developer i",
]
YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years|yrs)", re.IGNORECASE)
LEVEL_RANK = {"intern": 0, "new grad": 1, "entry": 1, "junior": 2, "associate": 2, "mid": 3,
              "senior": 4, "staff": 5, "principal": 6, "lead": 5, "manager": 5, "director": 6}


def _title_has_senior_signal(title: str) -> str | None:
    low = f" {title.lower()} "
    # "engineer i"/"lead engineer" nuance: match tokens with word-ish boundaries.
    for token in SENIOR_TITLE_TOKENS:
        if token in low:
            # Avoid matching "lead" inside "leader of" false positives is fine; and avoid
            # "sr" matching inside words via the spaced variants above.
            return token.strip()
    return None


def _title_has_junior_signal(title: str) -> bool:
    low = f" {title.lower()} "
    return any(token in low for token in JUNIOR_TITLE_TOKENS)


def user_is_entry_level(target_levels: list[str], seniority_targets: list[str]) -> bool:
    """True when the user only targets intern/new-grad/entry/junior bands."""
    ranks: list[int] = []
    for level in [*(target_levels or []), *(seniority_targets or [])]:
        low = level.lower()
        matched = False
        for key, rank in LEVEL_RANK.items():
            if key in low:
                ranks.append(rank)
                matched = True
        # Year ranges like "0-1 years" / "1-3 years" imply entry/junior.
        years = [int(n) for n in re.findall(r"\d{1,2}", low)]
        if not matched and years:
            ranks.append(2 if max(years) <= 3 else (3 if max(years) <= 5 else 4))
    if not ranks:
        return False
    return max(ranks) <= 2  # nothing above "junior/associate"


def is_seniority_eligible(
    title: str,
    description: str,
    job_seniority: str | None,
    target_levels: list[str],
    seniority_targets: list[str],
) -> tuple[bool, str | None, str | None]:
    if not user_is_entry_level(target_levels, seniority_targets):
        return True, None, None  # user is open to mid/senior; no hard senior filter

    senior_token = _title_has_senior_signal(title)
    if senior_token and not _title_has_junior_signal(title):
        return False, "seniority:senior_title", f"“{title}” is a {senior_token}-level role above your target level"

    # Parsed seniority from the source (e.g. Ashby team/level) that is clearly senior.
    if job_seniority and LEVEL_RANK.get(job_seniority.lower().strip(), 0) >= 4 and not _title_has_junior_signal(title):
        return False, "seniority:senior_level", f"Role level “{job_seniority}” is above your target level"

    # Description demanding many years of experience.
    years = [int(m.group(1)) for m in YEARS_RE.finditer(description or "")]
    if years and max(years) >= 5:
        return False, "seniority:years", f"Requires {max(years)}+ years of experience"

    return True, None, None


# --------------------------------------------------------------------------- #
# Role-family knowledge
# --------------------------------------------------------------------------- #
ROLE_FAMILIES: dict[str, list[str]] = {
    "software_engineering": [
        "software engineer", "software developer", "software development engineer", "backend engineer",
        "back-end engineer", "back end engineer", "frontend engineer", "front-end engineer",
        "front end engineer", "full stack", "fullstack", "full-stack", "web engineer",
        "platform engineer", "application engineer", "api engineer", "systems engineer",
        "infrastructure engineer", "developer", "swe", "sde", "engineer i", "engineer ii",
        "engineer 1", "engineer 2",
    ],
    "ai_ml": [
        "ai engineer", "a.i. engineer", "artificial intelligence engineer", "applied ai",
        "applied scientist", "machine learning", "ml engineer", "ml scientist", "nlp engineer",
        "natural language", "computer vision", "cv engineer", "perception engineer", "mlops",
        "deep learning", "research engineer", "research scientist",
    ],
    "data": ["data engineer", "data scientist", "analytics engineer", "data analyst"],
    "devops": ["devops", "site reliability", "sre", "cloud engineer"],
    "security": ["security engineer", "security analyst", "appsec", "application security"],
    "design": ["designer", "ux engineer", "ui engineer", "design engineer", "product designer",
               "ux designer", "ui designer", "graphic designer", "creative"],
    "hardware": ["hardware engineer", "mechanical engineer", "electrical engineer", "firmware",
                 "asic", "fpga"],
    "customer_success": ["customer success", "customer experience", "customer support",
                         "support engineer", "technical support", "solutions engineer",
                         "solutions architect", "implementation"],
    "sales": ["sales engineer", "account executive", "account manager", "business development",
              "sales development", "sales representative", "pre-sales", "presales"],
    "product": ["product manager", "product management", "program manager", "project manager",
                "technical program manager", "product owner"],
    "partnerships": ["partner manager", "partnerships", "partner engineer", "alliances"],
    "marketing": ["marketing", "social measurement", "brand", "growth marketing", "content",
                  "communications", "seo", "demand generation"],
    "recruiting_hr": ["recruiter", "recruiting", "talent acquisition", "human resources",
                      "people operations", "people partner"],
    "finance_legal_ops": ["accountant", "accounting", "finance manager", "controller", "legal",
                          "counsel", "operations manager", "business operations", "biz ops",
                          "office manager"],
}
# Families the user typically must explicitly opt into; if a title clearly belongs
# to one of these and the user did NOT select it, the job is ineligible even if
# "engineer" appears in the title.
_OPT_IN_FAMILIES = {
    "design", "hardware", "customer_success", "sales", "product", "partnerships", "marketing",
    "recruiting_hr", "finance_legal_ops",
}
# Map a selected target-role string to a role family.
_ROLE_TO_FAMILY = [
    ("machine learning", "ai_ml"), ("ml engineer", "ai_ml"), ("ai engineer", "ai_ml"),
    ("applied ai", "ai_ml"), ("nlp", "ai_ml"), ("computer vision", "ai_ml"), ("mlops", "ai_ml"),
    ("data scientist", "data"), ("data engineer", "data"), ("data analyst", "data"),
    ("devops", "devops"), ("site reliability", "devops"), ("sre", "devops"),
    ("security", "security"),
    ("designer", "design"), ("design engineer", "design"), ("ux", "design"), ("ui", "design"),
    ("hardware", "hardware"), ("mechanical", "hardware"), ("electrical", "hardware"),
    ("customer success", "customer_success"), ("support", "customer_success"),
    ("solutions engineer", "customer_success"),
    ("sales", "sales"), ("account executive", "sales"),
    ("product manager", "product"), ("program manager", "product"), ("project manager", "product"),
    ("partner", "partnerships"),
    ("marketing", "marketing"), ("recruit", "recruiting_hr"),
    # Generic software roles last so specific ones win.
    ("software engineer", "software_engineering"), ("backend", "software_engineering"),
    ("frontend", "software_engineering"), ("front end", "software_engineering"),
    ("full stack", "software_engineering"), ("full-stack", "software_engineering"),
    ("web engineer", "software_engineering"), ("platform engineer", "software_engineering"),
    ("developer", "software_engineering"), ("engineer", "software_engineering"),
]


def selected_families(target_roles: list[str]) -> set[str]:
    families: set[str] = set()
    for role in target_roles or []:
        low = role.lower()
        for keyword, family in _ROLE_TO_FAMILY:
            if keyword in low:
                families.add(family)
                break
    return families


def _families_in_title(title: str) -> set[str]:
    low = f" {title.lower()} "
    hits: set[str] = set()
    for family, keywords in ROLE_FAMILIES.items():
        if any(kw in low for kw in keywords):
            hits.add(family)
    return hits


def is_role_eligible(
    title: str,
    target_roles: list[str],
    department: str | None = None,
) -> tuple[bool, str | None, str | None]:
    families = selected_families(target_roles)
    if not families:
        return True, None, None  # user gave no target roles -> no role hard filter

    title_families = _families_in_title(f"{title} {department or ''}")

    # An opt-in family the user did NOT select disqualifies the job, even if a
    # generic "engineer" also matched a selected family.
    unselected_opt_in = (title_families & _OPT_IN_FAMILIES) - families
    if unselected_opt_in:
        family = sorted(unselected_opt_in)[0].replace("_", " ")
        return False, "role:unrelated", f"“{title}” looks like a {family} role, not your target roles"

    if title_families & families:
        return True, None, None

    return False, "role:no_family", f"“{title}” does not match your target role families"


# --------------------------------------------------------------------------- #
# Top-level evaluation
# --------------------------------------------------------------------------- #
@dataclass
class EligibilityResult:
    eligible: bool
    reasons: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)
    flags: dict[str, bool | None] = field(default_factory=dict)

    @property
    def only_unknown_location(self) -> bool:
        return self.reason_codes == ["location:unknown"]


def evaluate_eligibility(
    profile: ProfileView,
    job: JobView,
    *,
    target_levels: list[str] | None = None,
    include_unknown_location: bool = False,
) -> EligibilityResult:
    reasons: list[str] = []
    codes: list[str] = []
    flags: dict[str, bool | None] = {}
    if target_levels is None:
        target_levels = profile.target_levels

    role_ok, role_code, role_reason = is_role_eligible(job.title, profile.target_roles)
    flags["role_match"] = role_ok
    if not role_ok:
        codes.append(role_code or "role:unrelated")
        reasons.append(role_reason or "Unrelated role")

    sen_ok, sen_code, sen_reason = is_seniority_eligible(
        job.title, job.description, job.seniority, target_levels or [], profile.seniority_targets
    )
    flags["seniority_match"] = sen_ok
    if not sen_ok:
        codes.append(sen_code or "seniority")
        reasons.append(sen_reason or "Seniority mismatch")

    loc_ok, loc_code, loc_reason = is_location_eligible(
        job.location, job.workplace_type, profile.preferred_locations,
        include_unknown_location=include_unknown_location,
    )
    flags["location_match"] = loc_ok
    if not loc_ok:
        codes.append(loc_code or "location")
        reasons.append(loc_reason or "Location mismatch")

    work_ok, work_reason = _workplace_ok(profile, job)
    flags["workplace_match"] = work_ok
    if not work_ok:
        codes.append("workplace:onsite")
        reasons.append(work_reason or "Workplace mismatch")

    spon_ok = _sponsorship_flag(profile, job)
    flags["sponsorship_match"] = spon_ok
    if spon_ok is False:
        codes.append("sponsorship:none")
        reasons.append("Role does not offer visa sponsorship, which you require")

    eligible = not codes
    return EligibilityResult(eligible=eligible, reasons=reasons, reason_codes=codes, flags=flags)


def _workplace_ok(profile: ProfileView, job: JobView) -> tuple[bool, str | None]:
    pref = (profile.remote_preference or "everything").lower()
    workplace = (job.workplace_type or "unknown").lower()
    if pref == "remote" and workplace == "onsite":
        return False, "You prefer remote, but this role is onsite"
    return True, None


def _sponsorship_flag(profile: ProfileView, job: JobView) -> bool | None:
    notes = (job.work_authorization_notes or "").lower()
    if profile.requires_sponsorship is None or not notes:
        return None
    if profile.requires_sponsorship and "no visa sponsorship" in notes:
        return False
    return True
