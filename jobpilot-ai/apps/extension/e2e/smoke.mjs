/**
 * Section O acceptance smoke test — a REAL Chromium browser driving the whole
 * Greenhouse-shaped application, then reporting the actual DOM state of every
 * dropdown. Run: node e2e/smoke.mjs   (add HEADED=1 to watch it).
 */
import { chromium } from "playwright";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = pathToFileURL(path.join(here, "fixtures", "samsara.html")).href;
const HARNESS = path.join(here, "bundle", "harness.js");

// What a real session would supply: confirmed profile/vault answers only.
// Sensitive, legal, and consent questions are deliberately ABSENT.
const CONFIRMED = {
  work_auth: "Yes",
  prev_samsara: "No",
  country: "United States",
  sponsor: "No",
  learned: ["LinkedIn", "Industry event"],
  country2: "United States",
  referral: "Employee referral"
};

const browser = await chromium.launch({ headless: !process.env.HEADED });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
await page.goto(FIXTURE);
await page.addScriptTag({ path: HARNESS });
await page.evaluate(() => window.JobPilotHarness.discover("#application-form"));

const results = [];
for (const [id, value] of Object.entries(CONFIRMED)) {
  const r = await page.evaluate(([i, v]) => window.JobPilotHarness.fill(i, v), [id, value]);
  results.push({ id, status: r.status, reason: r.dropdown?.reasonCode ?? null, adapter: r.dropdown?.adapterId });
}

// Cascade: the dependent control only has options after its parent is set.
await page.evaluate(() => window.JobPilotHarness.discover("#application-form"));
const state = await page.evaluate(() => window.JobPilotHarness.fill("state", "Arizona"));
results.push({ id: "state", status: state.status, reason: state.dropdown?.reasonCode ?? null, adapter: state.dropdown?.adapterId });

// Dropdowns with NO confirmed answer must stay blank and expose real options.
const unanswered = [];
for (const id of ["inert", "virt", "flaky", "behind"]) {
  const probe = await page.evaluate((i) => window.JobPilotHarness.probe(i), id);
  unanswered.push({ id, reason: probe.reason, optionCount: (probe.options || []).length });
}

const finalState = await page.evaluate(() => {
  const ids = ["work_auth", "prev_samsara", "country", "sponsor", "learned",
               "country2", "state", "referral", "inert", "virt", "flaky", "behind"];
  const out = {};
  for (const id of ids) out[id] = { selection: window.JobPilotHarness.selection(id), hasValue: window.JobPilotHarness.hasValue(id) };
  return { fields: out, submitClicked: window.__submitClicked, menusLeftOpen: document.querySelectorAll('[aria-expanded="true"]').length };
});

if (process.env.HEADED) await page.screenshot({ path: path.join(here, "smoke.png"), fullPage: false });

console.log("\n=== FILLED (confirmed answers) ===");
for (const r of results) console.log(` ${r.status === "filled" ? "OK  " : "MISS"} ${r.id.padEnd(14)} ${r.status.padEnd(16)} ${r.adapter ?? ""} ${r.reason ?? ""}`);
console.log("\n=== NOT ANSWERED (must stay blank, real options offered) ===");
for (const u of unanswered) console.log(` ${u.id.padEnd(14)} ${String(u.reason).padEnd(18)} options=${u.optionCount}`);
console.log("\n=== FINAL DOM STATE ===");
for (const [id, v] of Object.entries(finalState.fields)) {
  console.log(` ${id.padEnd(14)} hasValue=${String(v.hasValue).padEnd(5)} selection=${JSON.stringify(v.selection)}`);
}
console.log(`\n submitClicked = ${finalState.submitClicked}`);
console.log(` menusLeftOpen = ${finalState.menusLeftOpen}`);

const filledOk = results.every((r) => r.status === "filled");
const blanksStayBlank = ["inert", "virt", "flaky", "behind"].every((id) => finalState.fields[id].hasValue === false);
const ok = filledOk && blanksStayBlank && finalState.submitClicked === false && finalState.menusLeftOpen === 0;
console.log(`\n ACCEPTANCE: ${ok ? "PASS" : "FAIL"}\n`);
await browser.close();
process.exit(ok ? 0 : 1);
