"""Deterministic, section-aware resume/LinkedIn parser.

This module turns already-extracted plain text (from a PDF, DOCX, LinkedIn
"Save to PDF", or pasted text) into the structured ``ImportProfileDraft`` shape
*without* calling an AI model. It is used as the reliable fallback when
``OPENAI_API_KEY`` is not configured, and it also powers the regression tests.

The design goal is correctness over cleverness: we split the document into
sections by detecting well-known headings, then parse each section with rules
that only apply inside that section. In particular we never treat an arbitrary
line that happens to contain a job-title keyword or a pipe character as a new
experience entry -- that was the root cause of a resume with 3 jobs producing 7
bogus experience records.
"""

from __future__ import annotations

import re
from typing import Any

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"(?:\+?1[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-]?)\d{3}[\s.\-]?\d{4}")
URL_RE = re.compile(
    r"https?://[^\s)>\]]+|(?:www\.)?[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}(?:/[^\s)>\]]*)?"
)
# City, ST  or  City, ST, Country
LOCATION_RE = re.compile(r"^([A-Za-z .'\-]+),\s*([A-Z]{2})\b(?:,\s*([A-Za-z .'\-]+))?$")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
# Date range like "Apr 2026 - Present" / "Oct 2025 – Mar 2026" / "2022 - 2025"
DATE_RANGE_RE = re.compile(
    r"((?:[A-Za-z]{3,9}\.?\s+)?\d{4}|Present|Current)\s*(?:-|–|—|to)\s*"
    r"((?:[A-Za-z]{3,9}\.?\s+)?\d{4}|Present|Current)",
    re.IGNORECASE,
)
GPA_RE = re.compile(r"gpa[:\s]*([0-9]\.[0-9]{1,2}(?:\s*/\s*[0-9]\.?[0-9]?)?)", re.IGNORECASE)
BULLET_PREFIX_RE = re.compile(r"^\s*[•▪◦‣·\-\*•▪⁃]\s+")
MINOR_RE = re.compile(r"minor(?:\s+in)?\s+([^,|;]+)", re.IGNORECASE)
DEGREE_MAJOR_RE = re.compile(r"^(.*?\b(?:in|of)\b)\s+(.+)$", re.IGNORECASE)

# Maps a normalized heading string to a canonical section name.
HEADING_MAP: dict[str, str] = {
    "professional summary": "summary",
    "summary": "summary",
    "profile": "summary",
    "about": "summary",
    "objective": "summary",
    "core skills": "skills",
    "skills": "skills",
    "technical skills": "skills",
    "core competencies": "skills",
    "areas of expertise": "skills",
    "professional experience": "experience",
    "experience": "experience",
    "work experience": "experience",
    "employment": "experience",
    "employment history": "experience",
    "selected ai projects": "projects",
    "selected projects": "projects",
    "projects": "projects",
    "key projects": "projects",
    "personal projects": "projects",
    "notable projects": "projects",
    "education": "education",
    "awards, publications & recognition": "awards",
    "awards & recognition": "awards",
    "awards": "awards",
    "honors": "awards",
    "honors & awards": "awards",
    "publications": "awards",
    "awards and honors": "awards",
    "certifications": "certifications",
    "licenses & certifications": "certifications",
    "certifications & licenses": "certifications",
    "links": "links",
    "contact": "contact",
    "contact info": "contact",
}

# Headings that legitimately appear inline as "Label: content" on a resume.
# Restricting the inline form to these avoids misreading education sub-fields
# like "Honors: Magna Cum Laude" as a new AWARDS section.
INLINE_HEADINGS = {
    "skills",
    "technical skills",
    "core skills",
    "core competencies",
    "areas of expertise",
    "links",
    "contact",
    "contact info",
}


def parse_resume_text(text: str) -> dict[str, Any]:
    """Parse extracted resume text into the import draft dict shape."""
    raw_lines = [line.rstrip() for line in text.splitlines()]
    lines = [line for line in raw_lines if line.strip()]

    header_lines, sections = _split_sections(lines)

    basic_info, links = _parse_header(header_lines)
    skills, skill_groups = _parse_skills(sections.get("skills", []))
    experience = _parse_experience(sections.get("experience", []))
    projects = _parse_projects(sections.get("projects", []))
    education = _parse_education(sections.get("education", []))
    awards = _parse_awards(sections.get("awards", []))
    certifications = _parse_certifications(sections.get("certifications", []))
    summary = " ".join(sections.get("summary", [])).strip()

    return {
        "basic_info": basic_info,
        "summary": summary,
        "job_targets": {
            "target_roles": [],
            "target_levels": [],
            "preferred_locations": [],
            "work_preference": "",
        },
        "education": education,
        "experience": experience,
        "projects": projects,
        "skills": skills,
        "skill_groups": skill_groups,
        "certifications": certifications,
        "awards": awards,
        "links": links,
        "confidence_warnings": [],
        "missing_fields": [],
    }


