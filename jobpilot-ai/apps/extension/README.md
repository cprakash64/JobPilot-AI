# JobPilot AI — Assisted Apply (browser extension)

A Manifest V3 Chrome/Chromium extension that autofills employer application
forms with your **verified** JobPilot data, uploads your **tailored** resume and
cover letter, flags anything that needs your review, and **never submits** —
you always do the final review and click Submit yourself.

## What it does

1. Detects the JobPilot web app (version + capability handshake).
2. When you click **Open and autofill application**, a content script on the
   JobPilot origin catches that exact click in the **capture phase** and forwards
   a launch request to the background **synchronously within the user gesture**.
   The background opens the side panel (gesture-valid), then creates the employer
   tab itself (`chrome.tabs.create` — no website popup, exact tab id) and binds a
   `PendingLaunch` to that tab id in `chrome.storage.session`. The one-time launch
   token is staged into the extension's isolated content world, never the DOM/URL.
3. The employer content script announces readiness (`CONTENT_READY`); the
   background exchanges the single-use token **once**, caches the session package
   per tab (so refresh/retry never re-consumes the token), and hands the content
   script the verified answers.
4. Autofill starts **automatically** — no “Fill application” click needed. It
   detects the ATS, fills confidently-known fields, uploads the tailored documents
   into the correct inputs (verifying the employer UI accepted them), and
   highlights unresolved/sensitive items for you to answer.
5. It reports a **PII-free** summary (counts + reason codes only) back to JobPilot.

The launch flows through one canonical state machine
(`idle → preparing → package_ready → opening_tab → waiting_for_tab →
waiting_for_content_script → fetching_package → detecting_ats → discovering_fields
→ filling → completed / completed_with_review / failed`). The side panel is a pure
**observer** of the tab-scoped state in `chrome.storage.session`; both the
automatic launch and the manual **Fill application** retry call the same runner
(`startAutofillForTab` → content `runAutofill`).

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
9. The extension opens the employer tab **and** the side panel. Autofill runs
   **automatically** (you should NOT need to click “Fill application”). The panel
   shows the ATS, stage, and non-zero Filled/Review counts as it works.
10. Refresh the employer page — the extension recovers the pending session and
    continues without duplicating already-filled values or overwriting your edits.
11. Review everything and **submit the application yourself** — JobPilot never submits.

> After any code change you must **rebuild AND reload**: run `npm run build`, then
> click the ↻ reload icon on the extension card in `chrome://extensions`. A rebuild
> alone does not reload the running service worker.

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
