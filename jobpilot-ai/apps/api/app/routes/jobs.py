import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.jobs.company_logo_service import normalize_company_key, resolve_company_logo
from app.jobs.job_eligibility_service import evaluate_eligibility
from app.jobs.job_ingestion_service import build_profile_view, discover_jobs, rematch_user
from app.jobs.job_ingestion_service import _job_view  # noqa: PLC2701 - internal shared mapper
from app.jobs.job_normalization_service import DEMO_COMPANIES, is_placeholder_url
from app.jobs.job_search_criteria_service import build_search_criteria
from app.jobs.safe_fetch import FetchFailedError, UnsafeUrlError, safe_fetch_image
from app.models.entities import (
    ApplicationStatus,
    ApplicationTracker,
    CompanyBranding,
    DocumentFormat,
    DocumentType,
    Experience,
    GeneratedDocument,
    JobMatch,
    JobPosting,
    JobSource,
    User,
    UserProfile,
)
from app.documents.cover_letter_generation_service import generate_cover_letter
from app.documents.filenames import build_document_filename
from app.documents.resume_generation_service import generate_resume
from app.documents.store import export_document as render_document_file
from app.documents.store import persist_document, serialize_document
from app.schemas.jobs import (
    ApplicationTrackerIn,
    DiscoverJobsIn,
    DocumentUpdateIn,
    GenerateMaterialsIn,
)
from app.services.documents import export_document_file, generate_document

router = APIRouter(prefix="/jobs", tags=["jobs"])

logger = logging.getLogger("jobpilot.logos")

_LOGO_CACHE_DIR = Path(os.getenv("UPLOAD_DIR", "uploads")) / "company_logos"
_EXT_FOR_CONTENT_TYPE = {
    "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/webp": "webp",
    "image/gif": "gif", "image/x-icon": "ico", "image/vnd.microsoft.icon": "ico"
}


@router.get("/companies/{normalized_key}/logo")
def company_logo_proxy(normalized_key: str, db: Session = Depends(get_db)) -> Response:
    """Serve a company's logo through a JobPilot-controlled endpoint instead of
    the browser hitting an arbitrary third-party/ATS-provided URL directly:
    avoids mixed-content/host-restriction issues, survives an upstream URL
    expiring or going down, and lets a broken source be re-resolved without
    the frontend caring. SSRF-safe: the only URL ever fetched server-side is
    the one already resolved and persisted on this company's CompanyBranding
    row — never an arbitrary client-supplied URL — and even that goes through
    safe_fetch_image's public-address validation before any bytes are trusted.
    """
    branding = db.scalar(select(CompanyBranding).where(CompanyBranding.normalized_key == normalized_key))
    if branding is None or not branding.logo_url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No logo resolved for this company")

    _LOGO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = _find_cached_logo(normalized_key)
    if cached is not None:
        path, content_type = cached
        return Response(content=path.read_bytes(), media_type=content_type, headers={"Cache-Control": "public, max-age=86400"})

    try:
        result = safe_fetch_image(branding.logo_url)
    except (UnsafeUrlError, FetchFailedError) as exc:
        logger.info("logo_proxy fetch_failed key=%s reason=%s", normalized_key, type(exc).__name__)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Logo could not be fetched") from exc
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all, see below
        # A logo is decoration: no upstream defect (a library API change, a
        # surprise exception type from a transport) may ever surface as an
        # unhandled 500 on a page that is otherwise fine. This is exactly how
        # the httpx `URL.human_repr()` removal took the endpoint down. The
        # client gets the same controlled 502 as any other fetch failure and
        # renders its neutral placeholder; the detail is logged, never returned.
        logger.warning(
            "logo_proxy unexpected_error key=%s reason=%s", normalized_key, type(exc).__name__, exc_info=True
        )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Logo could not be fetched") from exc

    ext = _EXT_FOR_CONTENT_TYPE.get(result.content_type, "img")
    (_LOGO_CACHE_DIR / f"{normalized_key}.{ext}").write_bytes(result.content)
    return Response(content=result.content, media_type=result.content_type, headers={"Cache-Control": "public, max-age=86400"})


