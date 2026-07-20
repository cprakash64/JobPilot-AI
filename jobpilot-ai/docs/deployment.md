# Deployment

The MVP runs locally with Docker Compose. Production deployments should use
managed PostgreSQL, managed Redis, TLS termination, secret management,
structured logs, and object storage for generated files.

---

## 1. Required production settings

Set `APP_ENV=production`. This is not cosmetic — it activates
`app/core/config_validation.py`, which **refuses to start** when any of these is
true:

| Setting | Rejected when |
| --- | --- |
| `SECRET_KEY` | missing, a documented dev default, under 32 chars, or low-variation |
| `DEMOGRAPHICS_ENCRYPTION_KEY` | empty while `DEMOGRAPHICS_ENCRYPTION_REQUIRED=true` |
| `CORS_ORIGINS` | empty, `*`, or a non-localhost plaintext `http://` origin |
| `DATABASE_URL` | missing, SQLite, no password, or a known dev password |
| `DEBUG` | `true` |

Startup aborts rather than warns. The app runs *fine* with the shipped signing
key right up until someone forges a token for another user, so a warning would
simply be ignored.

Generate a real key with `openssl rand -base64 48` and supply it as a
deployment secret. Never commit it — `.env.example` holds placeholders only.
The error raised on a bad config names the offending **settings**, never their
values, because that message goes to logs and crash reporters.

Also required, unchanged from before: production database URL, Redis URL,
OpenAI key and approved model names if AI generation is enabled, an encryption
strategy for sensitive demographics, and upload malware scanning.

API docs (`/docs`, `/redoc`, `/openapi.json`) are disabled automatically when
`APP_ENV` is production — the schema is free reconnaissance. Override with
`DOCS_ENABLED=true` only behind authentication.

Secrets are additionally scrubbed from logs by `app/core/log_redaction.py`
(URL userinfo, `key=value` secrets, bearer and provider tokens). That is a
safety net, not permission to log secrets.

---

## 2. Health endpoints

| Endpoint | Checks | Use for |
| --- | --- | --- |
| `GET /healthz` | process is alive; **no** external dependency | liveness probe, container `HEALTHCHECK` |
| `GET /readyz` | PostgreSQL reachable **and** schema at head; Redis probed and reported | readiness probe |

`GET /health` is an alias of `/healthz` for existing callers.

**Do not point a liveness probe at `/readyz`.** A database blip would fail
liveness on every replica simultaneously and the orchestrator would restart the
whole API fleet, turning a partial outage into a total one.

`/readyz` returns `503` with a sanitized body when not ready: booleans, revision
ids and exception *type* names only. A driver error stringifies to the full DSN
including the password, and this endpoint is unauthenticated.

Redis is reported but **not gating** — ingestion degrades to inline scoring
without a broker, so the API still serves every request correctly. Pulling the
replica from the load balancer would be a self-inflicted outage.

Kubernetes:

```yaml
livenessProbe:
  httpGet: { path: /healthz, port: 8000 }
  initialDelaySeconds: 10
  periodSeconds: 15
readinessProbe:
  httpGet: { path: /readyz, port: 8000 }
  initialDelaySeconds: 5
  periodSeconds: 10
  failureThreshold: 3
```

Docker Compose wires the container `HEALTHCHECK` to `/healthz`, and `web` waits
on `api: condition: service_healthy`. No dependency cycle:
`web → api → {postgres, redis}`.

---

## 3. Migration policy

**Migrations are a release step, not a startup step.**

`RUN_MIGRATIONS_ON_STARTUP` defaults to `false`. Compose sets it `true` for
local convenience, and `scripts/start-api.sh` **refuses** the combination
`RUN_MIGRATIONS_ON_STARTUP=true` + `APP_ENV=production`.

Why: with N replicas, startup migration means N concurrent `alembic upgrade
head` runs against one database. Alembic's lock prevents corruption, but the
losers block on boot and a migration failure surfaces as a crash-looping replica
instead of a halted deployment. The worker and scheduler never migrate — they
run `celery` directly and are pinned to `RUN_MIGRATIONS_ON_STARTUP=false`.

### Release sequence

```bash
# 0. Verify exactly one head before anything else.
alembic heads            # must print exactly one revision
alembic current          # what production is on now — record this

# 1. BACK UP. This is the only real rollback (see §4).
pg_dump --format=custom --no-owner --no-privileges \
        --file "backup-$(date -u +%Y%m%dT%H%M%SZ).dump" "$DATABASE_URL"

# 2. Verify the backup is restorable, not merely present.
pg_restore --list backup-*.dump > /dev/null && echo "backup readable"

# 3. Apply migrations as ONE step, before any new replica starts.
alembic upgrade head     # non-zero exit ⇒ STOP, do not roll replicas

# 4. Confirm.
alembic current          # must equal `alembic heads`

# 5. Roll the API replicas, then worker and scheduler.
```

