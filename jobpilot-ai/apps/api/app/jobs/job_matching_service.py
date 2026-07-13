"""Explainable profile-to-job fit scoring.

Deterministic weighted scoring (no randomness):
    30% role/title alignment
    25% required-skills match
    15% experience/project relevance
    10% seniority/level match
    10% location/remote preference match
     5% education/certification alignment
     5% work-authorization / sponsorship compatibility

Output is a MatchResult with a 0-100 score, a label, short reasons, missing
skills, risk factors, a resume angle, and a confidence value. AI is optional and
only used to polish the textual explanation; the numeric score is always
deterministic so matching works without OpenAI.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.jobs.job_search_criteria_service import SENIORITY_ORDER, SearchCriteria

WEIGHTS = {
    "role": 30.0,
    "skills": 25.0,
    "experience": 15.0,
    "seniority": 10.0,
    "location": 10.0,
    "education": 5.0,
    "authorization": 5.0,
}


@dataclass
class ProfileView:
    """A source-agnostic view of the user's profile used for scoring."""

    target_roles: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    experience_titles: list[str] = field(default_factory=list)
    project_terms: list[str] = field(default_factory=list)
    preferred_locations: list[str] = field(default_factory=list)
    remote_preference: str = "everything"
    seniority_targets: list[str] = field(default_factory=list)
    target_levels: list[str] = field(default_factory=list)
    has_degree: bool = False
    requires_sponsorship: bool | None = None


@dataclass
class JobView:
    title: str = ""
    description: str = ""
    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    location: str | None = None
    workplace_type: str = "unknown"
    seniority: str | None = None
    work_authorization_notes: str | None = None


@dataclass
class MatchResult:
    fit_score: int
    fit_label: str
    match_reasons: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    risk_factors: list[str] = field(default_factory=list)
    recommended_resume_angle: str = ""
    confidence: float = 0.0


def score_job(profile: ProfileView, job: JobView, criteria: SearchCriteria | None = None) -> MatchResult:
    reasons: list[str] = []
    risks: list[str] = []

    role_score, role_reason = _role_alignment(profile, job, criteria)
    skills_score, matched_skills, missing = _skills_match(profile, job)
    experience_score, exp_reason = _experience_relevance(profile, job)
    seniority_score, seniority_risk = _seniority_match(profile, job)
    location_score, location_risk = _location_match(profile, job)
    education_score = _education_match(profile, job)
    auth_score, auth_risk = _authorization_match(profile, job)

    raw = (
        role_score * WEIGHTS["role"]
        + skills_score * WEIGHTS["skills"]
        + experience_score * WEIGHTS["experience"]
        + seniority_score * WEIGHTS["seniority"]
        + location_score * WEIGHTS["location"]
        + education_score * WEIGHTS["education"]
        + auth_score * WEIGHTS["authorization"]
    )
    fit = int(round(raw))

    # Guardrails: don't rank highly when the essentials clearly fail.
    if job.required_skills and skills_score < 0.34:
        fit = min(fit, 45)
    if location_risk:
        fit = min(fit, 55)
    if seniority_risk:
        fit = min(fit, 60)

    if role_reason:
        reasons.append(role_reason)
    if matched_skills:
        reasons.append(f"Matches skills: {', '.join(matched_skills[:4])}")
    if exp_reason:
        reasons.append(exp_reason)
    for risk in (seniority_risk, location_risk, auth_risk):
        if risk:
            risks.append(risk)

    fit = max(0, min(100, fit))
    result = MatchResult(
        fit_score=fit,
        fit_label=_label(fit),
        match_reasons=reasons[:3] or ["Some overlap with your profile"],
        missing_skills=missing[:6],
        risk_factors=risks,
        recommended_resume_angle=_resume_angle(matched_skills, job),
        confidence=_confidence(profile, job),
    )
    return result


def _role_alignment(profile: ProfileView, job: JobView, criteria: SearchCriteria | None) -> tuple[float, str]:
    title = job.title.lower()
    queries = [role.lower() for role in profile.target_roles]
    if criteria:
        queries += [role.lower() for role in criteria.role_queries]
    queries = list(dict.fromkeys(queries))
    for role in queries:
        if role and role in title:
            return 1.0, f"Title aligns with your target role “{job.title}”"
    # Partial: significant token overlap between a target role and the title.
    best = 0.0
    for role in queries:
        overlap = _token_overlap(role, title)
        best = max(best, overlap)
    if best >= 0.5:
        return 0.6, f"Title is related to your target roles"
    if best > 0:
        return 0.3, ""
    return 0.0, ""


