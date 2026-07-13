# JobPilot AI — Assisted Apply (browser extension)

A Manifest V3 Chrome/Chromium extension that autofills employer application
forms with your **verified** JobPilot data, uploads your **tailored** resume and
cover letter, flags anything that needs your review, and **never submits** —
you always do the final review and click Submit yourself.

## What it does

1. Detects the JobPilot web app (version + capability handshake).
2. When you click **Open and autofill application**, JobPilot hands the
   extension a one-time launch token (never in the URL).
3. The extension exchanges that token for a short-lived, **session-scoped** token
   (never your normal login token) and fetches the prepared package:
   verified answers + the tailored resume/cover-letter documents.
4. On the employer tab it detects the ATS, fills confidently-known fields, uploads
   the documents into the correct inputs, and highlights unresolved/sensitive
   items for you to answer.
5. It reports a **PII-free** summary (counts + codes only) back to JobPilot.

Nothing is ever submitted automatically. Sensitive/voluntary questions
(demographics, veteran/disability status, legal attestations, salary, criminal
history, etc.) are **never** guessed — they are left for you.

## Security model (summary)

- The employer tab never receives a token or your profile data.
- Documents and answers are fetched with a **session-scoped** token held only in
  `chrome.storage.session` (in-memory, cleared on browser close), not your login
  token.
- No candidate PII is written to `chrome.storage.local` or logged.
- Allowed web origins and the API base are configurable in `src/config.ts`; the
  manifest host permissions are restricted to the supported ATS hosts + local dev.

## Supported ATS

Ashby (e.g. Temporal), Greenhouse, Lever, and a generic semantic-form adapter.
Workday is detected but flagged **limited** (standard fields only).

## Build

```bash
cd apps/extension
npm install       # first time only
npm run build     # outputs the loadable extension to apps/extension/dist
```

Other scripts: `npm run typecheck`, `npm test`.

## Install (load unpacked) and verify

1. Run `npm run build` (above). The loadable extension is in **`apps/extension/dist`**.
2. Open `chrome://extensions`.
3. Enable **Developer mode** (top-right toggle).
4. Click **Load unpacked**.
5. Select the **`apps/extension/dist`** directory.
6. Refresh JobPilot (`http://localhost:3000`).
7. Open a job and start an assisted application. The modal should now show
   **“JobPilot extension connected.”**
8. Click **Open and autofill application**.
9. On the employer tab, JobPilot fills the form and opens its side panel with a
   summary. Review, then submit the application yourself.

### Configuring for a non-local backend / production origin

Edit `src/config.ts`:

- `DEFAULT_API_BASE` — the JobPilot API base (or set `apiBase` in extension
  storage at runtime, no rebuild needed).
- `JOBPILOT_WEB_ORIGINS` — origins allowed to hand a launch token to the
  extension.

Then add the production web origin to `manifest.json` `content_scripts.matches`
and `host_permissions`, and rebuild. Production should list only known origins —
never `<all_urls>`.

## Notes / limitations

- File uploads use the browser `DataTransfer` API; a small number of ATSes block
  programmatic file assignment. In that case the extension reports the failure
  rather than claiming success — attach the document manually.
- Multi-step forms: the extension re-scans on DOM/route changes but never clicks
  a control that could submit or advance past a legal attestation.
