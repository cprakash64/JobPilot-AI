# People Who Can Help

This beta feature adds evidence-based professional contacts to authenticated jobs. Every `/jobs`
card contains a compact `PeopleWhoCanHelpSummary`; it performs no request when rendered and loads
or discovers only after explicit user action. **View all people** opens the existing details modal,
which contains the full shared component. The standalone `/jobs/{id}` route also reuses the full
component for direct links. Results show likely recruiters, potential hiring managers, and
potential referral candidates. Those labels express relevance, not a verified assignment or
willingness to help. Empty categories are intentional when evidence is weak.

## Architecture and data flow

The Jobs API card's `id` is the persisted `JobPosting.id`. The modal passes that value directly to
the shared people component, which calls authenticated `/jobs/{job_id}/people` endpoints. The API
first reuses fresh database candidates. On explicit discovery, it builds a deterministic job
search profile, validates the company domain, searches each category through a provider-neutral
interface, preliminarily ranks records, enriches only a capped set, deduplicates conservatively,
saves provenance, and creates user-owned recommendation rows. Redis `SET NX` coalesces concurrent
identical requests; the database is the durable cache and run/cost ledger. Results expire after
`PEOPLE_RESULT_TTL_DAYS`.

The card component does not issue `GET` requests for the jobs in the result list. Its first click
loads `GET /jobs/{job_id}/people`; only a `not_started` response causes the same explicit action to
send `POST /jobs/{job_id}/people/discover`. A short-lived user-and-job keyed browser cache shares
results between the compact card and modal and coalesces simultaneous reads/discovery calls.

No LLM creates or identifies people. No LinkedIn page is scraped or fetched. The extension is not
involved. Outreach drafts are grounded templates, require review, and are never sent.

## Setup and local mode

Apply `0020_people_who_can_help`, configure the variables in `.env.example`, and restart the API.
Real discovery requires an enabled Apollo account (or explicitly enabled PDL fallback) with the
fields/endpoints allowed by that account. Email discovery requires Hunter permissions and a
dedicated `PEOPLE_DATA_ENCRYPTION_KEY`.

`PEOPLE_PRIMARY_PROVIDER=mock` is accepted only in `development` or `test`; mock records are
injected by tests and it returns no people by default. This ensures local mode never invents a
person. Provider keys are read by the backend only.

### Rollout scenarios

- Internal recommendations without email: set `PEOPLE_RECOMMENDATIONS_ENABLED=true`,
  `PEOPLE_ROLLOUT_MODE=internal`, `PEOPLE_INTERNAL_EMAILS`, `PEOPLE_PRIMARY_PROVIDER`, the matching
  backend provider key (`APOLLO_API_KEY`, or `PDL_API_KEY` when configured), and explicit non-zero
  `PEOPLE_DAILY_CREDIT_BUDGET` and `PEOPLE_PER_USER_DAILY_LIMIT`. Leave
  `PEOPLE_EMAIL_DISCOVERY_ENABLED=false`.
- Internal recommendations with email: use the preceding variables, then set
  `PEOPLE_EMAIL_DISCOVERY_ENABLED=true`, `HUNTER_API_KEY`, and a dedicated
  `PEOPLE_DATA_ENCRYPTION_KEY`.
- Selected beta users: set `PEOPLE_ROLLOUT_MODE=beta` and populate `PEOPLE_BETA_USER_IDS` with
  persisted user IDs, in addition to provider and budget configuration.
- Public rollout: set `PEOPLE_ROLLOUT_MODE=all` only after provider keys, the required encryption
  key for email, and explicit budgets are configured. Percentage rollout is also available through
  `PEOPLE_ROLLOUT_MODE=percentage` and `PEOPLE_ROLLOUT_PERCENTAGE`.

At startup the API logs only enabled/configured booleans, rollout mode, and environment. It never
logs provider keys, the encryption key, provider record IDs, email values, or raw provider errors.
The client always renders the section and maps global disable, cohort exclusion, configuration
unavailability, provider failure, empty results, and request errors to distinct safe messages.

## Cache, cost, failure, and rollback

Fresh results avoid provider calls. Per-category search caps, a top-enrichment cap, per-user/global
daily budgets, endpoint rate limits, provider timeouts/response limits, and a Redis lock bound cost.
Provider failures return safe partial or unavailable states. To roll back immediately, set
`PEOPLE_RECOMMENDATIONS_ENABLED=false`; disable email/outreach/PDL independently. Downgrade the
migration only after confirming feature data may be deleted.

LinkedIn actions are rendered only for provider-evidence URLs that parse as HTTPS LinkedIn
`/in/` profiles, and open with `noopener noreferrer`; the client never constructs or scrapes a
profile URL. Work-email lookup is a separate explicit action. Only backend-verified professional
emails are displayed; accept-all, risky, unknown, not-found, budget, and provider-error states do
not expose an address.

Run `make test-api`, `make test-web`, `make test-extension`, and `make evaluate-people`.
Known limitation: the first release uses deterministic role families and the provider fields
available to the configured account; an empty result is preferred to weak inference.