def _skills_match(profile: ProfileView, job: JobView) -> tuple[float, list[str], list[str]]:
    user = {s.lower(): s for s in profile.skills}
    required = [s for s in job.required_skills]
    if not required and job.preferred_skills:
        required = job.preferred_skills
    if not required:
        return 0.5, [], []  # neutral when the job lists no parsable skills
    matched = [s for s in required if s.lower() in user]
    missing = [s for s in required if s.lower() not in user]
    ratio = len(matched) / len(required)
    return ratio, matched, missing


def _experience_relevance(profile: ProfileView, job: JobView) -> tuple[float, str]:
    haystack = f"{job.title} {job.description}".lower()
    terms = [t.lower() for t in (profile.experience_titles + profile.project_terms) if t]
    hits = [t for t in terms if t and t in haystack]
    if hits:
        return min(1.0, 0.5 + 0.25 * len(hits)), "Your past experience/projects are relevant"
    # Fall back to skills appearing in the description as weak experience signal.
    skill_hits = [s for s in profile.skills if s.lower() in haystack]
    if skill_hits:
        return 0.5, ""
    return 0.0, ""


def _seniority_match(profile: ProfileView, job: JobView) -> tuple[float, str | None]:
    if not job.seniority or not profile.seniority_targets:
        return 0.6, None  # neutral when unknown
    job_rank = _rank(job.seniority)
    target_ranks = [_rank(t) for t in profile.seniority_targets if _rank(t) is not None]
    if job_rank is None or not target_ranks:
        return 0.6, None
    lowest, highest = min(target_ranks), max(target_ranks)
    if lowest <= job_rank <= highest:
        return 1.0, None
    gap = min(abs(job_rank - lowest), abs(job_rank - highest))
    if job_rank > highest and gap >= 2:
        return 0.2, f"Role seniority ({job.seniority}) is above your target level"
    if job_rank < lowest and gap >= 2:
        return 0.4, f"Role seniority ({job.seniority}) is below your target level"
    return 0.6, None


def _location_match(profile: ProfileView, job: JobView) -> tuple[float, str | None]:
    pref = (profile.remote_preference or "everything").lower()
    workplace = (job.workplace_type or "unknown").lower()
    if pref in ("", "everything"):
        base = 1.0
    elif workplace == "unknown":
        base = 0.6
    elif pref == "remote" and workplace == "remote":
        base = 1.0
    elif pref == "remote" and workplace in ("onsite", "hybrid"):
        return 0.2, "You prefer remote but this role is not remote"
    elif pref == workplace:
        base = 1.0
    else:
        base = 0.6
    # Location string preference (city/region), if provided.
    if profile.preferred_locations and job.location and workplace != "remote":
        if any(loc.lower() in job.location.lower() for loc in profile.preferred_locations):
            base = min(1.0, base + 0.0)
        else:
            return min(base, 0.4), "Job location differs from your preferred locations"
    return base, None


def _education_match(profile: ProfileView, job: JobView) -> float:
    return 1.0 if profile.has_degree else 0.5


def _authorization_match(profile: ProfileView, job: JobView) -> tuple[float, str | None]:
    notes = (job.work_authorization_notes or "").lower()
    if not notes:
        return 1.0, None
    if profile.requires_sponsorship and "no visa sponsorship" in notes:
        return 0.0, "You need sponsorship but this role does not offer it"
    if "security clearance" in notes:
        return 0.3, "Role requires a security clearance"
    if "us citizenship" in notes:
        return 0.4, "Role requires US citizenship"
    return 0.8, None


def _resume_angle(matched_skills: list[str], job: JobView) -> str:
    if matched_skills:
        return f"Lead with {', '.join(matched_skills[:3])} and quantified impact relevant to {job.title}."
    return f"Highlight transferable strengths and projects relevant to {job.title}."


def _label(fit: int) -> str:
    if fit >= 85:
        return "Strong fit"
    if fit >= 70:
        return "Good fit"
    if fit >= 50:
        return "Stretch"
    return "Low fit"


def _confidence(profile: ProfileView, job: JobView) -> float:
    signals = [
        bool(profile.skills),
        bool(profile.target_roles),
        bool(job.required_skills),
        job.seniority is not None,
        job.workplace_type != "unknown",
    ]
    return round(0.5 + 0.1 * sum(signals), 2)


def _rank(level: str | None) -> int | None:
    if not level:
        return None
    low = level.lower()
    for index, name in enumerate(SENIORITY_ORDER):
        if name in low:
            return index
    return None


def _token_overlap(a: str, b: str) -> float:
    tokens_a = {t for t in a.split() if len(t) > 2}
    tokens_b = {t for t in b.split() if len(t) > 2}
    if not tokens_a:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a)
