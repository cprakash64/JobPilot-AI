# People data privacy and retention

Stored data is limited to professional identity, current professional role/company, professional
location, allowlisted public profile URL, source identity/provenance, freshness, structured
recommendation evidence, and—only after on-demand discovery—a verified professional work email.
Personal emails, phones, home addresses, LinkedIn page contents, and unnecessary raw provider
payloads are prohibited.

Verified email values are encrypted with a dedicated derived Fernet key and hashed with a separate
derived HMAC key for deduplication. Only verified values are decrypted into the owning user's API
response; accept-all, risky, and unknown addresses are not displayed as verified. Logs, metrics,
audit metadata, and analytics never contain names, URLs, emails, keys, or payloads.

Canonical candidates expire after `PEOPLE_RESULT_TTL_DAYS`; operators should schedule deletion of
expired, unreferenced provider data according to provider terms. User recommendation, run, feedback,
save, and contact state is exported through `/privacy/export` and cascades on `/privacy/account`.
Incorrect-information feedback suppresses that person for the reporting user. Shared school and
employer reasons are computed only inside that authenticated user's scope.

Audit events record email lookup status, draft generation (never content), and feedback. No
automated connection request, message, or email exists. Provider contractual review, privacy-policy
disclosure, data-processing agreements, retention jobs, and jurisdiction-specific legal review
remain deployment responsibilities; this document does not promise unsupported legal compliance.
