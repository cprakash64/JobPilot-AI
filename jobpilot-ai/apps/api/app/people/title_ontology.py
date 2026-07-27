from __future__ import annotations

import re
from dataclasses import dataclass

TITLE_ONTOLOGY_VERSION = "people-title-v2"

_PHRASE_EQUIVALENTS = {
    "artificial intelligence": "ai",
    "machine learning": "ml",
    "software development": "software engineering",
    "software developer": "software engineer",
    "talent acquisition": "recruiting",
    "talent partner": "recruiter",
    "campus": "early career",
    "university": "early career",
    "emerging talent": "early career",
    "new graduate": "early career",
    "new grad": "early career",
    "agentic ai": "applied ai",
    "ai platform": "applied ai",
}

_TOKEN_EQUIVALENTS = {
    "sr": "senior",
    "mgr": "manager",
    "dir": "director",
    "dev": "engineer",
    "developers": "engineer",
    "engineers": "engineer",
    "recruiters": "recruiter",
    "managers": "manager",
}

_EARLY_CAREER_MARKERS = (
    "intern",
    "internship",
    "new grad",
    "new graduate",
    "graduate program",
    "early career",
    "entry level",
)

RECRUITER_CORE_TITLES = [
    "Technical Recruiter",
    "Engineering Recruiter",
    "Software Recruiter",
    "AI Recruiter",
    "Technology Recruiter",
]
RECRUITER_BROAD_TITLES = [
    "Talent Acquisition Partner",
    "Talent Partner",
    "Senior Technical Recruiter",
    "Recruiting Manager",
]
RECRUITER_EARLY_CAREER_TITLES = [
    "University Recruiter",
    "Campus Recruiter",
    "Early Careers Recruiter",
    "University Talent Acquisition",
    "Emerging Talent Recruiter",
    "Campus Talent Partner",
    "University Programs Recruiter",
]

_MANAGER_TITLES = {
    "software_engineering": [
        "Software Engineering Manager",
        "Engineering Manager",
        "Software Development Manager",
        "Director of Software Engineering",
        "Director of Engineering",
        "Head of Engineering",
        "Engineering Lead",
    ],
    "machine_learning": [
        "AI Engineering Manager",
        "Machine Learning Engineering Manager",
        "Applied AI Manager",
        "Director of AI",
        "Director of Machine Learning",
        "Head of Engineering",
        "Engineering Lead",
    ],
    "embedded_systems": [
        "Embedded Software Manager",
        "Firmware Engineering Manager",
        "Embedded Systems Manager",
        "Director of Embedded Engineering",
        "Director of Engineering",
    ],
    "product": [
        "Product Manager",
        "Group Product Manager",
        "Director of Product",
        "Head of Product",
        "VP of Product",
    ],
    "finance": [
        "Finance Manager",
        "Accounting Manager",
        "Director of Finance",
        "Head of Finance",
        "VP of Finance",
    ],
    "healthcare": [
        "Clinical Engineering Manager",
        "Healthcare Technology Manager",
        "Director of Clinical Engineering",
        "Director of Engineering",
    ],
}

_TEAM_TITLES = {
    "software_engineering": ["Software Engineer", "Software Developer", "Platform Engineer"],
    "machine_learning": ["AI Engineer", "Machine Learning Engineer", "Applied Scientist"],
    "embedded_systems": [
        "Embedded Software Engineer",
        "Firmware Engineer",
        "Embedded Systems Engineer",
    ],
    "product": ["Product Manager", "Product Owner", "Product Analyst"],
    "finance": ["Financial Analyst", "Accountant", "Finance Business Partner"],
    "healthcare": ["Clinical Engineer", "Healthcare Systems Engineer", "Biomedical Engineer"],
}


@dataclass(frozen=True)
class TitleGroup:
    name: str
    titles: list[str]
    seniorities: list[str]


def normalize_title(value: str | None) -> str:
    normalized = re.sub(r"[^a-z0-9+]+", " ", (value or "").lower()).strip()
    for source, target in sorted(_PHRASE_EQUIVALENTS.items(), key=lambda item: -len(item[0])):
        normalized = re.sub(rf"\b{re.escape(source)}\b", target, normalized)
    tokens = [_TOKEN_EQUIVALENTS.get(token, token) for token in normalized.split()]
    return " ".join(tokens)


def title_similarity(value: str | None, choices: list[str]) -> float:
    left = set(normalize_title(value).split())
    if not left:
        return 0
    best = 0.0
    for choice in choices:
        right = set(normalize_title(choice).split())
        if not right:
            continue
        overlap = len(left & right)
        containment = overlap / max(1, len(right))
        jaccard = overlap / max(1, len(left | right))
        best = max(best, 0.55 * containment + 0.45 * jaccard)
    return round(min(1.0, best), 4)


def is_early_career_job(title: str, description: str = "") -> bool:
    haystack = f"{title} {description[:2000]}".lower()
    return any(marker in haystack for marker in _EARLY_CAREER_MARKERS)


def recruiter_title_groups(*, early_career: bool) -> list[TitleGroup]:
    groups = [
        TitleGroup("specialist", RECRUITER_CORE_TITLES, []),
        TitleGroup("broad", RECRUITER_BROAD_TITLES, []),
    ]
    if early_career:
        groups.append(TitleGroup("early_career", RECRUITER_EARLY_CAREER_TITLES, []))
    return groups


def manager_title_groups(role_family: str | None, base_title: str) -> list[TitleGroup]:
    titles = _MANAGER_TITLES.get(
        role_family or "",
        [f"{base_title} Manager", "Department Manager", "Director", "Head"],
    )
    return [
        TitleGroup("manager", titles[:4], ["manager"]),
        TitleGroup("leadership", titles[4:] or titles[-2:], ["director", "head", "vp"]),
    ]


def team_titles(role_family: str | None, job_title: str) -> list[str]:
    configured = _TEAM_TITLES.get(role_family or "", [])
    return list(dict.fromkeys([job_title, *configured]))