def _find_cached_logo(normalized_key: str) -> tuple[Path, str] | None:
    for content_type, ext in _EXT_FOR_CONTENT_TYPE.items():
        path = _LOGO_CACHE_DIR / f"{normalized_key}.{ext}"
        if path.exists():
            return path, content_type
    return None


@router.post("/discover")
async def discover(
    payload: DiscoverJobsIn | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    body = payload or DiscoverJobsIn()
    result = await discover_jobs(
        db,
        user.id,
        days=body.posted_within_days,
        include_unknown=body.include_unknown_dates,
    )
    include_unknown_dates = body.include_unknown_dates or settings.job_discovery_include_unknown_dates
    listing = _list_payload(
        db,
        user,
        posted_within_days=body.posted_within_days,
        include_unknown=include_unknown_dates,
        include_unknown_location=body.include_unknown_location,
    )
    # Safe fallback: if too few strict matches, broaden by including eligible
    # unknown-posted-date jobs (labeled on the UI). This never relaxes the
    # location/seniority/role eligibility filters — only the date requirement.
    if len(listing["jobs"]) < _MIN_RESULTS and not include_unknown_dates:
        broadened = _list_payload(
            db,
            user,
            posted_within_days=body.posted_within_days,
            include_unknown=True,
            include_unknown_location=body.include_unknown_location,
        )
        if len(broadened["jobs"]) > len(listing["jobs"]):
            listing = broadened
            listing["broadened"] = "included_unknown_dates"

    listing["discovery"] = {
        "fetched": result.fetched,
        "fresh": result.fresh,
        "persisted": result.persisted,
        "matched": result.matched,
        "source_warnings": result.source_warnings,
        "sources_searched": result.sources_searched,
        "sources_succeeded": result.sources_succeeded,
        "sources_failed": result.sources_failed,
        "by_provider": result.by_provider,
        "packs": result.packs,
        **listing.get("counts", {}),
    }
    return listing


_MIN_RESULTS = 20


@router.get("")
def list_jobs(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    role: str | None = Query(default=None),
    company: str | None = Query(default=None),
    location: str | None = Query(default=None),
    workplace_type: str | None = Query(default=None),
    min_fit_score: float | None = Query(default=None, ge=0, le=100),
    posted_within_days: int = Query(default=7, ge=1, le=60),
    include_unknown_dates: bool = Query(default=False),
    include_unknown_location: bool = Query(default=False),
    include_ineligible: bool = Query(default=False),
) -> dict:
    return _list_payload(
        db,
        user,
        role=role,
        company=company,
        location=location,
        workplace_type=workplace_type,
        min_fit_score=min_fit_score,
        posted_within_days=posted_within_days,
        include_unknown=include_unknown_dates,
        include_unknown_location=include_unknown_location,
        include_ineligible=include_ineligible,
    )


def _list_payload(
    db: Session,
    user: User,
    *,
    role: str | None = None,
    company: str | None = None,
    location: str | None = None,
    workplace_type: str | None = None,
    min_fit_score: float | None = None,
    posted_within_days: int = 7,
    include_unknown: bool = False,
    include_unknown_location: bool = False,
    include_ineligible: bool = False,
) -> dict:
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    experiences = list(db.scalars(select(Experience).where(Experience.user_id == user.id)).all())
    criteria = build_search_criteria(profile, experiences)
    built = build_profile_view(db, user.id)
    profile_view = built[0] if built else None

    cutoff = datetime.now(UTC) - timedelta(days=posted_within_days)
    # Expired jobs (disappeared from the official source past the grace period)
    # are hidden from discovery by default; saved/tracked jobs live in the tracker
    # and are unaffected by this filter.
    jobs = db.scalars(select(JobPosting).where(JobPosting.is_active.is_(True))).all()
    matches = {
        match.job_id: match
        for match in db.scalars(select(JobMatch).where(JobMatch.user_id == user.id)).all()
    }
    sources = {source.id: source for source in db.scalars(select(JobSource)).all()}

    cards: list[dict] = []
    # Debug counts of why fresh jobs were excluded (shown only in dev on the UI).
    counts = {"fresh": 0, "excluded_location": 0, "excluded_seniority": 0, "excluded_role": 0, "excluded_other": 0, "eligible": 0}
    for job in jobs:
        if _is_demo_posting(job, sources.get(job.source_id)):
            continue  # never surface fake/demo/placeholder jobs, even if in the DB
        if not _passes_freshness(job.posted_at, cutoff, include_unknown):
            continue
        counts["fresh"] += 1

        # Hard eligibility gate: runs before any scoring/soft filters.
        eligibility = None
        if profile_view is not None:
            eligibility = evaluate_eligibility(
                profile_view, _job_view(job), include_unknown_location=include_unknown_location
            )
            if not eligibility.eligible:
                _tally_exclusion(counts, eligibility.reason_codes)
                if not include_ineligible:
                    continue
            else:
                counts["eligible"] += 1

        if not _passes_filters(job, role, company, location, workplace_type):
            continue
        match = matches.get(job.id)
        if min_fit_score is not None and (
            match is None or match.fit_score is None or match.fit_score < min_fit_score
        ):
            continue
        cards.append(_serialize_card(job, match, sources.get(job.source_id), eligibility))

    cards.sort(
        key=lambda card: (
            card["match"]["fit_score"]
            if card["match"] and card["match"].get("fit_score") is not None
            else -1.0,
            card["_posted_key"],
        ),
        reverse=True,
    )
    for card in cards:
        card.pop("_posted_key", None)

    return {
        "profile_complete": _profile_complete(profile),
        "criteria": criteria.model_dump(),
        "counts": counts,
        "profile_filters": _profile_filter_payload(profile),
        "jobs": cards,
    }


def _passes_freshness(posted_at: datetime | None, cutoff: datetime, include_unknown: bool) -> bool:
    if posted_at is None:
        return include_unknown
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=UTC)
    return posted_at >= cutoff


