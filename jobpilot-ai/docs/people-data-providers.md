# People data providers

`PeopleDiscoveryProvider` exposes `search_people`, `enrich_people`, and `get_usage`.
`WorkEmailProvider` exposes `find_work_email` and `verify_work_email`. Business logic depends only
on these protocols.

- Apollo: primary professional people search/enrichment.
- Hunter: on-demand work-email finding and deliverability verification.
- People Data Labs: optional people fallback behind `PEOPLE_PDL_FALLBACK_ENABLED`.
- Mock: synthetic injected fixtures for tests/local development; empty by default.

Accounts and plans may omit fields or deny endpoints. Missing data is normal and lowers confidence;
it is never synthesized. Calls have bounded timeouts, no redirects, a response-size ceiling,
sanitized failure codes, and tightly bounded enrichment. Provider payloads and keys are never
logged or returned. Only normalized evidence, field provenance, stable provider identity, and an
empty redacted diagnostic object are retained.

No adapter scrapes LinkedIn. Public LinkedIn URLs supplied by a licensed provider are allowlisted
to HTTPS `linkedin.com/in/...` links and are never fetched server-side. Confirm that the applicable
provider agreement allows storage, display, and job-level reuse before enabling production.
