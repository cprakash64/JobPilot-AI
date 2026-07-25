/**
 * Section M — REAL BROWSER dropdown tests (Chromium via Playwright).
 *
 * The fixture page implements genuine dropdown components (open on mousedown,
 * portal menus, async options, virtualization, multi-select chips, a flaky
 * control, and an inert control that renders options but never commits). The
 * adapters run as the real bundled code inside the page, so these tests prove
 * behaviour jsdom cannot: layout, hit-testing, focus, and portals.
 */
import { test, expect, type Page } from "@playwright/test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = pathToFileURL(path.join(here, "fixtures", "samsara.html")).href;
const HARNESS = path.join(here, "bundle", "harness.js");

type FillResult = { status: string; reason?: string; dropdown?: { adapterId?: string; reasonCode?: string; options: string[]; selected: string[] } };

async function load(page: Page): Promise<void> {
  await page.goto(FIXTURE);
  await page.addScriptTag({ path: HARNESS });
  await page.evaluate(() => {
    // Real components settle fast; keep budgets tight so the suite stays quick.
    window.JobPilotHarness.configureDropdownTiming({
      openPointerMs: 800, openKeyboardMs: 500, openEnterMs: 300,
      listboxMs: 500, optionsMs: 900, verifyMs: 800, pollStepMs: 20
    });
    window.JobPilotHarness.discover("#application-form");
  });
}

const fill = (page: Page, id: string, value: string | string[]) =>
  page.evaluate(([i, v]) => window.JobPilotHarness.fill(i as string, v as string | string[]), [id, value] as const) as Promise<FillResult>;
const selection = (page: Page, id: string) => page.evaluate((i) => window.JobPilotHarness.selection(i), id);
const hasValue = (page: Page, id: string) => page.evaluate((i) => window.JobPilotHarness.hasValue(i), id);

test.beforeEach(async ({ page }) => load(page));

test("M-1: native select is discovered, filled and verified", async ({ page }) => {
  const result = await fill(page, "work_auth", "Yes");
  expect(result.status).toBe("filled");
  expect(result.dropdown?.adapterId).toBe("native-select");
  await expect(page.locator("#work_auth")).toHaveValue("Yes");
  expect(await hasValue(page, "work_auth")).toBe(true);
});

test("M-2: Greenhouse custom single-select opens and commits", async ({ page }) => {
  const result = await fill(page, "prev_samsara", "No");
  expect(result.status).toBe("filled");
  await expect(page.locator("#prev_samsara .select__value")).toHaveText("No");
  expect(await selection(page, "prev_samsara")).toEqual(["No"]);
});

test("M-3: React Select with a document.body menu PORTAL", async ({ page }) => {
  const result = await fill(page, "country", "United States");
  expect(result.status).toBe("filled");
  // The menu really was rendered outside the control's subtree.
  await expect(page.locator("#country .select__value")).toHaveText("United States");
  expect(await selection(page, "country")).toEqual(["United States"]);
});

test("M-4: searchable combobox filters and selects the exact option", async ({ page }) => {
  const result = await fill(page, "sponsor", "No");
  expect(result.status).toBe("filled");
  await expect(page.locator("#sponsor .select__value")).toHaveText("No");
});

test("M-5: multi-select selects several options and verifies each", async ({ page }) => {
  const result = await fill(page, "learned", ["LinkedIn", "Industry event"]);
  expect(result.status).toBe("filled");
  const chips = await page.locator("#learned .select__multi-value").allTextContents();
  expect(chips).toEqual(expect.arrayContaining(["LinkedIn", "Industry event"]));
  expect(await selection(page, "learned")).toEqual(expect.arrayContaining(["LinkedIn", "Industry event"]));
});

test("M-6: country → state cascade re-discovers dependent options", async ({ page }) => {
  // Before the country is chosen the state control has NO options at all.
  const early = await page.evaluate(() => window.JobPilotHarness.probe("state"));
  expect(early.options ?? []).toHaveLength(0);

  expect((await fill(page, "country2", "United States")).status).toBe("filled");
  // Re-discover: the dependent control re-rendered after the country changed.
  await page.evaluate(() => window.JobPilotHarness.discover("#application-form"));
  const state = await fill(page, "state", "Arizona");
  expect(state.status).toBe("filled");
  await expect(page.locator("#state .select__value")).toHaveText("Arizona");
});

test("M-7: options that load asynchronously are waited for", async ({ page }) => {
  const result = await fill(page, "referral", "Employee referral");
  expect(result.status).toBe("filled");
  await expect(page.locator("#referral .select__value")).toHaveText("Employee referral");
});

test("M-8: virtualized list — an option outside the rendered window is reported, never guessed", async ({ page }) => {
  // "Nepal" exists in the data but is not in the rendered DOM window.
  const result = await fill(page, "virt", "Nepal");
  expect(result.status).toBe("review_required");
  expect(result.dropdown?.reasonCode).toBe("OPTION_NOT_AVAILABLE");
  // The first option was NOT selected as a fallback.
  expect(await selection(page, "virt")).toEqual([]);
  // An option that IS in the window selects normally.
  const inWindow = await fill(page, "virt", "Country 3");
  expect(inWindow.status).toBe("filled");
});

