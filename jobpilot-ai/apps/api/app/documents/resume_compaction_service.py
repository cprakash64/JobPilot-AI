"""One-page resume compaction and de-duplication.

Profiles imported from messy resumes/LinkedIn PDFs often carry duplicated
experience, projects, and awards (or bullet fragments that leaked in as project
"names"). This service collapses those into a strong, one-page ATS resume:

- dedupe experience by normalized company + title + dates
- dedupe projects by normalized name (dropping bullet-fragment "names")
- dedupe awards/certifications by normalized title
- dedupe bullets by normalized text and drop empty/malformed ones
- cap sections and bullets to one-page limits
- keep the strongest, most job-relevant items first

It is deterministic and side-effect free: it takes the "working" resume dict
(the shape produced by ``resume_generation_service._build_resume``) and a set of
job skills, and returns a new compacted dict. It also exposes ``group_skills``
so the generator can present categorized Core Skills instead of a comma dump.
"""

from __future__ import annotations

import re
from typing import Any

# One-page limits. Tuned for junior/new-grad resumes: enough to show depth
# without spilling onto a second page.
MAX_EXPERIENCE = 3
MAX_BULLETS_PER_EXPERIENCE = 3
MAX_PROJECTS = 2
MAX_BULLETS_PER_PROJECT = 2
MAX_EDUCATION = 2
MAX_AWARDS = 4
MAX_SKILL_CATEGORIES = 6
MAX_SKILLS_PER_CATEGORY = 8

_WORD_RE = re.compile(r"[a-z0-9]+")


def compact_resume(
    working: dict[str, Any],
    job_skills: set[str],
    job_keywords: set[str] | None = None,
) -> dict[str, Any]:
    """Return a de-duplicated, one-page version of ``working``.

    ``job_skills`` (lowercased) ranks experience by relevance. ``job_keywords``
    (a broader set: job skills + title/JD tokens) ranks project relevance so the
    strongest job-relevant projects survive the caps. Education is deduped by a
    canonical school/degree/major key so near-duplicate entries collapse to one.
    Projects are never dropped just to hit one page — bullets shrink first.
    """
    keywords = job_keywords or job_skills
    result = dict(working)
    result["experience"] = _compact_experience(working.get("experience") or [], keywords)
    result["projects"] = select_relevant_projects(
        working.get("projects") or [], keywords, max_projects=MAX_PROJECTS, max_bullets=MAX_BULLETS_PER_PROJECT
    )
    result["education"] = dedupe_education(working.get("education") or [])[:MAX_EDUCATION]
    result["awards"] = _dedupe_by_key(working.get("awards") or [], _named_key)[:MAX_AWARDS]
    result["certifications"] = _dedupe_by_key(working.get("certifications") or [], _named_key)[:MAX_AWARDS]
    return result


# --------------------------------------------------------------------------- #
# Experience
# --------------------------------------------------------------------------- #
def _compact_experience(entries: list[dict], job_skills: set[str]) -> list[dict]:
    deduped = _dedupe_by_key(entries, _experience_key)
    cleaned: list[dict] = []
    for entry in deduped:
        item = dict(entry)
        item["bullets"] = _compact_bullets(
            entry.get("bullets") or [], job_skills, MAX_BULLETS_PER_EXPERIENCE
        )
        cleaned.append(item)
    # Newest-first is already the profile order; keep it stable, just cap count.
    return cleaned[:MAX_EXPERIENCE]


def _experience_key(entry: dict) -> str:
    return _norm(
        " ".join(
            str(entry.get(field) or "")
            for field in ("company", "title", "start_date", "end_date")
        )
    )


# --------------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------------- #
def dedupe_projects(projects: list[dict]) -> list[dict]:
    """Collapse duplicate project records by normalized name."""
    return _dedupe_by_key([p for p in projects if isinstance(p, dict)], _named_key)


def select_relevant_projects(
    projects: list[dict],
    job_keywords: set[str],
    *,
    max_projects: int = MAX_PROJECTS,
    max_bullets: int = MAX_BULLETS_PER_PROJECT,
) -> list[dict]:
    """Pick the top ``max_projects`` real, job-relevant projects.

    Drops bullet-fragment "names" and duplicates, ranks the rest by relevance to
    the job (name, tech stack, and bullet-keyword overlap), and caps each project
    to ``max_bullets`` strong bullets. Projects are kept even when their bullets
    are weak — for junior/new-grad/software/AI resumes a relevant project is more
    valuable than an empty slot, so we shrink bullets rather than drop projects.
    """
    real = [
        entry
        for entry in projects
        if isinstance(entry, dict) and not looks_like_fragment(str(entry.get("name") or ""))
    ]
    deduped = dedupe_projects(real)
    ranked = sorted(deduped, key=lambda p: _project_relevance(p, job_keywords), reverse=True)
    selected: list[dict] = []
    for entry in ranked[:max_projects]:
        item = dict(entry)
        item["bullets"] = _compact_bullets(entry.get("bullets") or [], job_keywords, max_bullets)
        selected.append(item)
    return selected