def _is_demo_posting(job: JobPosting, source: JobSource | None) -> bool:
    """Defensive guard so legacy demo rows can never reach the user, even if a
    cleanup migration has not yet run against this database."""
    if (job.company or "").strip().lower() in DEMO_COMPANIES:
        return True
    if source is not None and (source.type or "").strip().lower() == "demo":
        return True
    return is_placeholder_url(job.application_url) or is_placeholder_url(job.source_url)


def _passes_filters(
    job: JobPosting,
    role: str | None,
    company: str | None,
    location: str | None,
    workplace_type: str | None,
) -> bool:
    if role:
        haystack = f"{job.title} {job.description_clean} {' '.join(job.required_skills or [])}".lower()
        if role.lower() not in haystack:
            return False
    if company and company.lower() not in (job.company or "").lower():
        return False
    if location and location.lower() not in (job.location or "").lower():
        return False
    if workplace_type and workplace_type.lower() != (job.remote_type or "unknown").lower():
        return False
    return True


_EXCLUSION_BUCKETS = {"location": "excluded_location", "seniority": "excluded_seniority", "role": "excluded_role"}


def _tally_exclusion(counts: dict[str, int], reason_codes: list[str]) -> None:
    # Attribute the exclusion to its primary dimension (location > role > seniority order-agnostic).
    prefixes = {code.split(":", 1)[0] for code in reason_codes}
    for prefix, bucket in _EXCLUSION_BUCKETS.items():
        if prefix in prefixes:
            counts[bucket] += 1
            return
    counts["excluded_other"] += 1


