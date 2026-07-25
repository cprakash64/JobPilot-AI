FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# build-essential is only needed to compile wheels during pip install and is
# removed afterwards, so the runtime image ships no compiler toolchain.
# curl stays for the container HEALTHCHECK below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first: this layer stays cached until requirements.txt changes.
# requirements.txt is fully pinned (==) so the same commit rebuilds to the same
# dependency set.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./alembic.ini
COPY scripts/ ./scripts/

# The test suite is not part of the runtime image: dead weight, and it carries
# fixtures/helpers that should not be reachable in production. .dockerignore
# also excludes it; this is belt-and-braces against a stale build context.
RUN rm -rf ./app/tests

# Non-root runtime. Writable directories are created and chowned BEFORE the
# switch so the docker-compose named volumes stay writable.
RUN useradd --create-home --uid 10001 jobpilot \
    && chmod +x /app/scripts/start-api.sh /app/scripts/container-entrypoint.sh \
    && mkdir -p /app/generated /app/uploads \
    && chown -R jobpilot:jobpilot /app

# Start as root only long enough for the entrypoint to repair named-volume
# ownership; it immediately execs the requested process as uid/gid 10001.
ENTRYPOINT ["/app/scripts/container-entrypoint.sh"]

EXPOSE 8000

# Liveness only. /healthz touches no database, so a database blip cannot make
# Docker restart otherwise-healthy API containers.
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

CMD ["sh", "/app/scripts/start-api.sh"]
