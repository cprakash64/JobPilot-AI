import logging
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.ai.provider import ai_provider
from app.core.audit import record_audit
from app.db.session import get_db
from app.documents.resume_compaction_service import looks_like_fragment
from app.profile.names import compose_full_name
from app.profile.phone import parse_phone
from app.models.entities import (
    Award,
    Certification,
    Education,
    Experience,
    Project,
    SensitiveDemographics,
    User,
    UserProfile,
)
from app.schemas.import_profile import (
    FileSourceType,
    ImportProfileApplyRequest,
    ImportProfileDraft,
    ImportProfileResponse,
    ImportProfileTextRequest,
)
from app.schemas.profile import (
    CareerProfileIn,
    ProfileImportIn,
    ProfileNameIn,
    SensitiveDemographicsIn,
    UserProfileIn,
    WorkdayCredentialsIn,
    WORK_AUTHORIZATION_STATUSES,
    WORK_PREFERENCES,
)
from app.services.document_parser import (
    DocumentParserError,
    detect_document_kind,
    extract_uploaded_file_text,
)
from app.services.profile_import import normalize_import_draft

router = APIRouter(prefix="/profile", tags=["profile"])

# Bumped when the import/dedupe pipeline changes; stored in the import audit log.
PARSER_VERSION = "2025.07.1"

PROFILE_IMPORT_SECTIONS = {
    "basic_info",
    "job_targets",
    "education",
    "experience",
    "projects",
    "skills",
    "certifications",
    "awards",
    "links",
}


def serialize_profile(profile: UserProfile | None) -> dict[str, Any] | None:
    if profile is None:
        return None
    work_preference = profile.remote_preference or "everything"
    return {
        "id": profile.id,
        "user_id": profile.user_id,
        "full_name": profile.full_name or "",
        "first_name": profile.first_name,
        "middle_name": profile.middle_name,
        "last_name": profile.last_name,
        "preferred_first_name": profile.preferred_first_name,
        "preferred_last_name": profile.preferred_last_name,
        "preferred_name": profile.preferred_name,
        "name_confirmed": profile.name_confirmed,
        # Legacy keys, still emitted for clients built against the old shape.
        "given_name": profile.first_name,
        "family_name": profile.last_name,
        "phone": profile.phone,
        "phone_country_code": profile.phone_country_code,
        "phone_country_iso2": profile.phone_country_iso2,
        "phone_national_number": profile.phone_national_number,
        "phone_e164": profile.phone_e164,
        "application_email": profile.application_email,
        "application_email_confirmed": profile.application_email_confirmed,
        "workday_password_configured": bool(profile.workday_password_ciphertext),
        "location_city": profile.location_city,
        "location_state": profile.location_state,
        "location_postal_code": profile.location_postal_code,
        "location_country": profile.location_country,
        "linkedin_url": profile.linkedin_url,
        "github_url": profile.github_url,
        "portfolio_url": profile.portfolio_url,
        "work_authorization": profile.work_authorization,
        "work_authorization_status": profile.work_authorization,
        "requires_sponsorship": profile.requires_sponsorship,
        "open_to_relocation": profile.open_to_relocation,
        "target_roles": profile.target_roles or [],
        "target_levels": profile.target_levels or [],
        "preferred_locations": profile.preferred_locations or [],
        "remote_preference": work_preference,
        "work_preference": work_preference,
        "skills": profile.skills or [],
    }


