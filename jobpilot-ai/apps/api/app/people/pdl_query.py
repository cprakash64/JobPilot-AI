"""Progressive, bounded PDL Person Search query construction.

The original query required exact job-title string equality:

    job_title IN ('Recruiter', 'Technical Recruiter', ...)

That works for a company large enough that someone holds one of those exact
strings, and fails completely for everyone else — which is why Cisco returned
people and Toshiba Global Commerce and Vanderbilt Health returned nothing.

This module replaces exact-title equality with PDL's canonical taxonomy
(``job_title_role``, ``job_title_sub_role``, ``job_title_levels``) and adds a
bounded ladder that relaxes *title* precision one step at a time. The company
constraint is never relaxed: every strategy pins the search to a verified PDL
company id, or to a verified website/name when no id resolved.

Canonical values are taken from PDL's published taxonomy (v28.0+):
roles https://docs.peopledatalabs.com/docs/job-title-roles,
sub-roles https://docs.peopledatalabs.com/docs/job-title-subroles,
levels https://docs.peopledatalabs.com/docs/job-title-levels.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.people.schemas import PeopleCategory

PDL_QUERY_LADDER_VERSION = "pdl-progressive-search-v1"

# --- Canonical PDL taxonomy -------------------------------------------------

# Roles that plausibly own technical hiring or technical individual work.
_RECRUITER_ROLES = ("human_resources",)
_RECRUITER_SUB_ROLES = ("recruiting", "talent_analytics")

_ENGINEERING_ROLES = ("engineering",)
_ENGINEERING_SUB_ROLES = (
    "software",
    "devops",
    "qa_engineering",
    "web",
    "data_engineering",
)
_DATA_SUB_ROLES = ("data_engineering", "data_science", "data_analyst")

# job_title_levels values that indicate people-management responsibility.
_MANAGER_LEVELS = ("manager", "director", "vp")
_IC_LEVELS = ("entry", "senior", "training")

# Role families mapped onto PDL roles/sub-roles. Anything unmapped falls back
# to engineering, which is what the People feature is scoped to today.
_ROLE_FAMILY_TO_PDL: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "software_engineering": (_ENGINEERING_ROLES, _ENGINEERING_SUB_ROLES),
    "machine_learning": (_ENGINEERING_ROLES, ("software", "data_science", "data_engineering")),
    "embedded_systems": (_ENGINEERING_ROLES, ("software", "devops")),
    "data": (_ENGINEERING_ROLES, _DATA_SUB_ROLES),
    "security": (_ENGINEERING_ROLES, ("software", "devops")),
    "product": (("product",), ()),
    "finance": (("finance",), ()),
    "marketing": (("marketing",), ()),
    "sales": (("sales", "sales_engineering"), ()),
    "healthcare": (("health", "engineering"), ()),
}


def pdl_roles_for(role_family: str | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return _ROLE_FAMILY_TO_PDL.get(
        role_family or "", (_ENGINEERING_ROLES, _ENGINEERING_SUB_ROLES)
    )


# --- SQL value sanitation ---------------------------------------------------


def sql_value(value: object) -> str:
    """Sanitize a value for a PDL SQL literal.

    The allowlist is deliberately narrow and quotes are doubled, so no caller
    can inject a clause through a company name or job title.
    """

    cleaned = re.sub(r"[^A-Za-z0-9 /,&+().:_-]", "", str(value or "")).strip()[:120]
    return cleaned.replace("'", "''")


def _in_clause(field_name: str, values: tuple[str, ...] | list[str]) -> str | None:
    safe = [sql_value(value) for value in values if sql_value(value)]
    if not safe:
        return None
    joined = ",".join(f"'{value}'" for value in safe)
    return f"{field_name} IN ({joined})"


# --- Strategies -------------------------------------------------------------


@dataclass(frozen=True)
class PdlSearchStrategy:
    """One rung of the ladder: a name, a SQL WHERE body, and its precision."""

    name: str
    sql: str
    # Whether results still need a local exact-company check. They always do,
    # but a website/name-scoped search needs a stricter one.
    company_binding: str
    precision: float
    size: int

    def safe_summary(self) -> dict[str, object]:
        return {
            "strategy": self.name,
            "company_binding": self.company_binding,
            "precision": self.precision,
            "size": self.size,
            "sql": self.sql,
        }


@dataclass
class LadderInputs:
    """Everything the ladder needs, already verified."""

    pdl_company_id: str | None
    verified_domain: str | None
    pdl_company_name: str | None
    raw_company_name: str
    aliases: tuple[str, ...] = ()
    role_family: str | None = None
    location_region: str | None = None
    location_country: str | None = None
    exact_titles: tuple[str, ...] = ()
    size: int = 10
    location_required: bool = False
    extra: dict[str, object] = field(default_factory=dict)


def _company_clauses(inputs: LadderInputs) -> list[tuple[str, str]]:
    """Company constraints, strongest first.

    Empty unless there is *verified* company evidence — a PDL company id or a
    verified domain. A bare display name off a job feed is not evidence: an
    unpinned search returns strangers who merely work somewhere similarly
    named, which is worse than returning nobody.
    """

    clauses: list[tuple[str, str]] = []
    if inputs.pdl_company_id:
        clauses.append(
            ("pdl_company_id", f"job_company_id='{sql_value(inputs.pdl_company_id)}'")
        )
    if inputs.verified_domain:
        clauses.append(
            (
                "verified_domain",
                f"job_company_website='{sql_value(inputs.verified_domain)}'",
            )
        )
    if not clauses:
        return []
    # A canonical-name clause is only ever a *fallback rung*, never the sole
    # company constraint, so it is appended only alongside verified evidence.
    name_values: list[str] = []
    for value in (inputs.pdl_company_name, inputs.raw_company_name, *inputs.aliases):
        if value and value not in name_values:
            name_values.append(value)
    name_clause = _in_clause("job_company_name", name_values[:6])
    if name_clause:
        clauses.append(("verified_name", name_clause))
    return clauses


def _location_clause(inputs: LadderInputs) -> str | None:
    """Location is a filter only when explicitly configured as required.

    PDL person records carry the person's own location, which for a distributed
    employer is frequently not the job's city. Filtering on it removes the very
    people the search exists to find, so by default it does not appear in the
    query at all and is used for local ranking instead.
    """

    if not inputs.location_required:
        return None
    if inputs.location_region:
        return f"location_region='{sql_value(inputs.location_region)}'"
    if inputs.location_country:
        return f"location_country='{sql_value(inputs.location_country)}'"
    return None


def _category_title_clauses(
    category: PeopleCategory, inputs: LadderInputs, *, level: int
) -> list[str]:
    """Title constraints for a category at a given relaxation level.

    level 0 — canonical sub-role plus level (highest precision)
    level 1 — canonical role plus level
    level 2 — canonical role only
    """

    roles, sub_roles = pdl_roles_for(inputs.role_family)
    clauses: list[str] = []
    if category == "likely_recruiter":
        if level == 0:
            clause = _in_clause("job_title_sub_role", _RECRUITER_SUB_ROLES)
            if clause:
                clauses.append(clause)
        else:
            clause = _in_clause("job_title_role", _RECRUITER_ROLES)
            if clause:
                clauses.append(clause)
        return clauses
    if category == "potential_hiring_manager":
        role_clause = (
            _in_clause("job_title_sub_role", sub_roles)
            if level == 0 and sub_roles
            else _in_clause("job_title_role", roles)
        )
        if role_clause:
            clauses.append(role_clause)
        if level <= 1:
            level_clause = _in_clause("job_title_levels", _MANAGER_LEVELS)
            if level_clause:
                clauses.append(level_clause)
        return clauses
    # potential_referrer
    role_clause = (
        _in_clause("job_title_sub_role", sub_roles)
        if level == 0 and sub_roles
        else _in_clause("job_title_role", roles)
    )
    if role_clause:
        clauses.append(role_clause)
    if level == 0:
        level_clause = _in_clause("job_title_levels", _IC_LEVELS)
        if level_clause:
            clauses.append(level_clause)
    return clauses


def build_ladder(
    category: PeopleCategory, inputs: LadderInputs, *, max_strategies: int
) -> list[PdlSearchStrategy]:
    """Build the bounded relaxation ladder for one category.

    Every rung keeps a company constraint. Only title precision — and, at the
    last rung, the company binding's strength — relaxes.
    """

    company_clauses = _company_clauses(inputs)
    if not company_clauses:
        return []
    strongest_binding, strongest_clause = company_clauses[0]
    location = _location_clause(inputs)
    strategies: list[PdlSearchStrategy] = []

    seen_sql: set[str] = set()

    def add(name: str, binding: str, company_sql: str, title_clauses: list[str],
            precision: float, *, include_location: bool) -> None:
        parts = [company_sql, *title_clauses]
        if include_location and location:
            parts.append(location)
        sql = "SELECT * FROM person WHERE " + " AND ".join(parts)
        if sql in seen_sql:
            # Two relaxation levels can collapse to the same query (a recruiter
            # search has no level constraint to drop). Re-issuing it would spend
            # a call to learn nothing.
            return
        seen_sql.add(sql)
        strategies.append(
            PdlSearchStrategy(
                name=name,
                sql=sql,
                company_binding=binding,
                precision=precision,
                size=inputs.size,
            )
        )

    # 1. Highest precision: strongest company binding + canonical sub-role.
    add(
        "exact_company_subrole",
        strongest_binding,
        strongest_clause,
        _category_title_clauses(category, inputs, level=0),
        precision=1.0,
        include_location=True,
    )
    # 2. Same company binding, broader title family, never location-filtered.
    add(
        "exact_company_role",
        strongest_binding,
        strongest_clause,
        _category_title_clauses(category, inputs, level=1),
        precision=0.85,
        include_location=False,
    )
    # 3. Same company binding, role only — no level constraint at all.
    add(
        "exact_company_role_any_level",
        strongest_binding,
        strongest_clause,
        _category_title_clauses(category, inputs, level=2),
        precision=0.7,
        include_location=False,
    )
    # 4. Fall back to the next-strongest *verified* company binding. This is
    #    still an exact-company constraint, just expressed by website or
    #    canonical name because the id search found nobody.
    if len(company_clauses) > 1:
        binding, clause = company_clauses[1]
        add(
            f"{binding}_role",
            binding,
            clause,
            _category_title_clauses(category, inputs, level=2),
            precision=0.6,
            include_location=False,
        )
    return strategies[: max(1, max_strategies)]


def exact_title_strategy(
    inputs: LadderInputs, titles: tuple[str, ...]
) -> PdlSearchStrategy | None:
    """The legacy exact-title query, kept for comparison and diagnostics."""

    company_clauses = _company_clauses(inputs)
    title_clause = _in_clause("job_title", list(titles)[:20])
    if not company_clauses or not title_clause:
        return None
    binding, clause = company_clauses[0]
    return PdlSearchStrategy(
        name="legacy_exact_titles",
        sql=f"SELECT * FROM person WHERE {clause} AND {title_clause}",
        company_binding=binding,
        precision=1.0,
        size=inputs.size,
    )
