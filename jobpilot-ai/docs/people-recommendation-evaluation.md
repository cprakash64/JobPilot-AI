# People recommendation evaluation

Run:

```text
make evaluate-people
```

The runner validates a versioned synthetic/redacted benchmark and writes
`evaluation/people_recommendations/results/latest.json` and `latest.md`. It exits non-zero when a
release target is missed. Incomplete fixture development may use `--allow-incomplete` or
`PEOPLE_EVAL_ALLOW_INCOMPLETE=true`; release/CI must not.

Human labels:

- Relevant: the evidence supports the category for the specific company/job.
- Irrelevant: unrelated function, company, or seniority.
- Current/stale employment: reliable evidence supports current work, or contradicts/is too old.
- Likely recruiter: relevant internal recruiting specialization; not necessarily assigned.
- Potential hiring manager: manager/director/head in the relevant function; not necessarily actual.
- Reasonable referral candidate: relevant current employee with team/role or verified shared evidence.
- Unsupported claim: a displayed reason/label lacks structured source evidence.

For manually labeled real-job cases, remove direct identifiers, replace IDs with stable opaque IDs,
record labeling date/source class, and conform to `benchmark.schema.json`. Never commit names,
emails, profile URLs, raw provider payloads, or production IDs. Use `--benchmark PATH` to import.
Two reviewers should independently label ambiguous cases and adjudicate disagreements.