A non-zero exit at step 3 stops the deployment. Do not continue to step 5: the
old code is still running against the old schema and is serving fine.

Do not delete production database volumes to recover from a migration failure,
and do not run destructive migrations automatically unless that behaviour is
explicitly designed, reviewed and configured.

### Compatibility during the roll — `0013_name_parts_phone`

This release is additive **plus a column rename** (`given_name→first_name`,
`family_name→last_name`). During a rolling deploy old replicas briefly run
against the new schema and will error on profile reads. For **this release
only**, either take a short maintenance window or deploy with
`maxSurge=0, maxUnavailable=100%` (recreate rather than roll). Later additive
releases roll normally.

---

## 4. Rollback

**`alembic downgrade` is not a rollback.** It is verified to run, but it is
**not data-safe** for 0013. Tested on a disposable PostgreSQL instance:

* `middle_name`, `preferred_first_name`, `preferred_last_name` and all four
  `phone_*` columns are **dropped** — their contents are gone.
* Name confirmations the upgrade withdrew are **not restored**; the upgrade
  deleted those values deliberately and the downgrade cannot invent them.

Downgrade therefore returns the *schema* to 0012 while permanently losing data
written since the upgrade.

### Recovery order

1. **Roll back the application first.** Redeploy the previous image. With the
   schema still at 0013 most reads keep working, since 0013 is additive apart
   from the rename.
2. **If the schema must be reverted**, restore the §3 step-1 backup:

   ```bash
   # Stop the replicas / enter maintenance mode first.
   pg_restore --clean --if-exists --no-owner --no-privileges \
              --dbname "$DATABASE_URL" backup-<timestamp>.dump
   alembic current   # confirm the revision matches the backup
   ```

   Everything written between the backup and the restore is lost. That is the
   real cost of a rollback, and why step 1 is strongly preferred.
3. Use `alembic downgrade` **only** against a disposable/dev database.

### Never

* Do not edit or renumber an applied revision (0001–0013). Alembic stores the
  revision id; renaming one it has already applied makes the chain unresolvable
  (`Can't locate revision`). Add a new forward-only revision instead.
* Do not create a second head. `alembic heads` must print exactly one line.

---

## 5. Migration-history note (0009)

`0009_score_states_and_ingestion_runs.py` was renamed to
`0009_scoring_pipeline.py`, changing the revision id. The files are
**byte-identical apart from that id** — every schema operation matches — so it
is a pure rename, not a semantic change, and no corrective revision is needed.

It is safe because the chain resolves (`0008 → 0009_scoring_pipeline → 0010 →
… → 0013`) and production already sits at `0013_name_parts_phone`; Alembic only
stores the current revision, so the historical id is never consulted.

**Caveat:** any *other* database still at
`0009_score_states_and_ingestion_runs` (an old staging box, a teammate's local
volume) cannot upgrade — Alembic reports `Can't locate revision`. Repair it by
pointing the row at the new id, which is safe precisely because the two
revisions apply identical DDL:

```sql
UPDATE alembic_version SET version_num = '0009_scoring_pipeline'
 WHERE version_num = '0009_score_states_and_ingestion_runs';
```

---

## 6. Services

`api`, `worker` and `scheduler` all build from the **same** `apps/api` context,
so they share one image and one dependency set. Rebuild and roll all three
together — otherwise a newly added dependency (`phonenumbers`, in this release)
lands in one service and is missing from another.

| Service | Command | Migrates? |
| --- | --- | --- |
| api | `scripts/start-api.sh` → uvicorn | dev only |
| worker | `celery … worker` | never |
| scheduler | `celery … beat` | never |

Run exactly **one** scheduler replica; multiple beat processes duplicate
scheduled runs (ingestion is additionally guarded by a distributed lock).

The image runs as non-root (`uid 10001`), excludes the test suite, and installs
from a fully pinned `requirements.txt`. `worker` has a 30s
`stop_grace_period` so an in-flight scoring batch finishes on SIGTERM.

---

## 7. Pre-release checklist

```bash
alembic heads                                  # exactly one
cd apps/api    && pytest -q
cd apps/web    && npm test && npm run typecheck && npm run lint
cd apps/extension && npm test && npm run typecheck && npm run build
docker build -t jobpilot-api ./apps/api
docker build -t jobpilot-web ./apps/web
```
