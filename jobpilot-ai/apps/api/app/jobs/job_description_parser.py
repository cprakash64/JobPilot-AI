"""Parse a job description into structured fields.

Deterministic extraction (skills, responsibilities, YOE, degree, seniority,
workplace type, sponsorship hints, salary). It never invents values: anything it
cannot find stays empty/None. The original description is preserved by the
caller for audit. An AI pass can be layered on top later; this module guarantees
discovery works without AI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# A pragmatic, extensible skill vocabulary. Matching is word-boundary based to
# avoid false positives (e.g. "R" inside "React").
SKILL_VOCAB = [
    "python", "java", "javascript", "typescript", "c++", "c#", "go", "golang", "rust", "ruby",
    "scala", "kotlin", "swift", "sql", "nosql", "bash",
    "react", "next.js", "vue", "angular", "node.js", "fastapi", "django", "flask", "spring",
    "graphql", "rest", "grpc",
    "pytorch", "tensorflow", "scikit-learn", "keras", "xgboost", "hugging face", "transformers",
    "llm", "rag", "nlp", "computer vision", "opencv", "yolo", "diffusion",
    "openai", "langchain", "llamaindex", "vector database", "embeddings",
    "mlops", "triton", "tensorrt", "onnx", "sagemaker", "kubeflow", "mlflow",
    "aws", "gcp", "azure", "docker", "kubernetes", "terraform", "ci/cd", "airflow", "spark",
    "kafka", "postgresql", "postgres", "mysql", "mongodb", "redis", "elasticsearch", "snowflake",
    "pandas", "numpy", "etl", "data pipeline",
]

DEGREE_RE = re.compile(
    r"\b(ph\.?d|master'?s|m\.?s\.?|bachelor'?s|b\.?s\.?|b\.?a\.?|mba|degree in [a-z ]+)\b",
    re.IGNORECASE,
)
YOE_RE = re.compile(r"(\d{1,2})\+?\s*(?:-\s*\d{1,2}\s*)?(?:years|yrs)", re.IGNORECASE)
SALARY_RE = re.compile(
    r"\$\s?(\d{2,3}(?:,\d{3})?|\d{2,3}[kK])\s*(?:-|to|–)\s*\$?\s?(\d{2,3}(?:,\d{3})?|\d{2,3}[kK])"
)

SENIORITY_MARKERS = [
    ("intern", ["intern", "internship"]),
    ("new grad", ["new grad", "new graduate", "entry level", "entry-level", "university grad"]),
    ("junior", ["junior", "associate", " i "]),
    ("senior", ["senior", "sr."]),
    ("staff", ["staff engineer"]),
    ("principal", ["principal"]),
    ("lead", ["lead", "manager", "director"]),
]

NO_SPONSORSHIP_MARKERS = [
    "no sponsorship", "no visa sponsorship", "not able to sponsor", "unable to sponsor",
    "without sponsorship", "do not offer sponsorship", "cannot sponsor", "not provide sponsorship",
    "not provide visa sponsorship",
]
CLEARANCE_MARKERS = ["security clearance", "ts/sci", "secret clearance", "public trust"]
CITIZENSHIP_MARKERS = ["u.s. citizen", "us citizen", "citizenship required", "must be a citizen"]

RESPONSIBILITY_HEADERS = [
    "responsibilities", "what you'll do", "what you will do", "the role", "your impact", "you will",
]
REQUIREMENT_HEADERS = [
    "requirements", "qualifications", "what you'll need", "what we're looking for",
    "minimum qualifications", "basic qualifications", "must have",
]
PREFERRED_HEADERS = [
    "preferred", "nice to have", "bonus", "preferred qualifications", "pluses",
]


@dataclass
class ParsedJob:
    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    responsibilities: list[str] = field(default_factory=list)
    years_experience_min: float | None = None
    degree_requirement: str | None = None
    seniority: str | None = None
    workplace_type: str = "unknown"
    work_authorization_notes: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    confidence: float = 0.0


def parse_job_description(
    text: str,
    title: str = "",
    location: str | None = None,
) -> ParsedJob:
    clean = _normalize(text)
    lower = clean.lower()
    title_lower = title.lower()

    required_section, preferred_section, resp_section = _split_sections(clean)

    required_skills = _skills_in(required_section or clean)
    preferred_skills = [s for s in _skills_in(preferred_section) if s not in required_skills]
    responsibilities = _bullets(resp_section)[:8]

    degree = DEGREE_RE.search(required_section or clean)
    yoe = _years(required_section or clean)
    seniority = _seniority(f"{title_lower} {lower}")
    workplace = _workplace(lower, location)
    auth_notes = _authorization(lower)
    salary_min, salary_max, currency = _salary(clean)

    parsed = ParsedJob(
        required_skills=required_skills,
        preferred_skills=preferred_skills,
        responsibilities=responsibilities,
        years_experience_min=yoe,
        degree_requirement=degree.group(0).strip() if degree else None,
        seniority=seniority,
        workplace_type=workplace,
        work_authorization_notes=auth_notes,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=currency,
    )
    parsed.confidence = _confidence(parsed, clean)
    return parsed


_HEADER_PHRASES = sorted(
    set(REQUIREMENT_HEADERS + PREFERRED_HEADERS + RESPONSIBILITY_HEADERS),
    key=len,
    reverse=True,
)


def _normalize(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = text.replace("•", "\n- ")
    text = re.sub(r"[ \t]+", " ", text).strip()
    # Break inline "Section:" markers onto their own line so single-line/HTML
    # descriptions still split into requirement/responsibility blocks.
    for phrase in _HEADER_PHRASES:
        text = re.sub(rf"(?i)(?<!\n)\s*({re.escape(phrase)})\s*:", r"\n\1:", text)
    return text.strip()


def _bucket_for(low: str) -> tuple[str, int] | None:
    """Return (bucket, header_length) if the line starts with a known header."""
    for bucket, headers in (("pref", PREFERRED_HEADERS), ("req", REQUIREMENT_HEADERS), ("resp", RESPONSIBILITY_HEADERS)):
        for header in headers:
            if low.startswith(header):
                return bucket, len(header)
    return None


def _split_sections(text: str) -> tuple[str, str, str]:
    """Best-effort split into (requirements, preferred, responsibilities) blocks."""
    buckets: dict[str, list[str]] = {"req": [], "pref": [], "resp": [], "other": []}
    current = "other"
    for line in text.splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if not low:
            continue
        match = _bucket_for(low)
        if match is not None:
            current, header_len = match
            remainder = stripped[header_len:].lstrip(" :-\t")
            if remainder:
                buckets[current].append(remainder)
            continue
        buckets[current].append(line)
    return "\n".join(buckets["req"]), "\n".join(buckets["pref"]), "\n".join(buckets["resp"])


def _skills_in(text: str) -> list[str]:
    if not text:
        return []
    lower = text.lower()
    found: list[str] = []
    for skill in SKILL_VOCAB:
        pattern = r"(?<![a-z0-9+#.])" + re.escape(skill) + r"(?![a-z0-9+#])"
        if re.search(pattern, lower):
            found.append(_display_skill(skill))
    return _dedupe(found)


def _display_skill(skill: str) -> str:
    overrides = {
        "postgres": "PostgreSQL", "postgresql": "PostgreSQL", "ci/cd": "CI/CD",
        "nlp": "NLP", "rag": "RAG", "llm": "LLM", "aws": "AWS", "gcp": "GCP",
        "sql": "SQL", "rest": "REST", "grpc": "gRPC", "etl": "ETL",
        "opencv": "OpenCV", "yolo": "YOLO", "onnx": "ONNX", "mlops": "MLOps",
        "tensorrt": "TensorRT", "next.js": "Next.js", "node.js": "Node.js",
        "c++": "C++", "c#": "C#", "openai": "OpenAI", "pytorch": "PyTorch",
        "tensorflow": "TensorFlow", "computer vision": "Computer Vision",
    }
    return overrides.get(skill, skill.title() if skill.islower() else skill)


def _bullets(text: str) -> list[str]:
    bullets = []
    for line in text.splitlines():
        stripped = re.sub(r"^\s*[-*•]\s*", "", line).strip()
        if len(stripped) > 8:
            bullets.append(stripped)
    return _dedupe(bullets)


def _years(text: str) -> float | None:
    matches = [int(m.group(1)) for m in YOE_RE.finditer(text)]
    return float(min(matches)) if matches else None


def _seniority(text: str) -> str | None:
    for level, markers in SENIORITY_MARKERS:
        if any(marker in text for marker in markers):
            return level
    return None


def _workplace(lower: str, location: str | None) -> str:
    from app.job_sources.base import normalize_workplace_type

    hint = "remote" if "remote" in lower else ("hybrid" if "hybrid" in lower else None)
    return normalize_workplace_type(hint, location)


def _authorization(lower: str) -> str | None:
    notes = []
    if any(marker in lower for marker in NO_SPONSORSHIP_MARKERS):
        notes.append("No visa sponsorship")
    if any(marker in lower for marker in CLEARANCE_MARKERS):
        notes.append("Security clearance required")
    if any(marker in lower for marker in CITIZENSHIP_MARKERS):
        notes.append("US citizenship required")
    return "; ".join(notes) or None


def _salary(text: str) -> tuple[float | None, float | None, str | None]:
    match = SALARY_RE.search(text)
    if not match:
        return None, None, None
    return _to_amount(match.group(1)), _to_amount(match.group(2)), "USD"


def _to_amount(raw: str) -> float | None:
    raw = raw.replace(",", "").lower()
    if raw.endswith("k"):
        return float(raw[:-1]) * 1000
    try:
        return float(raw)
    except ValueError:
        return None


def _confidence(parsed: ParsedJob, text: str) -> float:
    if len(text) < 40:
        return 0.2
    signals = [
        bool(parsed.required_skills),
        bool(parsed.responsibilities),
        parsed.seniority is not None,
        parsed.workplace_type != "unknown",
        parsed.years_experience_min is not None or parsed.degree_requirement is not None,
    ]
    return round(0.4 + 0.12 * sum(signals), 2)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result
