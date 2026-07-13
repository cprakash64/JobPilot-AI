# API

Base URL: `http://localhost:8000`

Important endpoints:

- `POST /auth/signup`
- `POST /auth/login`
- `GET /auth/me`
- `GET /profile`
- `PUT /profile`
- `PUT /profile/career`
- `PUT /profile/demographics`
- `DELETE /profile/demographics`
- `POST /jobs/ingest-demo`
- `GET /jobs`
- `GET /jobs/{job_id}`
- `POST /jobs/{job_id}/match`
- `POST /jobs/{job_id}/documents/{doc_type}`
- `POST /jobs/documents/{document_id}/export/{fmt}`
- `PUT /jobs/{job_id}/tracker`
- `GET /jobs/tracker/all`
- `GET /privacy/export`
- `DELETE /privacy/account`

Authentication uses a bearer token returned by signup or login.

