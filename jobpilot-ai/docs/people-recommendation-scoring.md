# People recommendation scoring

Scoring version `people-v1` lives in `app/people/scoring.py`. Recruiter, potential-manager, and
referral relevance use separate centralized weights. Title matching uses normalized token overlap
against expanded role-family titles, not exact strings. Company/domain, department, role, location,
freshness, quality, shared school/employer evidence, and appropriate seniority are category-specific
inputs.

Ranking and confidence are deliberately separate. Confidence considers a safe profile identity,
employment/source freshness, company-domain evidence, corroboration, and conflicts. UI labels are:
high (`>= .78`), moderate (`>= .55`), and limited. Candidates below
`PEOPLE_MIN_RELEVANCE_SCORE` or confidence `.50` are suppressed.

Reasons and limitations come from structured evidence templates. “Potential hiring manager” always
includes that exact responsibility/team membership is unconfirmed. Referral candidates always note
that willingness to refer is unknown. Changing weights requires a scoring-version bump, benchmark
run, and documented rollout.
