# Assisted Auto-Apply (Application Copilot)

JobPilot's assisted auto-apply is a **user-controlled copilot**, not a mass-apply
bot. When the user clicks **Apply on official site**, JobPilot prepares a tailored
resume + cover letter, creates a secure application session, opens the official
employer page, and — via a Chrome extension — fills the form with verified
profile data and marks anything uncertain or sensitive for review.

> **JobPilot never clicks the employer's final Submit button.** The user reviews
> everything and submits manually.

---

## Architecture

```
apps/web (Next.js)                 apps/api (FastAPI)                 apps/extension (MV3)
  Apply button                       POST /application-sessions          content script (JobPilot origin)
    └─ AutoApplyModal  ──create──▶     ├─ reuse/generate resume+cover       └─ PING/PONG + LAUNCH handoff
       │                               ├─ build safe answer set           background service worker
       │  launch token (postMessage)   ├─ mint one-time launch token        ├─ exchange launch → session token
       └────────────────────────────▶  └─ link tracker (Applying)          ├─ holds session-scoped token only
                                                                            └─ fetch docs (base64)
  opens employer tab ─────────────────────────────────────────────────▶  content script (ATS page)
                                       GET  /application-sessions/:id         ├─ detect ATS (Greenhouse/Lever/Ashby/Generic)
                                       GET  /…/:id/answers (safe only)        ├─ discover → map → fill (never sensitive)
                                       GET  /…/:id/resume|cover-letter        ├─ upload resume/cover
                                       POST /…/:id/events (audit)             └─ side panel: review, clear, complete
                                       POST /…/:id/complete (confirmed)
```

### Backend application-session flow
1. `POST /application-sessions {job_id}` validates the official URL, snapshots the
   job + verified profile, reuses or generates the tailored resume and cover
   letter, computes the **safe answer set** + **unresolved (sensitive) questions**,
   links the tracker (`applying`), mints a **one-time launch token** (only its
   SHA-256 is stored), and returns the session + launch token.
2. The extension calls `POST /application-sessions/token {launch_token}` to
   exchange it (single use) for a **session-scoped token** (~30 min) that can
   reach only that one session, its documents, and its safe answers.
3. Documents are served by authenticated, ownership-checked routes
   (`/…/:id/resume`, `/…/:id/cover-letter`) — private storage paths are never
   exposed.
4. Every important action is written to `application_audit_logs`.

Tables (`alembic` revision `0007_application_sessions`):
`application_sessions`, `application_audit_logs`, `application_answers` (vault).

---

## Browser extension

### Load it locally
```bash
cd apps/extension
npm install
npm run build        # → apps/extension/dist
# Chrome → chrome://extensions → Developer mode → Load unpacked → select apps/extension/dist
```
Configure the API base for non-local backends via the extension's storage
(`chrome.storage.local { apiBase }`); defaults to `http://localhost:8000`.

### Supported ATS (this phase)
| ATS        | Support |
|------------|---------|
| Greenhouse | Full — detection, discovery, mapping, fill, upload |
| Lever      | Full |
| Ashby      | Full |
| Generic HTML form | Fallback — standard fields |
| Workday    | **Limited** — detected + standard fields only; multi-step sections are manual. Architected for a dedicated adapter later. |

### Adding a new ATS adapter
1. Create `src/ats/<name>.ts` implementing `ATSAdapter` (see `greenhouse.ts`).
   Usually you only customize `detect()` and `findSubmitControl()`; reuse
   `baseDiscover` / `baseMap` from `ats/base.ts`.
2. Register it in `src/ats/registry.ts` (`SPECIFIC_ADAPTERS`).
3. Add a fixture in `src/__tests__/fixtures.ts` and a detection test.

### Field taxonomy
Canonical keys (`src/fields/taxonomy.ts`) align with the backend answer vault
(`first_name`, `email`, `linkedin_url`, …) plus `resume_upload`,
`cover_letter_upload`, `custom_motivation`, `custom_experience`, the sensitive
categories, and `unknown`.

### Confidence rules (`src/fields/mapping.ts`)
- **≥ 0.95** auto-fill verified facts
- **0.80–0.94** fill, but mark for review (non-sensitive only)
- **< 0.80** do not auto-fill — ask the user
- **sensitive** never auto-filled unless explicitly verified **and** enabled

Mapping hierarchy: sensitive detection → file/upload → `autocomplete` attribute →
name/id rules → label/nearby text. An LLM may only *classify*; deterministic code
performs every DOM interaction.

### Sensitive-data rules (`src/fields/sensitive.ts`)
Gender, race, ethnicity, disability, veteran status, sexual orientation,
religion, criminal history, legal attestations, security clearance, export
control, salary history, and government/EEO demographics are **never** filled,
inferred, or sent to an LLM. They are highlighted "review required" for the user
to answer directly.

---

## Security model
- No automatic final submission; no CAPTCHA bypass; no employer-password storage.
- Server-side ownership checks on every route (independent of frontend hiding).
- Short-lived, scoped tokens (launch = one-time ~5 min; session = ~30 min),
  independent of the user's main login token. Tokens live only in the web app and
  the extension's isolated context — **never** in a URL or on the employer page.
- Session tokens are held in `chrome.storage.session` (extension-isolated),
  never in page-local storage; cleared on browser close.
- Official URLs are validated (http/https only; `javascript:`/`data:`/`file:` and
  placeholder hosts rejected) before opening.
- Best-effort rate limits on session creation and token exchange.
- Only the active application form is filled; honeypot and disabled fields are
  skipped; JobPilot values are removable and never permanently alter the page.

---

## Testing

```bash
# Backend
cd apps/api && python3.11 -m pytest app/tests/test_applications.py -q

# Frontend
cd apps/web && npm run typecheck && npm test

# Extension
cd apps/extension && npm run typecheck && npm test && npm run build
```

Fixture pages live in `apps/extension/src/__tests__/fixtures.ts` (Greenhouse,
Lever, Ashby, generic, multi-step, EEO/sensitive, file uploads). **No automated
test ever runs against a live job application.**

---

## Local development workflow
1. Start the backend: `cd apps/api && uvicorn app.main:app --reload` (run
   `alembic upgrade head` first for a real DB).
2. Start the frontend: `cd apps/web && npm run dev`.
3. Build + load the extension (above).
4. Open a **local fixture** page (or a real ATS page you are authorized to test).
5. In JobPilot, click **Apply on official site** → the modal prepares the session
   and opens the employer tab.
6. Open the side panel → **Fill application** → review highlighted fields.
7. **Submit yourself** on the employer site, then **Mark application complete**.

### Manual QA checklist
- [ ] Greenhouse/Lever/Ashby/generic detected correctly
- [ ] Verified name/email/phone/links filled (green)
- [ ] Existing user-typed values not overwritten
- [ ] Resume + cover letter uploaded; filename appears
- [ ] Sensitive/EEO questions left blank + flagged
- [ ] Unknown questions not guessed
- [ ] Generated written answers marked for review
- [ ] "Clear JobPilot-filled fields" restores the form
- [ ] Final Submit is never clicked by JobPilot
- [ ] Tracker shows Applying → Applied after completion

---

## Known limitations
- Document upload, multi-step navigation, and the side panel are exercised via
  unit tests on fixtures + the built bundle; end-to-end fill on a **live** ATS is
  manual QA (never run against real applications in CI).
- Workday is detect-only + standard fields (no dedicated multi-step adapter yet).
- Written-response generation is scaffolded (fields are classified and flagged
  for review); on-demand drafting is a recommended next step.
- Semantic/LLM classification is a documented fallback tier; the current build
  ships the deterministic tiers only.