def _serialize_card(
    job: JobPosting,
    match: JobMatch | None,
    source: JobSource | None,
    eligibility=None,
) -> dict:
    posted_key = job.posted_at or job.discovered_at
    if posted_key and posted_key.tzinfo is None:
        posted_key = posted_key.replace(tzinfo=UTC)
    # Prefer branding stored at ingestion; resolve on the fly for legacy rows
    # that predate the columns (so cards work before a backfill is run).
    company_domain = job.company_domain or ""
    company_logo_url = job.company_logo_url or ""
    if not company_logo_url:
        resolved = resolve_company_logo(job.company or "", source_type=source.type if source else None)
        company_domain = company_domain or resolved["company_domain"]
        company_logo_url = resolved["company_logo_url"]
    return {
        "id": job.id,
        "title": job.title,
        "company": job.company,
        "company_domain": company_domain or None,
        "company_logo_url": company_logo_url or None,
        # Preferred source: served through our own endpoint (cached, SSRF-safe,
        # survives the upstream URL changing/expiring). The frontend tries this
        # first and falls back to company_logo_url, then the neutral placeholder.
        "company_logo_proxy_path": f"/jobs/companies/{normalize_company_key(job.company or '')}/logo" if company_logo_url else None,
        "source": source.type if source else None,
        "location": job.location,
        "workplace_type": job.remote_type,
        "employment_type": job.employment_type,
        "seniority_level": job.seniority_level,
        "posted_at": job.posted_at,
        "discovered_at": job.discovered_at,
        "application_url": job.application_url,
        "source_url": job.source_url,
        "description_clean": job.description_clean,
        "required_skills": job.required_skills or [],
        "preferred_skills": job.preferred_skills or [],
        "responsibilities": job.responsibilities or [],
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_currency": job.currency,
        "match": _serialize_match(match),
        "eligibility": (
            {
                "eligible": eligibility.eligible,
                "reasons": eligibility.reasons,
                "flags": eligibility.flags,
            }
            if eligibility is not None
            else None
        ),
        "_posted_key": posted_key,
    }


def _serialize_match(match: JobMatch | None) -> dict | None:
    if match is None:
        return None
    return {
        "fit_score": match.fit_score,
        "fit_label": match.fit_label,
        # Lifecycle so the UI can render "Calculating fit…", a retry, or a prompt
        # to finish the profile instead of a permanent "Not scored".
        "score_state": match.score_state,
        "scoring_error_code": match.scoring_error_code,
        "match_reasons": match.strengths or [],
        "missing_skills": match.gaps or [],
        "risk_factors": match.risks or [],
        "recommended_resume_angle": match.recommended_resume_angle,
        "confidence": match.confidence,
        "explanation_source": match.explanation_source,
        "fit_summary": match.fit_summary,
    }


def _profile_complete(profile: UserProfile | None) -> bool:
    if profile is None:
        return False
    return bool((profile.target_roles or []) or (profile.skills or []))


def _profile_filter_payload(profile: UserProfile | None) -> dict:
    if profile is None:
        return {
            "target_roles": [],
            "target_levels": [],
            "preferred_locations": [],
            "work_preference": "everything",
        }
    return {
        "target_roles": profile.target_roles or [],
        "target_levels": profile.target_levels or [],
        "preferred_locations": profile.preferred_locations or [],
        "work_preference": profile.remote_preference or "everything",
    }


@router.get("/tracker/all")
def list_tracker(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(ApplicationTracker).where(ApplicationTracker.user_id == user.id)).all()
    return {
        "applications": [
            {
                "id": row.id,
                "job_id": row.job_id,
                "status": row.status.value,
                "notes": row.notes,
                "applied_at": row.applied_at,
            }
            for row in rows
        ]
    }


