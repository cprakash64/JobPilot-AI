# Docker

Docker Compose at the repository root starts PostgreSQL, Redis, the FastAPI API, and the Next.js web app.

Production deployments should build immutable images, run migrations separately, and mount generated files on durable object storage instead of local volumes.