# --------------------------------------------------------------------------- #
# Section splitting
# --------------------------------------------------------------------------- #
def _match_heading(line: str) -> tuple[str | None, str]:
    """Return ``(section_name, inline_remainder)`` if ``line`` is a heading.

    Handles both a whole-line heading ("PROFESSIONAL EXPERIENCE") and an inline
    heading that carries content on the same line ("Skills: Python, React").
    """
    stripped = line.strip()
    normalized = re.sub(r"\s+", " ", stripped.lower()).strip().rstrip(":").strip()
    if normalized in HEADING_MAP:
        return HEADING_MAP[normalized], ""
    if ":" in stripped:
        before, after = stripped.split(":", 1)
        key = re.sub(r"\s+", " ", before.lower()).strip()
        # Only treat an inline "Label: content" line as a section switch for the
        # small set of headings that really appear that way (chiefly Skills).
        # This avoids misreading "Experience with Kafka: ..." or the education
        # sub-field "Honors: ..." as a new section.
        if key in INLINE_HEADINGS:
            return HEADING_MAP[key], after.strip()
    return None, ""


def _split_sections(lines: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    header_lines: list[str] = []
    sections: dict[str, list[str]] = {}
    current: str | None = None
    seen_heading = False
    for line in lines:
        section, inline = _match_heading(line)
        if section is not None:
            seen_heading = True
            sections.setdefault(section, [])
            if inline:
                # An inline "Label: content" heading is self-contained; the
                # content is only what follows the colon. Do not let later
                # unheaded lines fall into this section.
                sections[section].append(inline)
                current = None
            else:
                current = section
            continue
        if current is not None:
            sections[current].append(line)
        elif not seen_heading:
            header_lines.append(line)
        # Lines after an inline heading but before the next heading are dropped
        # (they belong to no clearly-labeled section).
    return header_lines, sections


# --------------------------------------------------------------------------- #
# Header
# --------------------------------------------------------------------------- #
def _parse_header(lines: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    text = "\n".join(lines)
    email = _first(EMAIL_RE, text)
    phone = _first(PHONE_RE, text)
    links = _extract_links(text)

    name = ""
    headline = ""
    city = state = country = ""

    for line in lines:
        if _is_contact_line(line):
            loc = _match_location(line)
            if loc and not state:
                city, state, country = loc
            continue
        if not name:
            name = line.strip()
            continue
        loc = _match_location(line)
        if loc:
            if not state:
                city, state, country = loc
            continue
        if not headline and _looks_like_headline(line):
            headline = line.strip()
    # A headline frequently shares the name's line region; if we still do not
    # have one, fall back to the first non-contact line after the name.
    if not headline:
        for line in lines[1:]:
            if not _is_contact_line(line) and not _match_location(line):
                headline = line.strip()
                break

    basic_info = {
        "full_name": name,
        "headline": headline,
        "phone": phone,
        "email": email,
        "location_city": city,
        "location_state": state,
        "location_country": country,
        "linkedin_url": links["linkedin_url"],
        "github_url": links["github_url"],
        "portfolio_url": links["portfolio_url"],
        "work_authorization_status": "",
        "requires_sponsorship": None,
    }
    return basic_info, links


def _is_contact_line(line: str) -> bool:
    return bool(EMAIL_RE.search(line) or PHONE_RE.search(line) or URL_RE.search(line))


def _looks_like_headline(line: str) -> bool:
    # A headline is a short-ish descriptive line, often pipe-separated role
    # keywords. Avoid picking up long summary sentences.
    if "|" in line:
        return True
    words = line.split()
    return 1 < len(words) <= 14 and not line.endswith(".")


def _match_location(line: str) -> tuple[str, str, str] | None:
    match = LOCATION_RE.match(line.strip())
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip(), (match.group(3) or "").strip()


# --------------------------------------------------------------------------- #
# Skills
# --------------------------------------------------------------------------- #
def _parse_skills(lines: list[str]) -> tuple[list[str], list[dict[str, Any]]]:
    groups: list[dict[str, Any]] = []
    flat: list[str] = []
    for line in lines:
        stripped = _strip_bullet(line)
        if not stripped:
            continue
        if ":" in stripped:
            category, rest = stripped.split(":", 1)
            items = _split_items(rest)
            category = category.strip()
        else:
            category = ""
            items = _split_items(stripped)
        if not items:
            continue
        groups.append({"category": category, "items": items})
        flat.extend(items)
    return _unique(flat), groups


# --------------------------------------------------------------------------- #
# Experience
# --------------------------------------------------------------------------- #
def _parse_experience(lines: list[str]) -> list[dict[str, Any]]:
    """Parse experience entries.

    An entry header is a line that carries structured metadata: either a
    pipe-separated ``Title | Company | Dates`` line, or a line that contains a
    recognizable date range. Everything else becomes a bullet on the current
    entry. This deliberately refuses to start a new entry from a plain bullet or
    a lone job-title word.
    """
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in lines:
        bulleted = bool(BULLET_PREFIX_RE.match(line))
        stripped = _strip_bullet(line)
        if not bulleted and _is_experience_header(stripped):
            current = _parse_experience_header(stripped)
            records.append(current)
            continue
        if current is None:
            # Content before any header -> synthesize an entry from the first
            # line so we do not silently drop text.
            current = _parse_experience_header(stripped)
            records.append(current)
            continue
        if stripped:
            current["bullets"].append(stripped)
    return records


def _is_experience_header(line: str) -> bool:
    has_range = bool(DATE_RANGE_RE.search(line))
    has_pipe = "|" in line
    # Require real structure: a pipe layout, or a date range on a short-ish line
    # (headers are rarely long prose sentences).
    if has_pipe and (has_range or line.count("|") >= 2):
        return True
    if has_range and len(line) <= 120 and not line.endswith("."):
        return True
    return False


def _parse_experience_header(line: str) -> dict[str, Any]:
    title = company = location = ""
    start_date = end_date = ""
    currently_working = False

    date_match = DATE_RANGE_RE.search(line)
    date_segment = ""
    if date_match:
        date_segment = date_match.group(0)
        start_date = _normalize_date(date_match.group(1))
        end_raw = date_match.group(2)
        if re.search(r"present|current", end_raw, re.IGNORECASE):
            currently_working = True
            end_date = ""
        else:
            end_date = _normalize_date(end_raw)

    working = line.replace(date_segment, "").strip(" |-–—")
    parts = [part.strip() for part in working.split("|") if part.strip()]
    if parts:
        title = parts[0]
    if len(parts) >= 2:
        company, location = _split_company_location(parts[1])
    if len(parts) >= 3 and not location:
        location = parts[2]

    return {
        "company": company,
        "title": title,
        "location": location,
        "start_date": start_date,
        "end_date": end_date,
        "currently_working": currently_working,
        "bullets": [],
        "technologies": [],
        "measurable_impact": [],
    }


def _split_company_location(value: str) -> tuple[str, str]:
    # "VeoTrex - Phoenix, AZ" / "Cardinal Health – AZ"
    parts = re.split(r"\s[-–—]\s", value, maxsplit=1)
    company = parts[0].strip()
    location = parts[1].strip() if len(parts) > 1 else ""
    return company, location


# --------------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------------- #
def _parse_projects(lines: list[str]) -> list[dict[str, Any]]:
    """Parse projects.

    A new project starts on a non-bulleted line that follows a bullet (or is the
    first line). Consecutive non-bulleted lines after a title are treated as a
    subtitle/description so a two-line project header does not spawn two
    projects.
    """
    projects: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    prev_was_bullet = True
    for line in lines:
        bulleted = bool(BULLET_PREFIX_RE.match(line))
        stripped = _strip_bullet(line)
        if not stripped:
            continue
        if bulleted:
            if current is None:
                current = _new_project("")
                projects.append(current)
            current["bullets"].append(stripped)
            prev_was_bullet = True
            continue
        if current is None or prev_was_bullet:
            current = _new_project(stripped)
            projects.append(current)
        elif not current["subtitle"]:
            current["subtitle"] = stripped
        else:
            current["description"] = (current["description"] + " " + stripped).strip()
        current["links"] = _unique(current["links"] + _extract_urls(stripped))
        prev_was_bullet = False
    return projects


def _new_project(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "subtitle": "",
        "description": "",
        "bullets": [],
        "technologies": [],
        "links": _extract_urls(name),
        "start_date": "",
        "end_date": "",
    }


# --------------------------------------------------------------------------- #
# Education
# --------------------------------------------------------------------------- #
EDU_KEYWORDS = ("university", "college", "institute", "school", "academy", "polytechnic")


def _parse_education(lines: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in lines:
        stripped = _strip_bullet(line)
        low = stripped.lower()
        if current is not None and GPA_RE.search(stripped):
            current["gpa"] = GPA_RE.search(stripped).group(1).replace(" ", "")
            continue
        if current is not None and low.startswith(("honors", "honours")):
            current["honors"] = _unique(current["honors"] + _split_items(_after_colon(stripped)))
            continue
        if current is not None and low.startswith(("coursework", "relevant coursework")):
            current["coursework"] = _unique(
                current["coursework"] + _split_items(_after_colon(stripped))
            )
            continue
        if any(keyword in low for keyword in EDU_KEYWORDS) or current is None:
            current = _parse_education_header(stripped)
            records.append(current)
        else:
            # Continuation line (e.g. degree on its own line).
            if not current["degree"]:
                current["degree"] = stripped
    return records


def _parse_education_header(line: str) -> dict[str, Any]:
    record = {
        "school": "",
        "degree": "",
        "major": "",
        "minor": "",
        "start_date": "",
        "end_date": "",
        "gpa": "",
        "honors": [],
        "coursework": [],
    }
    working = line
    date_match = DATE_RANGE_RE.search(working)
    if date_match:
        record["start_date"] = _normalize_date(date_match.group(1))
        end_raw = date_match.group(2)
        record["end_date"] = "" if re.search(r"present|current", end_raw, re.IGNORECASE) else _normalize_date(end_raw)
        working = working.replace(date_match.group(0), "")
    working = working.strip(" |-–—")

    parts = re.split(r"\s[-–—]\s|\s\|\s", working, maxsplit=1)
    record["school"] = parts[0].strip()
    remainder = parts[1].strip() if len(parts) > 1 else ""

    minor_match = MINOR_RE.search(remainder)
    if minor_match:
        record["minor"] = minor_match.group(1).strip()
        remainder = MINOR_RE.sub("", remainder)
    remainder = remainder.strip(" ,;|")
    if remainder:
        record["degree"] = remainder
        major_match = DEGREE_MAJOR_RE.match(remainder)
        if major_match:
            record["major"] = major_match.group(2).strip(" ,;")
    return record


# --------------------------------------------------------------------------- #
# Awards & certifications
# --------------------------------------------------------------------------- #
def _parse_awards(lines: list[str]) -> list[dict[str, Any]]:
    awards: list[dict[str, Any]] = []
    for line in lines:
        stripped = _strip_bullet(line)
        if not stripped:
            continue
        year_match = YEAR_RE.search(stripped)
        awards.append(
            {
                "name": stripped,
                "issuer": "",
                "date": year_match.group(0) if year_match else "",
                "description": "",
            }
        )
    return awards


def _parse_certifications(lines: list[str]) -> list[dict[str, Any]]:
    certs: list[dict[str, Any]] = []
    for line in lines:
        stripped = _strip_bullet(line)
        if not stripped:
            continue
        certs.append(
            {
                "name": stripped,
                "issuer": "",
                "issue_date": "",
                "expiration_date": "",
                "credential_url": "",
            }
        )
    return certs


# --------------------------------------------------------------------------- #
# Links & shared helpers
# --------------------------------------------------------------------------- #
def _extract_links(text: str) -> dict[str, Any]:
    urls = _extract_urls(text)
    linkedin = next((url for url in urls if "linkedin.com/in/" in url.lower()), "")
    if not linkedin:
        linkedin = next((url for url in urls if "linkedin.com" in url.lower()), "")
    github = next((url for url in urls if "github.com/" in url.lower()), "")
    portfolio = next(
        (
            url
            for url in urls
            if url not in {linkedin, github} and "linkedin.com" not in url.lower()
        ),
        "",
    )
    other = [url for url in urls if url not in {linkedin, github, portfolio}]
    return {
        "linkedin_url": linkedin,
        "github_url": github,
        "portfolio_url": portfolio,
        "other_links": other,
    }


def _extract_urls(text: str) -> list[str]:
    # Remove email addresses first so an email local part like
    # "cprakash.work@gmail.com" cannot be mistaken for the domain "cprakash.work".
    cleaned = EMAIL_RE.sub(" ", text)
    urls: list[str] = []
    for match in URL_RE.finditer(cleaned):
        raw = match.group(0)
        if "@" in raw:
            continue
        urls.append(_normalize_url(raw))
    return _unique(urls)


def _normalize_url(value: str) -> str:
    url = value.strip().rstrip(".,;)")
    if not url.lower().startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


def _normalize_date(value: str) -> str:
    """Normalize "Apr 2026" / "2022" to a consistent string; never guess."""
    value = value.strip()
    if re.search(r"present|current", value, re.IGNORECASE):
        return ""
    return re.sub(r"\s+", " ", value)


def _strip_bullet(line: str) -> str:
    return BULLET_PREFIX_RE.sub("", line).strip()


def _after_colon(line: str) -> str:
    return line.split(":", 1)[1].strip() if ":" in line else line


def _split_items(value: str) -> list[str]:
    pieces = re.split(r"[,;|••]| and (?=[A-Z])", value)
    return _unique(piece.strip(" \t-*·") for piece in pieces)


def _first(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group(0).strip() if match else ""


def _unique(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value).strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
