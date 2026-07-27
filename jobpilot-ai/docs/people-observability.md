# People recommendation observability

The repository has no Prometheus/OpenTelemetry client. `app.people.observability` therefore emits
safe structured events as `metric=<name> value=<number>` plus allowlisted low-cardinality
dimensions (`provider`, `category`, `status`, `scoring_version`). Configure the deployment log
pipeline to convert these events into counters/histograms and dashboard:

- request volume, cache-hit ratio, provider-error ratio, p50/p95 discovery duration;
- candidates found versus displayed by category/scoring version;
- enrichments and credits by provider;
- email find/verified/not-found rates;
- feedback and reported-incorrect rates.

Alert on a sustained provider-error ratio, exhausted credit budget, p95 latency above the provider
timeout envelope, a sharp drop in displayed candidates, or an increase in reported-incorrect
feedback. Metric dimensions cannot contain names, emails, URLs, provider IDs, job IDs, user IDs,
keys, or raw payload fields.