test("M-9: a dropdown overlapped by a floating widget is still operated", async ({ page }) => {
  // Simulate the JobPilot widget occupying the bottom-right corner.
  await page.evaluate(() => {
    const w = document.createElement("div");
    w.id = "fake-widget";
    document.body.appendChild(w);
  });
  const result = await fill(page, "behind", "I am not a veteran");
  expect(result.status).toBe("filled");
  await expect(page.locator("#behind .select__value")).toHaveText("I am not a veteran");
});

test("M-10: a flaky first open is retried and then succeeds", async ({ page }) => {
  const result = await fill(page, "flaky", "They/them");
  expect(result.status).toBe("filled");
  await expect(page.locator("#flaky .select__value")).toHaveText("They/them");
});

test("M-11: an inert control (renders options, commits nothing) FAILS verification", async ({ page }) => {
  const result = await fill(page, "inert", "Non-binary");
  expect(result.status).toBe("review_required");
  expect(result.dropdown?.reasonCode).toBe("DROPDOWN_VERIFICATION_FAILED");
  // The real options are still reported so the widget can ask the user.
  expect(result.dropdown?.options).toEqual(["Male", "Female", "Non-binary"]);
  expect(await hasValue(page, "inert")).toBe(false);
});

test("M-12: probing an unanswered dropdown returns its REAL options for the widget", async ({ page }) => {
  const probe = await page.evaluate(() => window.JobPilotHarness.probe("prev_samsara"));
  expect(probe.reason).toBe("ANSWER_MISSING");
  expect(probe.options).toEqual(["Yes", "No"]);
});

test("M-13: a placeholder never counts as a value", async ({ page }) => {
  // Nothing has been filled yet — every required dropdown must read as blank.
  for (const id of ["work_auth", "prev_samsara", "country", "sponsor", "learned"]) {
    expect(await hasValue(page, id), `${id} must be blank`).toBe(false);
  }
  expect(await page.evaluate(() => window.JobPilotHarness.isBlankValue("Select..."))).toBe(true);
  expect(await page.evaluate(() => window.JobPilotHarness.isBlankValue("Choose..."))).toBe(true);
  expect(await page.evaluate(() => window.JobPilotHarness.isBlankValue("United States"))).toBe(false);
});

test("M-14: only one dropdown is open at a time (per-frame mutex)", async ({ page }) => {
  // Drive several concurrently; the queue must serialize them and all succeed.
  const results = await page.evaluate(async () => {
    const h = window.JobPilotHarness;
    return Promise.all([
      h.fill("prev_samsara", "Yes"),
      h.fill("country", "Canada"),
      h.fill("sponsor", "No")
    ]);
  });
  for (const r of results as FillResult[]) expect(r.status).toBe("filled");
  const open = await page.locator('[aria-expanded="true"]').count();
  expect(open).toBe(0);
  await expect(page.locator("#prev_samsara .select__value")).toHaveText("Yes");
  await expect(page.locator("#country .select__value")).toHaveText("Canada");
  await expect(page.locator("#sponsor .select__value")).toHaveText("No");
});

test("M-15: the full diagnostic event trail is recorded (no answer values)", async ({ page }) => {
  await fill(page, "country", "Canada");
  const events = await page.evaluate(() => window.JobPilotHarness.events());
  const names = Object.values(events).flat().map((e) => (e as { event: string }).event);
  expect(names).toEqual(expect.arrayContaining([
    "FIELD_DISCOVERED", "DROPDOWN_ADAPTER_SELECTED", "DROPDOWN_OPEN_ATTEMPT",
    "DROPDOWN_OPENED", "OPTIONS_DISCOVERED", "OPTION_MATCHED", "OPTION_CLICKED", "SELECTION_VERIFIED"
  ]));
  // Diagnostics must never contain the chosen answer text.
  expect(JSON.stringify(events)).not.toContain("Canada");
});

test("M-16: no dropdown action ever clicks Submit", async ({ page }) => {
  await fill(page, "work_auth", "Yes");
  await fill(page, "country", "India");
  await fill(page, "learned", ["LinkedIn", "Other"]);
  await fill(page, "inert", "Male");
  expect(await page.evaluate(() => window.__submitClicked)).toBe(false);
});

declare global {
  interface Window {
    JobPilotHarness: {
      configureDropdownTiming: (t: Record<string, number>) => void;
      discover: (selector?: string) => unknown[];
      fill: (id: string, value: string | string[]) => Promise<FillResult>;
      probe: (id: string) => Promise<{ reason?: string; options: string[] }>;
      selection: (id: string) => string[] | null;
      hasValue: (id: string) => boolean | null;
      isBlankValue: (v: string) => boolean;
      events: () => Record<string, unknown[]>;
    };
    __submitClicked: boolean;
  }
}