def serialize_records(records: list[Any]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for record in records:
        item = {
            column.name: getattr(record, column.name)
            for column in record.__table__.columns
            if column.name not in {"user_id"}
        }
        serialized.append(item)
    return serialized


def career_payload(user_id: int, db: Session) -> dict[str, list[dict[str, Any]]]:
    return {
        "education": serialize_records(db.scalars(select(Education).where(Education.user_id == user_id)).all()),
        "experience": serialize_records(db.scalars(select(Experience).where(Experience.user_id == user_id)).all()),
        "projects": serialize_records(db.scalars(select(Project).where(Project.user_id == user_id)).all()),
        "certifications": serialize_records(db.scalars(select(Certification).where(Certification.user_id == user_id)).all()),
        "awards": serialize_records(db.scalars(select(Award).where(Award.user_id == user_id)).all()),
    }


@router.get("")
def get_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    return {"profile": serialize_profile(profile)}


def _apply_imported_name(profile: UserProfile, basic: Any, *, overwrite: bool) -> None:
    """Persist the name split the user confirmed in the import review UI.

    This is the ONE import path allowed to set ``name_confirmed``, because the
    parts arrive from a screen where the user saw and could correct them. If the
    client sent no parts (an older build, or the user cleared them), nothing is
    written and the name stays unconfirmed — it is never re-derived by splitting
    ``full_name`` here.
    """
    first = (getattr(basic, "first_name", "") or "").strip()
    last = (getattr(basic, "last_name", "") or "").strip()
    if not first or not last:
        return
    if profile.name_confirmed and not overwrite:
        return
    profile.first_name = first
    profile.middle_name = (getattr(basic, "middle_name", "") or "").strip() or None
    profile.last_name = last
    profile.full_name = compose_full_name(first, profile.middle_name, last)
    profile.name_confirmed = True


def _apply_imported_phone(profile: UserProfile) -> None:
    """Re-derive the structured phone columns from whatever phone was imported."""
    for key, value in _phone_columns(profile.phone, profile.location_country).items():
        setattr(profile, key, value)


def _application_email_columns(
    profile: UserProfile | None, submitted: Any
) -> dict[str, Any]:
    """Columns for an explicit application-email save.

    Saving the field IS the confirmation — the user typed an address into a
    control labelled "Application email" and pressed Save. Changing it to a
    different address re-confirms at the same moment; clearing it drops the
    confirmation rather than leaving a stale one behind.
    """
    email = (str(submitted or "")).strip()
    if not email:
        return {
            "application_email": None,
            "application_email_confirmed": False,
            "application_email_updated_at": None,
        }
    unchanged = profile is not None and (profile.application_email or "").strip().lower() == email.lower()
    return {
        "application_email": email,
        "application_email_confirmed": True,
        # Keep the original timestamp when the value did not actually change.
        "application_email_updated_at": (
            profile.application_email_updated_at if unchanged and profile else datetime.now(UTC)
        ),
    }


def _phone_columns(raw_phone: Any, location_country: Any) -> dict[str, str | None]:
    """Structured phone columns for a raw phone string.

    An unparseable or invalid number clears the structured columns rather than
    storing a half-parsed guess — the raw ``phone`` value is still kept, and the
    profile UI surfaces it as needing attention.
    """
    parts = parse_phone(raw_phone, _region_for_country(location_country))
    if not parts.valid:
        return {
            "phone_country_code": None,
            "phone_country_iso2": None,
            "phone_national_number": None,
            "phone_e164": None,
        }
    return {
        "phone_country_code": parts.country_code,
        "phone_country_iso2": parts.country_iso2,
        "phone_national_number": parts.national_number,
        "phone_e164": parts.e164,
    }


_COUNTRY_TO_REGION = {
    "united states": "US", "usa": "US", "us": "US",
    "canada": "CA", "united kingdom": "GB", "uk": "GB",
    "india": "IN", "australia": "AU", "germany": "DE",
}


def _region_for_country(location_country: Any) -> str:
    return _COUNTRY_TO_REGION.get(str(location_country or "").strip().lower(), "US")


@router.put("")
def upsert_profile(
    payload: UserProfileIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    values = payload.model_dump(mode="json")
    # Re-derive the structured phone columns from whatever the user typed, so
    # the split parts can never drift from the raw value.
    values.update(_phone_columns(values.get("phone"), values.get("location_country")))
    values.update(_application_email_columns(profile, values.get("application_email")))
    if profile is None:
        profile = UserProfile(user_id=user.id, **values)
        db.add(profile)
    else:
        for key, value in values.items():
            setattr(profile, key, value)
    db.commit()
    db.refresh(profile)
    _enqueue_profile_rescore(user.id)
    return {"profile": serialize_profile(profile)}


@router.put("/workday-credentials")
def set_workday_credentials(
    payload: WorkdayCredentialsIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Store a Workday password encrypted at rest; never return the value."""
    from app.profile.credentials import encrypt_workday_password

    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Save your basic profile before adding Workday credentials.",
        )
    profile.workday_password_ciphertext = encrypt_workday_password(payload.password)
    db.commit()
    return {"configured": True}


@router.delete("/workday-credentials")
def clear_workday_credentials(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    if profile is not None:
        profile.workday_password_ciphertext = None
        db.commit()
    return {"configured": False}


@router.put("/name")
def confirm_profile_name(
    payload: ProfileNameIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Explicit structured-name confirmation (never inferred). Kept separate
    from PUT /profile so an unrelated profile edit can never silently reset
    ``name_confirmed``."""
    from app.applications.answer_vault_service import confirm_name

    profile = confirm_name(
        db,
        user.id,
        payload.first_name,
        payload.last_name,
        middle_name=payload.middle_name,
        preferred_first_name=payload.preferred_first_name,
        preferred_last_name=payload.preferred_last_name,
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="Create your profile before confirming your name")
    db.commit()
    db.refresh(profile)
    return {"profile": serialize_profile(profile)}


def _enqueue_profile_rescore(user_id: int) -> None:
    """A profile change is score-relevant, so re-score this user's recent active
    jobs in the background. Best-effort: if the broker is down the next page load
    / "Refresh matches" still rescores on demand (the scoring service's change
    detection makes this idempotent)."""
    from app.jobs.job_ingestion_service import broker_reachable

    if not broker_reachable():
        return  # on-demand rescore path covers this; avoid a slow broker publish
    try:
        from app.workers.tasks import match_jobs_for_user_task

        match_jobs_for_user_task.apply_async(args=[user_id], retry=False)
    except Exception:  # noqa: BLE001 - broker optional; on-demand path covers it
        logging.getLogger("jobpilot.scoring").info("Profile rescore not enqueued (broker down).")


@router.get("/career")
def get_career(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return career_payload(user.id, db)


@router.put("/career")
def replace_career(
    payload: CareerProfileIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    for model in [Education, Experience, Project, Certification, Award]:
        db.execute(delete(model).where(model.user_id == user.id))
    db.add_all([Education(user_id=user.id, **item.model_dump()) for item in payload.education])
    db.add_all([Experience(user_id=user.id, **item.model_dump()) for item in payload.experience])
    db.add_all([Project(user_id=user.id, **item.model_dump()) for item in payload.projects])
    db.add_all([Certification(user_id=user.id, **item.model_dump()) for item in payload.certifications])
    db.add_all([Award(user_id=user.id, **item.model_dump()) for item in payload.awards])
    db.commit()
    _enqueue_profile_rescore(user.id)  # experience/education/projects affect scoring
    return career_payload(user.id, db)


@router.post("/import")
async def import_profile(
    payload: ProfileImportIn,
    user: User = Depends(get_current_user),
) -> ImportProfileResponse:
    source_type = "resume_text" if payload.source == "resume_text" else "other_text"
    draft, model_used = await parse_import_text(payload.text, source_type)
    return import_response(draft, model_used)


@router.post("/import/text")
async def import_profile_text(
    payload: ImportProfileTextRequest,
    user: User = Depends(get_current_user),
) -> ImportProfileResponse:
    draft, model_used = await parse_import_text(payload.text, payload.source_type)
    return import_response(draft, model_used)


@router.post("/import/file")
async def import_profile_file(
    source_type: FileSourceType = Form("auto"),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
) -> ImportProfileResponse:
    try:
        content = await file.read()
        text = extract_uploaded_file_text(file, content)
    except DocumentParserError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    detected_kind = detect_document_kind(text, file.filename)
    draft_source = detected_kind if source_type == "auto" else source_type
    if draft_source == "auto":
        draft_source = "unknown"
    draft, model_used = await parse_import_text(text, draft_source)
    response_kind = detected_kind if source_type == "auto" else draft_source
    return import_response(draft, model_used, document_kind=response_kind)


@router.post("/import/apply")
def apply_profile_import(
    payload: ImportProfileApplyRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    sections = set(payload.sections) & PROFILE_IMPORT_SECTIONS
    draft = payload.draft
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    if profile is None:
        profile = UserProfile(user_id=user.id)
        db.add(profile)
        db.flush()

    if "basic_info" in sections:
        apply_profile_strings(
            profile,
            draft.basic_info.model_dump(),
            [
                "full_name",
                "phone",
                "location_city",
                "location_state",
                "location_country",
            ],
            payload.overwrite,
        )
        _apply_imported_name(profile, draft.basic_info, overwrite=payload.overwrite)
        _apply_imported_phone(profile)
        work_authorization = clean_string(draft.basic_info.work_authorization_status)
        if work_authorization in WORK_AUTHORIZATION_STATUSES and should_apply(profile.work_authorization, work_authorization, payload.overwrite):
            profile.work_authorization = work_authorization
        if draft.basic_info.requires_sponsorship is not None and (payload.overwrite or not profile.requires_sponsorship):
            profile.requires_sponsorship = bool(draft.basic_info.requires_sponsorship)

    if "links" in sections:
        link_values = draft.links.model_dump()
        basic_values = draft.basic_info.model_dump()
        for field in ["linkedin_url", "github_url", "portfolio_url"]:
            imported = clean_string(link_values.get(field) or basic_values.get(field))
            if should_apply(getattr(profile, field), imported, payload.overwrite):
                setattr(profile, field, imported)

    if "job_targets" in sections:
        profile.target_roles = apply_string_list(profile.target_roles or [], draft.job_targets.target_roles, payload.overwrite)
        profile.target_levels = apply_string_list(profile.target_levels or [], draft.job_targets.target_levels, payload.overwrite)
        profile.preferred_locations = apply_string_list(
            profile.preferred_locations or [],
            draft.job_targets.preferred_locations,
            payload.overwrite,
        )
        work_preference = draft.job_targets.work_preference or ""
        if work_preference in WORK_PREFERENCES and should_apply(profile.remote_preference, work_preference, payload.overwrite):
            profile.remote_preference = work_preference

    if "skills" in sections:
        profile.skills = apply_string_list(profile.skills or [], draft.skills, payload.overwrite)

    if "education" in sections:
        apply_records(
            db,
            user.id,
            Education,
            _non_empty([education_values(item.model_dump()) for item in draft.education], key="school"),
            payload.overwrite,
        )
    if "experience" in sections:
        apply_records(
            db,
            user.id,
            Experience,
            _non_empty_experience([experience_values(item.model_dump()) for item in draft.experience]),
            payload.overwrite,
        )
    if "projects" in sections:
        apply_records(
            db,
            user.id,
            Project,
            _valid_projects([project_values(item.model_dump()) for item in draft.projects]),
            payload.overwrite,
        )
    if "certifications" in sections:
        apply_records(
            db,
            user.id,
            Certification,
            _non_empty([certification_values(item.model_dump()) for item in draft.certifications], key="name"),
            payload.overwrite,
        )
    if "awards" in sections:
        apply_records(
            db,
            user.id,
            Award,
            _non_empty([award_values(item.model_dump()) for item in draft.awards], key="name"),
            payload.overwrite,
        )

    # Final safety net: collapse any duplicate rows (legacy or parser-emitted)
    # so re-importing the same resume can never double the user's data.
    dedupe_user_records(db, user.id)

    # Import metadata for auditability (source_type / imported_at / parser_version).
    record_audit(
        db,
        user.id,
        "profile_import_applied",
        {
            "source_type": draft.source_type,
            "imported_at": datetime.now(UTC).isoformat(),
            "parser_version": PARSER_VERSION,
            "sections": sorted(sections),
            "mode": "replace" if payload.overwrite else "merge",
        },
    )

    db.commit()
    db.refresh(profile)
    return {"profile": serialize_profile(profile), "career": career_payload(user.id, db)}


async def parse_import_text(text: str, source_type: str) -> tuple[ImportProfileDraft, str]:
    result = await ai_provider.json_task(
        "import_profile.md",
        {"source_type": source_type, "text": text},
        smart=True,
    )
    draft = normalize_import_draft(result.data, text, source_type, result.model_used)
    return draft, result.model_used


def import_response(
    draft: ImportProfileDraft,
    model_used: str,
    document_kind: str | None = None,
) -> ImportProfileResponse:
    return ImportProfileResponse(
        draft=draft,
        model_used=model_used,
        document_kind=document_kind or draft.source_type,
        compliance_notice="We do not log into LinkedIn or scrape your account. You control what you upload or paste.",
    )


def clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def clean_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = value.replace(";", ",").split(",")
    elif isinstance(value, list):
        values = value
    else:
        return []
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


def should_apply(existing: Any, imported: str, overwrite: bool) -> bool:
    if not imported:
        return False
    existing_text = str(existing).strip() if existing is not None else ""
    return overwrite or not existing_text


def apply_profile_strings(
    profile: UserProfile,
    imported_values: dict[str, Any],
    fields: list[str],
    overwrite: bool,
) -> None:
    for field in fields:
        imported = clean_string(imported_values.get(field))
        if should_apply(getattr(profile, field), imported, overwrite):
            setattr(profile, field, imported)


def apply_string_list(existing: list[Any], imported: list[Any], overwrite: bool) -> list[str]:
    incoming = clean_string_list(imported)
    if overwrite:
        return incoming
    return list(dict.fromkeys(clean_string_list(existing) + incoming))


def parse_date(value: Any) -> date | None:
    raw = clean_string(value)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


# Fields that identify a "same" record for de-duplication. Two records that
# normalize to the same key are treated as duplicates and never stored twice.
_DEDUPE_FIELDS: dict[type[Any], tuple[str, ...]] = {
    Education: ("school", "degree", "major"),
    Experience: ("company", "title", "start_date", "end_date"),
    Project: ("name",),
    Certification: ("name",),
    Award: ("name",),
}


def _normalize_token(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _record_key(model: type[Any], source: Any) -> str:
    """Build a normalized dedupe key from a dict or an ORM row."""
    fields = _DEDUPE_FIELDS.get(model, ())

    def get(field: str) -> Any:
        return source.get(field) if isinstance(source, dict) else getattr(source, field, None)

    return "|".join(_normalize_token(get(field)) for field in fields)


def _dedupe_incoming(model: type[Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for record in records:
        key = _record_key(model, record)
        if key in seen:
            continue
        seen.add(key)
        result.append(record)
    return result


def apply_records(
    db: Session,
    user_id: int,
    model: type[Any],
    records: list[dict[str, Any]],
    overwrite: bool,
) -> None:
    """Persist imported records without ever creating duplicates.

    Replace (``overwrite=True``): clear the user's existing rows for this model
    and store the de-duplicated import cleanly. Merge (``overwrite=False``):
    keep existing rows and add only records that are not already present.
    """
    deduped = _dedupe_incoming(model, records)
    if overwrite:
        db.execute(delete(model).where(model.user_id == user_id))
        db.add_all([model(user_id=user_id, **record) for record in deduped])
        return
    existing = list(db.scalars(select(model).where(model.user_id == user_id)).all())
    seen = {_record_key(model, row) for row in existing}
    to_add: list[Any] = []
    for record in deduped:
        key = _record_key(model, record)
        if key in seen:
            continue
        seen.add(key)
        to_add.append(model(user_id=user_id, **record))
    db.add_all(to_add)


def dedupe_user_records(db: Session, user_id: int) -> int:
    """Remove duplicate career rows left by prior bad imports.

    Runs after every import apply so legacy duplicates (or duplicates the AI
    parser emitted) are collapsed regardless of replace/merge mode. Returns the
    number of rows deleted."""
    removed = 0
    for model in _DEDUPE_FIELDS:
        seen: set[str] = set()
        for row in db.scalars(select(model).where(model.user_id == user_id)).all():
            key = _record_key(model, row)
            if key in seen:
                db.delete(row)
                removed += 1
            else:
                seen.add(key)
    return removed


def _non_empty(records: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """Drop records whose identifying field is blank so we never persist
    placeholder rows like "Untitled project"."""
    return [record for record in records if clean_string(record.get(key))]


def _valid_projects(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only projects with a real name — drops blanks and bullet fragments
    (e.g. "validation states.") that a messy parser leaked in as project names."""
    return [
        record
        for record in records
        if clean_string(record.get("name")) and not looks_like_fragment(clean_string(record.get("name")))
    ]


def _non_empty_experience(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep an experience only if it has a company or a title. This prevents
    empty/fake experience rows from being saved."""
    return [
        record
        for record in records
        if clean_string(record.get("company")) or clean_string(record.get("title"))
    ]


def education_values(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "school": clean_string(item.get("school")),
        "degree": clean_string(item.get("degree")) or None,
        "major": clean_string(item.get("major")) or None,
        "minor": clean_string(item.get("minor")) or None,
        "start_date": parse_date(item.get("start_date")),
        "end_date": parse_date(item.get("end_date")),
        "gpa": clean_string(item.get("gpa")) or None,
        "honors": clean_string_list(item.get("honors")),
        "coursework": clean_string_list(item.get("coursework")),
    }


def experience_values(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "company": clean_string(item.get("company")),
        "title": clean_string(item.get("title")),
        "location": clean_string(item.get("location")) or None,
        "start_date": parse_date(item.get("start_date")),
        "end_date": parse_date(item.get("end_date")),
        "currently_working": bool(item.get("currently_working")),
        "bullets": clean_string_list(item.get("bullets")),
        "technologies": clean_string_list(item.get("technologies")),
        "measurable_impact": clean_string_list(item.get("measurable_impact")),
    }


def project_values(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": clean_string(item.get("name")),
        "description": clean_string(item.get("description")) or None,
        "bullets": clean_string_list(item.get("bullets")),
        "technologies": clean_string_list(item.get("technologies")),
        "links": clean_string_list(item.get("links")),
        "start_date": parse_date(item.get("start_date")),
        "end_date": parse_date(item.get("end_date")),
    }


def certification_values(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": clean_string(item.get("name")),
        "issuer": clean_string(item.get("issuer")) or None,
        "issue_date": parse_date(item.get("issue_date")),
        "expiration_date": parse_date(item.get("expiration_date")),
        "credential_url": clean_string(item.get("credential_url")) or None,
    }


def award_values(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": clean_string(item.get("name")),
        "issuer": clean_string(item.get("issuer")) or None,
        "date": parse_date(item.get("date")),
        "description": clean_string(item.get("description")) or None,
    }


def _serialize_demographics(record: SensitiveDemographics | None) -> dict | None:
    """Explicit allow-list. Never returns the legacy free-text columns, whose
    quarantined values (e.g. gender="yes") carry no meaning."""
    if record is None:
        return None
    return {
        "id": record.id,
        "user_id": record.user_id,
        "gender_identity": record.gender_identity,
        "gender_self_description": record.gender_self_description,
        "veteran_status": record.veteran_status,
        "disability_status": record.disability_status,
        "hispanic_or_latino": record.hispanic_or_latino,
        "race_ethnicity": record.race_ethnicity or [],
        "race_self_description": record.race_self_description,
        "consent_to_store": record.consent_to_store,
        "needs_review": record.needs_review,
    }


@router.get("/demographics")
def get_demographics(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    demographics = db.scalar(
        select(SensitiveDemographics).where(SensitiveDemographics.user_id == user.id)
    )
    return {"demographics": _serialize_demographics(demographics)}


@router.put("/demographics")
def upsert_demographics(
    payload: SensitiveDemographicsIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Save voluntary EEO answers.

    Consent is about STORING the data, so:
      * answers + no consent  -> 422. Pressing Save is not consent.
      * no answers + no consent -> withdrawal; stored values are deleted.
    """
    from app.profile.eeo import has_any_answer

    values = payload.model_dump()
    answered = has_any_answer(values)

    if not payload.consent_to_store:
        if answered:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Storing optional demographic information requires explicit consent. "
                    "Tick the consent box, or clear the answers to remove stored data."
                ),
            )
        # Withdrawing consent deletes whatever was stored.
        db.execute(delete(SensitiveDemographics).where(SensitiveDemographics.user_id == user.id))
        record_audit(db, user.id, "demographics_deleted", {"reason": "consent_withdrawn"})
        db.commit()
        return {"demographics": None}

    demographics = db.scalar(
        select(SensitiveDemographics).where(SensitiveDemographics.user_id == user.id)
    )
    # Free-text self-descriptions are kept only while their trigger option is
    # selected — deselecting "Self-describe" must not leave the text behind.
    if values.get("gender_identity") != "self_describe":
        values["gender_self_description"] = None
    if "another_race_or_ethnicity" not in (values.get("race_ethnicity") or []):
        values["race_self_description"] = None

    if demographics is None:
        demographics = SensitiveDemographics(user_id=user.id, **values)
        db.add(demographics)
    else:
        for key, value in values.items():
            setattr(demographics, key, value)
    # Answering clears the "please re-answer" flag set by the 0015 migration.
    demographics.needs_review = False
    record_audit(db, user.id, "demographics_saved", {"consented": True})
    db.commit()
    db.refresh(demographics)
    return {"demographics": _serialize_demographics(demographics)}


@router.delete("/demographics", status_code=status.HTTP_204_NO_CONTENT)
def delete_demographics(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    db.execute(delete(SensitiveDemographics).where(SensitiveDemographics.user_id == user.id))
    record_audit(db, user.id, "demographics_deleted")
    db.commit()
