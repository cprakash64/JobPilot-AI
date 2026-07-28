# People recommendation scoring

Scoring version `people-v2` and deterministic title ontology `people-title-v2` live in
`app/people/scoring.py` and `app/people/title_ontology.py`. Recruiter, potential-manager, and
referral relevance use separate centralized weights. Title matching uses normalized token overlap
against expanded role-family titles, not exact strings. Company/domain, department, role, location,
freshness, quality, shared school/employer evidence, and appropriate seniority are category-specific
inputs.

Ranking and confidence are deliberately separate. Confidence considers a safe profile identity,
employment/source freshness, company-domain evidence, corroboration, and conflicts. UI labels are:
high (`>= .78`), moderate (`>= .55`), and limited. Candidates below
their category relevance threshold or `PEOPLE_MIN_DATA_CONFIDENCE` are suppressed. Exact canonical
domains, evidence-backed aliases, controlled related parent domains, and weak name-only matches are
scored distinctly; a generic parent-company employee is not accepted merely because one brand
token overlaps.

Reasons and limitations come from structured evidence templates. “Potential hiring manager” always
includes that exact responsibility/team membership is unconfirmed. Referral candidates always note
that willingness to refer is unknown. Changing weights requires a scoring-version bump, benchmark
run, and documented rollout.