@router.get("/{job_id}")
def get_job(job_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    job = db.get(JobPosting, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    match = db.scalar(select(JobMatch).where((JobMatch.user_id == user.id) & (JobMatch.job_id == job_id)))
    source = db.get(JobSource, job.source_id) if job.source_id else None
    card = _serialize_card(job, match, source)
    card.pop("_posted_key", None)
    card["description_raw"] = job.description_raw
    card["degree_requirement"] = job.degree_requirement
    card["years_experience_min"] = job.years_experience_min
    card["work_authorization_notes"] = job.work_authorization_notes
    return {"job": card}


@router.post("/{job_id}/match")
def refresh_match(job_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    if db.get(JobPosting, job_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    rematch_user(db, user.id)
    match = db.scalar(select(JobMatch).where((JobMatch.user_id == user.id) & (JobMatch.job_id == job_id)))
    return {"match": _serialize_match(match)}


@router.post("/refresh-matches")
def refresh_matches(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    posted_within_days: int = Query(default=7, ge=1, le=60),
    include_unknown_dates: bool = Query(default=False),
    include_unknown_location: bool = Query(default=False),
) -> dict:
    """Re-score the existing (already-discovered) job index against the saved
    profile. This does NOT re-fetch external ATS sources, so a source outage can
    never break re-scoring. Eligibility filters are applied unchanged."""
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    if not _profile_complete(profile):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Complete your profile to refresh job matches.",
        )

    rescored = rematch_user(
        db, user.id, days=posted_within_days, include_unknown=include_unknown_dates
    )
    listing = _list_payload(
        db,
        user,
        posted_within_days=posted_within_days,
        include_unknown=include_unknown_dates,
        include_unknown_location=include_unknown_location,
    )
    counts = listing.get("counts", {})
    return {
        "jobs": listing["jobs"],
        "profile_complete": listing["profile_complete"],
        "criteria": listing["criteria"],
        "summary": {
            "matched_count": len(listing["jobs"]),
            "rescored_count": rescored,
            "raw_jobs_considered": counts.get("fresh", 0),
            "excluded_by_location": counts.get("excluded_location", 0),
            "excluded_by_seniority": counts.get("excluded_seniority", 0),
            "excluded_by_role": counts.get("excluded_role", 0),
            "excluded_by_date": _stale_count(db, posted_within_days, include_unknown_dates),
            "source_warnings": [],
        },
    }


def _stale_count(db: Session, posted_within_days: int, include_unknown: bool) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=posted_within_days)
    stale = 0
    for job in db.scalars(select(JobPosting)):
        if not _passes_freshness(job.posted_at, cutoff, include_unknown):
            stale += 1
    return stale


@router.post("/{job_id}/save")
def save_job(job_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    if db.get(JobPosting, job_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    tracker = db.scalar(
        select(ApplicationTracker).where(
            (ApplicationTracker.user_id == user.id) & (ApplicationTracker.job_id == job_id)
        )
    )
    if tracker is None:
        tracker = ApplicationTracker(user_id=user.id, job_id=job_id, status=ApplicationStatus.saved)
        db.add(tracker)
    db.commit()
    db.refresh(tracker)
    return {"tracker": {"id": tracker.id, "job_id": tracker.job_id, "status": tracker.status.value}}


def _require_job_and_profile(db: Session, user: User, job_id: int) -> JobPosting:
    job = db.get(JobPosting, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    if profile is None or not (profile.full_name or profile.skills):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Add your profile details (name, skills, experience) before generating documents.",
        )
    return job


@router.post("/{job_id}/generate-resume")
async def generate_resume_endpoint(
    job_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    job = _require_job_and_profile(db, user, job_id)
    result = await generate_resume(db, user.id, job)
    record = persist_document(
        db, user.id, job, DocumentType.resume,
        title=result.title, content=result.content, markdown=result.markdown,
        plain_text=result.plain_text, quality=result.quality, model_used=result.model_used,
    )
    return serialize_document(
        record, warnings=result.warnings, unsupported_claims_removed=result.unsupported_claims_removed
    )


@router.post("/{job_id}/generate-cover-letter")
async def generate_cover_letter_endpoint(
    job_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    job = _require_job_and_profile(db, user, job_id)
    result = await generate_cover_letter(db, user.id, job)
    record = persist_document(
        db, user.id, job, DocumentType.cover_letter,
        title=result.title, content=result.content, markdown=result.markdown,
        plain_text=result.plain_text, quality=result.quality, model_used=result.model_used,
    )
    return serialize_document(
        record, warnings=result.warnings, unsupported_claims_removed=result.unsupported_claims_removed
    )


@router.post("/{job_id}/generate-materials")
async def generate_materials(
    job_id: int,
    payload: GenerateMaterialsIn | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    job = _require_job_and_profile(db, user, job_id)
    types = (payload.types if payload else None) or ["resume", "cover_letter"]
    documents: dict[str, dict] = {}
    if "resume" in types:
        result = await generate_resume(db, user.id, job)
        record = persist_document(db, user.id, job, DocumentType.resume, title=result.title,
                                  content=result.content, markdown=result.markdown, plain_text=result.plain_text,
                                  quality=result.quality, model_used=result.model_used)
        documents["resume"] = serialize_document(record, warnings=result.warnings,
                                                 unsupported_claims_removed=result.unsupported_claims_removed)
    if "cover_letter" in types:
        cover = await generate_cover_letter(db, user.id, job)
        record = persist_document(db, user.id, job, DocumentType.cover_letter, title=cover.title,
                                  content=cover.content, markdown=cover.markdown, plain_text=cover.plain_text,
                                  quality=cover.quality, model_used=cover.model_used)
        documents["cover_letter"] = serialize_document(record, warnings=cover.warnings,
                                                       unsupported_claims_removed=cover.unsupported_claims_removed)
    return {"documents": documents}


@router.put("/documents/{document_id}")
def update_document(
    document_id: int,
    payload: DocumentUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    record = db.get(GeneratedDocument, document_id)
    if record is None or record.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if payload.markdown is not None:
        record.content_markdown = payload.markdown
    if payload.plain_text is not None:
        record.plain_text = payload.plain_text
    if payload.content is not None:
        record.content = payload.content
    if payload.title is not None:
        record.title = payload.title
    db.commit()
    db.refresh(record)
    return serialize_document(record)


@router.get("/documents/{document_id}/download/{fmt}")
def download_document(
    document_id: int,
    fmt: DocumentFormat,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    record = db.get(GeneratedDocument, document_id)
    if record is None or record.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    path = render_document_file(record, fmt)
    db.commit()
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    filename = build_document_filename(
        kind=record.type.value,
        fmt=fmt.value,
        full_name=profile.full_name if profile else None,
        first_name=profile.first_name if profile else None,
        last_name=profile.last_name if profile else None,
        company=(record.job_snapshot or {}).get("company"),
    )
    return FileResponse(path, filename=filename)


@router.post("/{job_id}/documents/{doc_type}")
async def create_document(
    job_id: int,
    doc_type: DocumentType,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        record = await generate_document(db, user.id, job_id, doc_type)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"document": record}


@router.post("/documents/{document_id}/export/{fmt}")
def export_document(
    document_id: int,
    fmt: DocumentFormat,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    record = db.get(GeneratedDocument, document_id)
    if record is None or record.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    file_path = export_document_file(record, fmt)
    record.format = fmt
    record.file_path = file_path
    db.commit()
    return {"file_path": file_path}


@router.put("/{job_id}/tracker")
def upsert_tracker(
    job_id: int,
    payload: ApplicationTrackerIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if db.get(JobPosting, job_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    tracker = db.scalar(
        select(ApplicationTracker).where(
            (ApplicationTracker.user_id == user.id) & (ApplicationTracker.job_id == job_id)
        )
    )
    status_value = ApplicationStatus(payload.status)
    if tracker is None:
        tracker = ApplicationTracker(user_id=user.id, job_id=job_id, status=status_value)
        db.add(tracker)
    tracker.status = status_value
    tracker.notes = payload.notes
    if status_value == ApplicationStatus.applied and tracker.applied_at is None:
        tracker.applied_at = datetime.utcnow()
    db.commit()
    db.refresh(tracker)
    return {"tracker": tracker}