def _project_relevance(project: dict, job_keywords: set[str]) -> int:
    tech = {str(t).lower() for t in project.get("technologies") or []}
    name = _norm(str(project.get("name") or ""))
    text = (name + " " + " ".join(str(b) for b in project.get("bullets") or [])).lower()
    return len(tech & job_keywords) + sum(1 for keyword in job_keywords if keyword and keyword in text)


def looks_like_fragment(name: str) -> bool:
    """True when a project "name" is really a leaked bullet fragment.

    Real project names are short proper nouns, often with internal capitals
    ("LunaiCAD", "VeoTrex", "SourceCAD AI Part Studio", "Luna AI"). Parser noise
    looks like a sentence fragment: trailing punctuation, very long, or an
    all-lowercase multi-word phrase (e.g. "validation states.",
    "timestamped video results."). We stay conservative so real project names
    are never mistaken for fragments and dropped.
    """
    text = name.strip()
    if not text:
        return True
    if text.endswith((".", ",", ";", ":")):
        return True
    words = text.split()
    if len(words) > 8:
        return True
    # An all-lowercase phrase of several words reads like prose, not a name.
    # Any internal capital (real product names) keeps it out of this bucket.
    if len(words) >= 4 and text == text.lower():
        return True
    return False


# --------------------------------------------------------------------------- #
# Education
# --------------------------------------------------------------------------- #
# School-name aliases so common variants collapse to one canonical form.
_SCHOOL_ALIASES = {
    "asu": "arizona state university",
    "arizona state": "arizona state university",
    "arizona state univ": "arizona state university",
}

# Degree level keywords -> canonical level, so "B.S.", "BS", and
# "Bachelor of Science" are treated as the same degree.
_DEGREE_LEVELS = (
    ("phd", "doctorate"),
    ("ph d", "doctorate"),
    ("doctor", "doctorate"),
    ("mba", "master"),
    ("master", "master"),
    ("m s", "master"),
    ("m eng", "master"),
    ("bachelor", "bachelor"),
    ("b s", "bachelor"),
    ("b a", "bachelor"),
    ("b eng", "bachelor"),
    ("bsc", "bachelor"),
    ("associate", "associate"),
    ("a s", "associate"),
)


def dedupe_education(education: list[dict]) -> list[dict]:
    """Collapse duplicate education entries.

    Two entries are the same when they share a canonical school and their
    degrees/majors are compatible (equal, or one side is blank). The more
    complete record wins, and blank fields are filled from its duplicate — so
    "Arizona State University" appears only once even if a bad import saved it
    twice with slightly different completeness.
    """
    kept: list[dict] = []
    for entry in education:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        if not _norm(str(entry.get("school") or "")):
            continue  # a school-less education row is malformed noise
        match = next((i for i, existing in enumerate(kept) if _same_education(entry, existing)), None)
        if match is None:
            kept.append(entry)
        else:
            kept[match] = _merge_education(kept[match], entry)
    return kept


def _same_education(a: dict, b: dict) -> bool:
    if _canonical_school(a.get("school")) != _canonical_school(b.get("school")):
        return False
    da, db = _canonical_degree(a.get("degree")), _canonical_degree(b.get("degree"))
    if da and db and da != db:
        return False
    ma, mb = _norm(str(a.get("major") or "")), _norm(str(b.get("major") or ""))
    if ma and mb and ma != mb:
        return False
    return True


def _merge_education(a: dict, b: dict) -> dict:
    base, other = (a, b) if _edu_completeness(a) >= _edu_completeness(b) else (b, a)
    merged = dict(base)
    for key, value in other.items():
        if not str(merged.get(key) or "").strip() and str(value or "").strip():
            merged[key] = value
    return merged


def _edu_completeness(entry: dict) -> int:
    score = sum(
        1 for field in ("degree", "major", "minor", "gpa", "start_date", "end_date")
        if str(entry.get(field) or "").strip()
    )
    if entry.get("honors"):
        score += 1
    return score


def _canonical_school(name: Any) -> str:
    norm = _norm(str(name or ""))
    return _SCHOOL_ALIASES.get(norm, norm)


def _canonical_degree(degree: Any) -> str:
    norm = _norm(str(degree or ""))
    if not norm:
        return ""
    for keyword, canonical in _DEGREE_LEVELS:
        if keyword in norm:
            return canonical
    return norm


# --------------------------------------------------------------------------- #
# Bullets
# --------------------------------------------------------------------------- #
def _compact_bullets(bullets: list[Any], job_skills: set[str], cap: int) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in bullets:
        bullet = _clean_bullet(raw)
        if not bullet:
            continue
        key = _norm(bullet)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(bullet)
    if len(cleaned) <= cap:
        return cleaned
    # Over the cap: keep the strongest job-relevant bullets first, preserving
    # their original order among equally-relevant bullets.
    ordered = sorted(
        enumerate(cleaned),
        key=lambda pair: (-_bullet_relevance(pair[1], job_skills), pair[0]),
    )
    kept = sorted(ordered[:cap], key=lambda pair: pair[0])
    return [bullet for _, bullet in kept]


