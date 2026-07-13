"""Guardrails that keep generated documents truthful.

Every fact in a generated resume must trace back to the user's saved profile:
companies, titles, schools, certifications, skills, and any numeric metric. This
service builds a fact set from the profile payload and strips (or flags)
anything a generator produced that the profile does not support. It never adds
facts -- it only removes unsupported ones.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


@dataclass
class ProfileFacts:
    companies: set[str] = field(default_factory=set)
    titles: set[str] = field(default_factory=set)
    schools: set[str] = field(default_factory=set)
    project_names: set[str] = field(default_factory=set)
    certifications: set[str] = field(default_factory=set)
    skills: set[str] = field(default_factory=set)
    numbers: set[str] = field(default_factory=set)
    work_authorization: str = ""
    raw_text: str = ""

    def supports_skill(self, skill: str) -> bool:
        low = skill.strip().lower()
        return bool(low) and (low in self.skills or low in self.raw_text)

    def supports_number(self, number: str) -> bool:
        # A metric is allowed only if the exact number appears somewhere in the
        # profile (so a rewritten bullet cannot invent "43%").
        return number in self.numbers


def build_profile_facts(payload: dict[str, Any]) -> ProfileFacts:
    facts = ProfileFacts()
    profile = payload.get("profile") or {}
    text_parts: list[str] = []

    for value in profile.get("skills") or []:
        facts.skills.add(str(value).lower())
    facts.work_authorization = str(profile.get("work_authorization") or "").strip()

    for item in payload.get("experience", []):
        if item.get("company"):
            facts.companies.add(str(item["company"]).lower())
        if item.get("title"):
            facts.titles.add(str(item["title"]).lower())
        for tech in item.get("technologies") or []:
            facts.skills.add(str(tech).lower())
    for item in payload.get("projects", []):
        if item.get("name"):
            facts.project_names.add(str(item["name"]).lower())
        for tech in item.get("technologies") or []:
            facts.skills.add(str(tech).lower())
    for item in payload.get("education", []):
        if item.get("school"):
            facts.schools.add(str(item["school"]).lower())
    for item in payload.get("certifications", []):
        if item.get("name"):
            facts.certifications.add(str(item["name"]).lower())

    # Collect all text + numbers for skill/metric support checks.
    for section in ["experience", "projects", "education", "certifications", "awards"]:
        for item in payload.get(section, []):
            for value in item.values():
                if isinstance(value, str):
                    text_parts.append(value)
                elif isinstance(value, list):
                    text_parts.extend(str(entry) for entry in value)
    for value in profile.values():
        if isinstance(value, str):
            text_parts.append(value)
        elif isinstance(value, list):
            text_parts.extend(str(entry) for entry in value)

    facts.raw_text = " ".join(text_parts).lower()
    facts.numbers = set(_NUMBER_RE.findall(facts.raw_text))
    return facts


def validate_resume(content: dict[str, Any], facts: ProfileFacts) -> tuple[dict[str, Any], list[str]]:
    """Return (cleaned_content, unsupported_claims_removed)."""
    removed: list[str] = []
    clean = dict(content)

    # Header: never assert a work authorization the profile does not state.
    header = dict(clean.get("header") or {})
    if header.get("work_authorization") and not facts.work_authorization:
        removed.append(f"work authorization: {header['work_authorization']}")
        header["work_authorization"] = ""
    clean["header"] = header

    # Skills: keep only profile-supported skills.
    kept_skills = []
    for skill in clean.get("skills") or []:
        if facts.supports_skill(str(skill)):
            kept_skills.append(skill)
        else:
            removed.append(f"skill: {skill}")
    clean["skills"] = kept_skills

    clean["experience"] = _clean_entries(
        clean.get("experience") or [], facts, removed, name_key="company", role_key="title",
        valid_names=facts.companies, valid_roles=facts.titles, label="company",
    )
    clean["projects"] = _clean_entries(
        clean.get("projects") or [], facts, removed, name_key="name", role_key=None,
        valid_names=facts.project_names, valid_roles=None, label="project",
    )
    clean["education"] = _clean_named(clean.get("education") or [], "school", facts.schools, removed, "school")
    clean["certifications"] = _clean_named(
        clean.get("certifications") or [], "name", facts.certifications, removed, "certification"
    )
    return clean, removed


def _clean_entries(entries, facts, removed, *, name_key, role_key, valid_names, valid_roles, label):
    cleaned = []
    for entry in entries:
        name = str(entry.get(name_key) or "").lower()
        if valid_names and name and name not in valid_names:
            removed.append(f"{label}: {entry.get(name_key)}")
            continue
        if role_key and valid_roles:
            role = str(entry.get(role_key) or "").lower()
            if role and role not in valid_roles:
                removed.append(f"title: {entry.get(role_key)}")
                continue
        new_entry = dict(entry)
        new_entry["bullets"] = _clean_bullets(entry.get("bullets") or [], facts, removed)
        cleaned.append(new_entry)
    return cleaned


def _clean_bullets(bullets, facts, removed) -> list[str]:
    kept = []
    for bullet in bullets:
        text = str(bullet)
        invented = [n for n in _NUMBER_RE.findall(text) if not facts.supports_number(n)]
        if invented:
            removed.append(f"metric: {text}")
            continue
        kept.append(bullet)
    return kept


def _clean_named(entries, key, valid, removed, label) -> list[Any]:
    cleaned = []
    for entry in entries:
        if isinstance(entry, dict):
            name = str(entry.get(key) or "").lower()
            if name and valid and name not in valid:
                removed.append(f"{label}: {entry.get(key)}")
                continue
        cleaned.append(entry)
    return cleaned


def missing_job_skills(job_required: list[str], facts: ProfileFacts) -> list[str]:
    """Job-required skills the profile does not support (surfaced as warnings)."""
    return [skill for skill in job_required if not facts.supports_skill(str(skill))]
