"""Background fit-scoring pipeline with an explicit per-pair state machine.

This is the single place that decides *whether* a ``(user, job)`` pair needs
scoring and *transitions* its ``JobMatch`` row through the ScoreState lifecycle.
It is intentionally deterministic and idempotent:

* A pair is (re)scored only when something score-relevant changed — the job's
  content hash, the user's profile hash, or the global ``SCORE_VERSION``.
* Re-running with identical inputs is a no-op (``skipped``).
* A newer score is never overwritten by an older worker result (version guard).
* One failing pair never aborts the batch; it is marked ``failed`` and the loop
  continues.
* Users without enough profile information get a ``profile_incomplete`` row
  instead of a number, so the UI can prompt them rather than showing a fake 0.

The actual numeric scoring stays in ``job_matching_service.score_job`` — this
module only orchestrates persistence, state and change-detection.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.jobs.job_eligibility_service import evaluate_eligibility
from app.jobs.job_matching_service import JobView, MatchResult, ProfileView, score_job
from app.jobs.job_normalization_service import is_fresh
from app.jobs.job_search_criteria_service import SearchCriteria, build_search_criteria
from app.models.entities import (
    Education,
    Experience,
    JobMatch,
    JobPosting,
    Project,
    ScoreState,
    User,
    UserProfile,
)

logger = logging.getLogger("jobpilot.scoring")

# Bump when the scoring algorithm changes so previously-scored rows are refreshed
# by the backfill / next run instead of being treated as up to date.
SCORE_VERSION = 2

# Default page size for scanning jobs/users; overridable by callers/config.
DEFAULT_BATCH_SIZE = 100


@dataclass
class ScoreOutcome:
    state: ScoreState
    changed: bool  # True when the row was created or its score/state was updated


@dataclass
class ScoringStats:
    scanned: int = 0
    selected: int = 0
    scored: int = 0
    skipped: int = 0
    failed: int = 0
    profile_incomplete: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "scanned": self.scanned,
            "selected": self.selected,
            "scored": self.scored,
            "skipped": self.skipped,
            "failed": self.failed,
            "profile_incomplete": self.profile_incomplete,
        }


# --------------------------------------------------------------------------- #
# Hashing / change detection
# --------------------------------------------------------------------------- #
def _hash(payload: object) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def compute_job_content_hash(job: JobPosting) -> str:
    """Hash of the score-relevant job fields only. Irrelevant metadata churn
    (logo, discovered_at, tracking params) does not change this, so it does not
    trigger a rescore."""
    return _hash(
        {
            "title": (job.title or "").strip().lower(),
            "description": (job.description_clean or "").strip(),
            "required_skills": sorted(s.lower() for s in (job.required_skills or [])),
            "preferred_skills": sorted(s.lower() for s in (job.preferred_skills or [])),
            "location": (job.location or "").strip().lower(),
            "remote_type": (job.remote_type or "").strip().lower(),
            "employment_type": (job.employment_type or "").strip().lower(),
            "seniority_level": (job.seniority_level or "").strip().lower(),
            "salary_min": job.salary_min,
            "salary_max": job.salary_max,
            "years_experience_min": job.years_experience_min,
        }
    )


def compute_profile_version(view: ProfileView) -> str:
    """Hash of the profile fields that affect a score."""
    return _hash(
        {
            "target_roles": sorted(r.lower() for r in view.target_roles),
            "skills": sorted(s.lower() for s in view.skills),
            "experience_titles": sorted(t.lower() for t in view.experience_titles),
            "project_terms": sorted(t.lower() for t in view.project_terms),
            "preferred_locations": sorted(loc.lower() for loc in view.preferred_locations),
            "remote_preference": view.remote_preference,
            "seniority_targets": sorted(view.seniority_targets),
            "target_levels": sorted(lvl.lower() for lvl in view.target_levels),
            "has_degree": view.has_degree,
            "requires_sponsorship": view.requires_sponsorship,
            "open_to_relocation": view.open_to_relocation,
            "location_country": view.location_country.strip().lower(),
        }
    )


# --------------------------------------------------------------------------- #
# Profile view (kept here to avoid a circular import with the ingestion service)
# --------------------------------------------------------------------------- #
def has_scorable_profile(profile: UserProfile | None) -> bool:
    """A profile is scorable once it declares target roles or skills."""
    if profile is None:
        return False
    return bool((profile.target_roles or []) or (profile.skills or []))


def build_profile_view(db: Session, user_id: int) -> tuple[ProfileView, SearchCriteria] | None:
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if profile is None:
        return None
    experiences = list(db.scalars(select(Experience).where(Experience.user_id == user_id)).all())
    projects = list(db.scalars(select(Project).where(Project.user_id == user_id)).all())
    education = list(db.scalars(select(Education).where(Education.user_id == user_id)).all())
    criteria = build_search_criteria(profile, experiences)
    view = ProfileView(
        target_roles=profile.target_roles or [],
        skills=profile.skills or [],
        experience_titles=[exp.title for exp in experiences if exp.title],
        project_terms=[p.name for p in projects if p.name]
        + [tech for p in projects for tech in (p.technologies or [])],
        preferred_locations=profile.preferred_locations or [],
        remote_preference=profile.remote_preference or "everything",
        seniority_targets=criteria.seniority_targets,
        target_levels=profile.target_levels or [],
        has_degree=bool(education),
        requires_sponsorship=profile.requires_sponsorship,
        open_to_relocation=profile.open_to_relocation,
        location_country=profile.location_country or "",
    )
    return view, criteria


def job_view(job: JobPosting) -> JobView:
    return JobView(
        title=job.title or "",
        description=job.description_clean or "",
        required_skills=job.required_skills or [],
        preferred_skills=job.preferred_skills or [],
        location=job.location,
        workplace_type=(job.remote_type or "unknown"),
        seniority=job.seniority_level,
        work_authorization_notes=job.work_authorization_notes,
    )


# --------------------------------------------------------------------------- #
# Per-pair scoring
# --------------------------------------------------------------------------- #
def _needs_scoring(match: JobMatch | None, job_hash: str, profile_hash: str) -> bool:
    if match is None:
        return True
    if match.score_state in (ScoreState.pending.value, ScoreState.failed.value):
        return True
    if match.score_version != SCORE_VERSION:
        return True
    if match.job_content_hash != job_hash:
        return True
    if match.profile_version != profile_hash:
        return True
    return False


def _compute_match(
    profile_view: ProfileView, job: JobPosting, criteria: SearchCriteria
) -> MatchResult:
    view = job_view(job)
    # Eligibility runs BEFORE scoring; hard-ineligible jobs get a zeroed result so
    # they are never ranked or surfaced by default (mirrors legacy behaviour).
    eligibility = evaluate_eligibility(profile_view, view, include_unknown_location=False)
    if eligibility.eligible or eligibility.only_unknown_location:
        return score_job(profile_view, view, criteria)
    return MatchResult(
        fit_score=0,
        fit_label="Not eligible",
        match_reasons=[],
        missing_skills=[],
        risk_factors=eligibility.reasons,
        recommended_resume_angle="",
        confidence=0.0,
    )


def score_pair(
    db: Session,
    user_id: int,
    job: JobPosting,
    profile_view: ProfileView,
    profile_hash: str,
    criteria: SearchCriteria,
    *,
    force: bool = False,
    flush: bool = True,
) -> ScoreOutcome:
    """Score a single ``(user, job)`` pair and upsert its ``JobMatch`` row.

    Never raises for a scoring error — failures are captured on the row so the
    surrounding batch keeps going. Caller owns the transaction/commit."""
    job_hash = compute_job_content_hash(job)
    match = db.scalar(
        select(JobMatch).where((JobMatch.user_id == user_id) & (JobMatch.job_id == job.id))
    )

    if not force and not _needs_scoring(match, job_hash, profile_hash):
        return ScoreOutcome(state=ScoreState(match.score_state), changed=False)

    # Version guard: never let a stale worker (older SCORE_VERSION) clobber a row
    # already scored at a newer version.
    if match is not None and match.score_version > SCORE_VERSION:
        return ScoreOutcome(state=ScoreState(match.score_state), changed=False)

    if match is None:
        match = JobMatch(user_id=user_id, job_id=job.id, score_state=ScoreState.scoring.value)
        db.add(match)

    match.scoring_attempts = (match.scoring_attempts or 0) + 1
    try:
        result = _compute_match(profile_view, job, criteria)
        summary = "; ".join(result.match_reasons) or "Reviewed against your profile."
        match.fit_score = float(result.fit_score)
        match.fit_label = result.fit_label
        match.fit_summary = summary
        match.strengths = result.match_reasons
        match.gaps = result.missing_skills
        match.risks = result.risk_factors
        match.recommended_resume_angle = result.recommended_resume_angle
        match.confidence = result.confidence
        match.explanation_source = "deterministic"
        match.score_state = ScoreState.scored.value
        match.score_version = SCORE_VERSION
        match.job_content_hash = job_hash
        match.profile_version = profile_hash
        match.scoring_error_code = None
        match.scored_at = datetime.now(UTC)
        if flush:
            db.flush()
        return ScoreOutcome(state=ScoreState.scored, changed=True)
    except Exception as exc:  # noqa: BLE001 - isolate per-pair failures
        logger.exception("Scoring failed for user=%s job=%s", user_id, job.id)
        match.score_state = ScoreState.failed.value
        match.scoring_error_code = type(exc).__name__[:60]
        if flush:
            db.flush()
        return ScoreOutcome(state=ScoreState.failed, changed=True)


def mark_profile_incomplete(db: Session, user_id: int, job: JobPosting, *, flush: bool = True) -> ScoreOutcome:
    """Ensure a ``profile_incomplete`` placeholder row exists for a pair so the UI
    can prompt the user to finish their profile instead of showing 'Not scored'."""
    match = db.scalar(
        select(JobMatch).where((JobMatch.user_id == user_id) & (JobMatch.job_id == job.id))
    )
    if match is not None and match.score_state == ScoreState.scored.value:
        return ScoreOutcome(state=ScoreState.scored, changed=False)  # keep a real score
    if match is None:
        match = JobMatch(user_id=user_id, job_id=job.id)
        db.add(match)
    changed = match.score_state != ScoreState.profile_incomplete.value
    match.score_state = ScoreState.profile_incomplete.value
    match.fit_score = None
    match.fit_summary = None
    if flush:
        db.flush()
    return ScoreOutcome(state=ScoreState.profile_incomplete, changed=changed)


# --------------------------------------------------------------------------- #
# Batch scoring for one user
# --------------------------------------------------------------------------- #
def _fresh_active_job_ids(
    db: Session, *, days: int, include_unknown: bool
) -> list[int]:
    ids: list[int] = []
    for job in db.scalars(
        select(JobPosting).where(JobPosting.is_active.is_(True)).order_by(JobPosting.id)
    ):
        if is_fresh(job.posted_at, days, include_unknown=include_unknown):
            ids.append(job.id)
    return ids


def score_jobs_for_user(
    db: Session,
    user_id: int,
    *,
    days: int = 7,
    include_unknown: bool = False,
    only_missing: bool = False,
    force: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    commit: bool = True,
) -> ScoringStats:
    """Score all fresh, active jobs for one user, paginated. Returns per-run stats.

    ``only_missing`` restricts work to pairs that have no scored row yet (used by
    backfill). ``force`` ignores change-detection and rescores everything."""
    stats = ScoringStats()
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    built = build_profile_view(db, user_id)

    job_ids = _fresh_active_job_ids(db, days=days, include_unknown=include_unknown)
    stats.scanned = len(job_ids)

    # User cannot be scored yet → leave placeholder rows and stop.
    if built is None or not has_scorable_profile(profile):
        for start in range(0, len(job_ids), batch_size):
            chunk = job_ids[start : start + batch_size]
            jobs = db.scalars(select(JobPosting).where(JobPosting.id.in_(chunk))).all()
            for job in jobs:
                outcome = mark_profile_incomplete(db, user_id, job)
                stats.profile_incomplete += 1
                if outcome.changed:
                    stats.selected += 1
            if commit:
                db.commit()
        return stats

    profile_view, criteria = built
    profile_hash = compute_profile_version(profile_view)

    existing_scored: set[int] = set()
    if only_missing:
        existing_scored = {
            m.job_id
            for m in db.scalars(
                select(JobMatch).where(
                    (JobMatch.user_id == user_id)
                    & (JobMatch.score_state == ScoreState.scored.value)
                    & (JobMatch.score_version == SCORE_VERSION)
                )
            )
        }

    for start in range(0, len(job_ids), batch_size):
        chunk = [jid for jid in job_ids[start : start + batch_size] if jid not in existing_scored]
        if not chunk:
            continue
        jobs = db.scalars(select(JobPosting).where(JobPosting.id.in_(chunk))).all()
        for job in jobs:
            outcome = score_pair(db, user_id, job, profile_view, profile_hash, criteria, force=force)
            if not outcome.changed:
                stats.skipped += 1
                continue
            stats.selected += 1
            if outcome.state == ScoreState.scored:
                stats.scored += 1
            elif outcome.state == ScoreState.failed:
                stats.failed += 1
        if commit:
            db.commit()
    return stats


# --------------------------------------------------------------------------- #
# Scoring one job across many users (post-ingest fan-out)
# --------------------------------------------------------------------------- #
def active_user_ids(db: Session, *, batch_size: int = DEFAULT_BATCH_SIZE) -> list[int]:
    """Users eligible for scoring. 'Active' == has a profile row; we never load
    the whole users table into memory beyond their ids."""
    return list(
        db.scalars(
            select(User.id).join(UserProfile, UserProfile.user_id == User.id).order_by(User.id)
        )
    )


def score_users_for_job(
    db: Session,
    job_id: int,
    user_ids: list[int],
    *,
    commit: bool = True,
) -> ScoringStats:
    """Score one freshly ingested job for a set of users."""
    stats = ScoringStats()
    job = db.get(JobPosting, job_id)
    if job is None:
        return stats
    stats.scanned = len(user_ids)
    for user_id in user_ids:
        profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
        built = build_profile_view(db, user_id)
        if built is None or not has_scorable_profile(profile):
            outcome = mark_profile_incomplete(db, user_id, job)
            stats.profile_incomplete += 1
            if outcome.changed:
                stats.selected += 1
            continue
        profile_view, criteria = built
        profile_hash = compute_profile_version(profile_view)
        outcome = score_pair(db, user_id, job, profile_view, profile_hash, criteria)
        if outcome.changed:
            stats.selected += 1
            if outcome.state == ScoreState.scored:
                stats.scored += 1
            elif outcome.state == ScoreState.failed:
                stats.failed += 1
        else:
            stats.skipped += 1
    if commit:
        db.commit()
    return stats