def _clean_bullet(raw: Any) -> str:
    text = str(raw or "").strip()
    # Strip a leading bullet glyph/dash the parser may have left behind.
    text = re.sub(r"^[\-•\*▪●\s]+", "", text).strip()
    # Malformed if there is no real word content or it is a bare fragment.
    if len(text) < 3 or not _WORD_RE.search(text.lower()):
        return ""
    return text


def _bullet_relevance(bullet: str, job_skills: set[str]) -> int:
    low = bullet.lower()
    score = sum(1 for skill in job_skills if skill and skill in low)
    if re.search(r"\d", bullet):  # quantified impact is stronger
        score += 1
    return score


# --------------------------------------------------------------------------- #
# Skill grouping
# --------------------------------------------------------------------------- #
# Ordered category rules: (label, matcher keywords). First matching category
# wins for a given skill. "Programming" languages are matched by an exact,
# case-insensitive token list to avoid mis-bucketing (e.g. "Go", "R").
_SKILL_CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    (
        "ML / AI",
        (
            "pytorch", "tensorflow", "keras", "hugging face", "huggingface", "transformer",
            "rag", "embedding", "yolo", "opencv", "scikit", "sklearn", "nlp", "llm",
            "computer vision", "deep learning", "machine learning", "neural", "pandas",
            "numpy", "diffusion", "langchain", "spacy", "vector",
        ),
    ),
    (
        "Backend / Cloud",
        (
            "fastapi", "flask", "django", "express", "rest", "graphql", "grpc", "api",
            "postgres", "mysql", "sqlite", "mongo", "redis", "docker", "kubernetes", "k8s",
            "aws", "gcp", "azure", "s3", "lambda", "kafka", "rabbitmq", "nginx", "node",
            "microservice", "serverless", "supabase", "firebase",
        ),
    ),
    (
        "MLOps / Production",
        (
            "ci/cd", "cicd", "github actions", "gitlab ci", "jenkins", "terraform", "ansible",
            "monitoring", "prometheus", "grafana", "mlflow", "airflow", "kubeflow",
            "model evaluation", "latency", "health check", "observability", "sagemaker",
        ),
    ),
    (
        "Programming",
        (
            "python", "typescript", "javascript", "go", "golang", "java", "c++", "c#",
            "sql", "bash", "shell", "rust", "ruby", "scala", "kotlin", "swift", "php",
            "r", "matlab", "perl", "html", "css",
        ),
    ),
]

_PROGRAMMING_TOKENS = {
    "python", "typescript", "javascript", "go", "golang", "java", "c++", "c#", "sql",
    "bash", "shell", "rust", "ruby", "scala", "kotlin", "swift", "php", "r", "matlab",
}

_TOOLS_LABEL = "Tools & Practices"


def group_skills(skills: list[Any], job_skills: set[str]) -> list[dict[str, Any]]:
    """Group a flat skill list into ordered categories for the resume.

    - Categories with a job-relevant skill are listed first.
    - Within a category, job-relevant skills come first.
    - Only the user's real skills are grouped; nothing is invented.
    """
    items = _dedupe_strings(str(skill) for skill in skills if str(skill).strip())
    if not items:
        return []

    buckets: dict[str, list[str]] = {}
    order: list[str] = []
    for skill in items:
        label = _categorize_skill(skill)
        if label not in buckets:
            buckets[label] = []
            order.append(label)
        buckets[label].append(skill)

    groups: list[dict[str, Any]] = []
    for label in order:
        entries = buckets[label]
        entries = sorted(entries, key=lambda s: (s.lower() not in job_skills,))
        groups.append({"category": label, "items": entries[:MAX_SKILLS_PER_CATEGORY]})

    # Order categories: those containing a job-relevant skill first, then by the
    # canonical category order, keeping "Tools & Practices" last.
    canonical = [label for label, _ in _SKILL_CATEGORIES] + [_TOOLS_LABEL]

    def sort_key(group: dict[str, Any]) -> tuple[int, int]:
        has_relevant = any(item.lower() in job_skills for item in group["items"])
        rank = canonical.index(group["category"]) if group["category"] in canonical else len(canonical)
        return (0 if has_relevant else 1, rank)

    groups.sort(key=sort_key)
    return groups[:MAX_SKILL_CATEGORIES]


def _categorize_skill(skill: str) -> str:
    low = skill.strip().lower()
    if low in _PROGRAMMING_TOKENS:
        return "Programming"
    for label, keywords in _SKILL_CATEGORIES:
        if any(keyword in low for keyword in keywords):
            return label
    return _TOOLS_LABEL


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _dedupe_by_key(entries: list[Any], key_fn) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = key_fn(entry)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(entry)
    return result


def _dedupe_strings(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value).strip()
        norm = _norm(item)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        result.append(item)
    return result


def _named_key(entry: dict) -> str:
    return _norm(str(entry.get("name") or ""))


def _norm(text: str) -> str:
    return " ".join(_WORD_RE.findall(text.lower()))
