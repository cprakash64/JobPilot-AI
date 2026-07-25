/**
 * Real-browser coverage for the three reported problems.
 *
 * Requires the stack to be running (docker compose up). Skips itself when the
 * web app is unreachable, so it never fails a unit-test-only run.
 */

import { expect, test, type Page } from "@playwright/test";

const BASE = process.env.WEB_BASE_URL ?? "http://localhost:3000";
const API = process.env.API_BASE_URL ?? "http://localhost:8000";
const PASSWORD = "password123";

/** A fresh account per spec run, so one run cannot see another's data. */
async function signUp(page: Page): Promise<string> {
  const email = `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  const response = await page.request.post(`${API}/auth/signup`, {
    data: { email, password: PASSWORD }
  });
  expect(response.ok(), "signup should succeed").toBeTruthy();
  return email;
}

async function login(page: Page, email: string) {
  await page.goto(`${BASE}/login`);
  await page.locator("input").nth(0).fill(email);
  await page.locator('input[type=password]').first().fill(PASSWORD);
  await page.getByRole("button", { name: /log in/i }).click();
  await page.waitForTimeout(1500);
}

// Make GET {API}/profile fail with a 500.
//
// Matching on the URL object rather than a wildcard glob matters: a glob ending
// in "/profile" also matches the Next.js page navigation to {BASE}/profile,
// which would replace the HTML document with JSON and test nothing at all.
async function stubProfileApi500(page: Page) {
  await page.route(
    (url) => url.origin === new URL(API).origin && url.pathname === "/profile",
    (route) =>
      route.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"boom"}' })
  );
}

/** Fail the test on any uncaught page error or console error. */
function trackErrors(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(`console: ${m.text()}`);
  });
  return errors;
}

test.beforeEach(async ({ page }) => {
  const reachable = await page.request.get(`${API}/healthz`).then((r) => r.ok()).catch(() => false);
  test.skip(!reachable, "API/web stack is not running");
});

// --------------------------------------------------------------------------- //
// Profile — the Basic Info crash
// --------------------------------------------------------------------------- //
test.describe("Profile wizard", () => {
  test("Import renders, then Basic info opens without crashing", async ({ page }) => {
    const errors = trackErrors(page);
    const email = await signUp(page);
    await login(page, email);

    await page.goto(`${BASE}/profile`);
    await expect(page.getByRole("heading", { name: "Import", exact: true })).toBeVisible();

    await page.getByRole("button", { name: /2\.\s*Basic info/i }).click();

    // The regression: this rendered "This page couldn't load".
    await expect(page.getByText(/couldn.t load/i)).toHaveCount(0);
    await expect(page.getByLabel(/First name/)).toBeVisible();
    expect(errors, "no page or console errors").toEqual([]);
  });

  test("Basic info exposes every required field", async ({ page }) => {
    const email = await signUp(page);
    await login(page, email);
    await page.goto(`${BASE}/profile`);
    await page.getByRole("button", { name: /2\.\s*Basic info/i }).click();

    for (const label of [
      /First name/, /Middle name/, /Last name/,
      /Preferred first name/, /Preferred last name/,
      /^Email/, /Phone number/, /City/, /State\/region/,
      /ZIP\/postal code/, /Country/, /Current employer/
    ]) {
      await expect(page.getByLabel(label).first(), `${label} should render`).toBeVisible();
    }
    await expect(page.getByLabel(/Phone country/)).toBeVisible();
    await expect(page.getByLabel(/Work authorization/)).toBeVisible();
  });

  test("null structured fields render as empty inputs, not 'null'", async ({ page }) => {
    const email = await signUp(page);
    await login(page, email);
    await page.goto(`${BASE}/profile`);
    await page.getByRole("button", { name: /2\.\s*Basic info/i }).click();

    for (const label of [/First name/, /Middle name/, /Last name/]) {
      await expect(page.getByLabel(label).first()).toHaveValue("");
    }
  });

  test("structured name and phone survive save and reload", async ({ page }) => {
    const email = await signUp(page);
    await login(page, email);
    await page.goto(`${BASE}/profile`);
    await page.getByRole("button", { name: /2\.\s*Basic info/i }).click();

    await page.getByLabel(/First name/).first().fill("Chandra");
    await page.getByLabel(/Middle name/).first().fill("Prakash");
    await page.getByLabel(/Last name/).first().fill("Pandey");
    await page.getByLabel(/Phone number/).first().fill("602-816-1309");
    await page.getByLabel(/ZIP\/postal code/).first().fill("85281");
    await page.getByRole("button", { name: /save profile/i }).click();
    await expect(page.getByText(/profile saved/i)).toBeVisible({ timeout: 10000 });

    await page.reload();
    await page.getByRole("button", { name: /2\.\s*Basic info/i }).click();
    await expect(page.getByLabel(/First name/).first()).toHaveValue("Chandra");
    await expect(page.getByLabel(/Middle name/).first()).toHaveValue("Prakash");
    await expect(page.getByLabel(/Last name/).first()).toHaveValue("Pandey");
    await expect(page.getByLabel(/ZIP\/postal code/).first()).toHaveValue("85281");

    // The backend normalized the phone to E.164.
    const stored = await page.evaluate(async (api) => {
      const token = localStorage.getItem("jobpilot_token");
      const r = await fetch(`${api}/profile`, { headers: { Authorization: `Bearer ${token}` } });
      return (await r.json()).profile;
    }, API);
    expect(stored.phone_e164).toBe("+16028161309");
    expect(stored.phone_country_iso2).toBe("US");
  });

  test("a slow profile load never overwrites what the user already typed", async ({ page }) => {
    const email = await signUp(page);
    await login(page, email);

    // Hold the profile GET open so the user types BEFORE it resolves. Without
    // the dirty guard, the late response reset the form and the save then
    // failed with "First and last name are required".
    await page.route(
      (url) => url.origin === new URL(API).origin && url.pathname === "/profile",
      async (route) => {
        if (route.request().method() === "GET") await new Promise((r) => setTimeout(r, 2500));
        await route.continue();
      }
    );

    await page.goto(`${BASE}/profile`);
    await page.getByRole("button", { name: /2\.\s*Basic info/i }).click();
    await page.getByLabel(/First name/).first().fill("Ada");
    await page.getByLabel(/Last name/).first().fill("Lovelace");

    // Let the delayed response land.
    await page.waitForTimeout(3500);
    await expect(page.getByLabel(/First name/).first()).toHaveValue("Ada");
    await expect(page.getByLabel(/Last name/).first()).toHaveValue("Lovelace");
  });

  test("an API failure shows a recoverable section error, not a page crash", async ({ page }) => {
    const email = await signUp(page);
    await login(page, email);

    await stubProfileApi500(page);
    await page.goto(`${BASE}/profile`);

    await expect(page.getByTestId("section-error")).toBeVisible();
    // The wizard chrome survives — this is not the global boundary.
    await expect(page.getByRole("button", { name: /2\.\s*Basic info/i })).toBeVisible();
    await expect(page.getByText(/couldn.t load/i)).toHaveCount(0);
    await expect(page.getByRole("button", { name: /retry/i })).toBeVisible();
  });
});

// --------------------------------------------------------------------------- //
// EEO
// --------------------------------------------------------------------------- //
test.describe("Optional EEO", () => {
  async function openEeoStep(page: Page): Promise<void> {
    await page.goto(`${BASE}/profile`);
    await page.getByRole("button", { name: "9. Optional EEO" }).click();
  }

  test("gender identity offers identities, never Yes/No", async ({ page }) => {
    const email = await signUp(page);
    await login(page, email);
    await openEeoStep(page);

    const gender = page.getByRole("group", { name: "Gender identity" });
    await expect(gender).toBeVisible();
    await expect(gender.getByRole("radio", { name: "Woman", exact: true })).toBeVisible();
    await expect(gender.getByRole("radio", { name: "Man", exact: true })).toBeVisible();
    await expect(gender.getByRole("radio", { name: "Non-binary", exact: true })).toBeVisible();
    await expect(gender.getByRole("radio", { name: "Yes", exact: true })).toHaveCount(0);
    await expect(gender.getByRole("radio", { name: "No", exact: true })).toHaveCount(0);
  });

  test("nothing is preselected and consent is unchecked", async ({ page }) => {
    const email = await signUp(page);
    await login(page, email);
    await openEeoStep(page);

    await expect(page.getByRole("group", { name: "Gender identity" })).toBeVisible();
    expect(await page.locator('input[type=radio]:checked').count()).toBe(0);
    await expect(page.getByRole("checkbox", { name: /I consent/i })).not.toBeChecked();
  });

  test("saving an answer without consent is refused", async ({ page }) => {
    const email = await signUp(page);
    await login(page, email);
    await openEeoStep(page);

    await page.getByRole("group", { name: "Gender identity" })
      .getByRole("radio", { name: "Woman", exact: true }).click();
    await page.getByRole("button", { name: /save settings/i }).click();
    await expect(page.getByTestId("section-error")).toBeVisible();
  });

  test("race is multi-select and Prefer not to answer is exclusive", async ({ page }) => {
    const email = await signUp(page);
    await login(page, email);
    await openEeoStep(page);

    const race = page.getByRole("group", { name: "Race and ethnicity" });
    await race.getByRole("checkbox", { name: "Asian" }).check();
    await race.getByRole("checkbox", { name: "White" }).check();
    await expect(race.getByRole("checkbox", { name: "Asian" })).toBeChecked();
    await expect(race.getByRole("checkbox", { name: "White" })).toBeChecked();

    await race.getByRole("checkbox", { name: "Prefer not to answer" }).check();
    await expect(race.getByRole("checkbox", { name: "Asian" })).not.toBeChecked();
    await expect(race.getByRole("checkbox", { name: "White" })).not.toBeChecked();
  });
});

// --------------------------------------------------------------------------- //
// Theme
// --------------------------------------------------------------------------- //
const ROUTES = ["/login", "/dashboard", "/profile", "/jobs", "/tracker", "/settings"];

function luminance(rgb: string): number {
  const [r, g, b] = (rgb.match(/\d+/g) ?? ["255", "255", "255"]).map(Number);
  return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
}

test.describe("System light/dark theme", () => {
  test("light preference renders light surfaces on every major route", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });
    const email = await signUp(page);
    await login(page, email);

    for (const route of ROUTES) {
      await page.goto(`${BASE}${route}`);
      const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
      expect(luminance(bg), `${route} should be light`).toBeGreaterThan(0.7);
    }
  });

  test("dark preference renders dark surfaces on every major route", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark" });
    const email = await signUp(page);
    await login(page, email);

    for (const route of ROUTES) {
      await page.goto(`${BASE}${route}`);
      const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
      expect(luminance(bg), `${route} should be dark`).toBeLessThan(0.3);
      // Charcoal, not pure black.
      expect(bg, `${route} should not be pure black`).not.toBe("rgb(0, 0, 0)");
    }
  });

  test("changing the OS preference at runtime updates the page", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto(`${BASE}/login`);
    const light = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);

    await page.emulateMedia({ colorScheme: "dark" });
    const dark = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);

    expect(dark).not.toBe(light);
    expect(luminance(dark)).toBeLessThan(luminance(light));
  });

  test("no white first paint in dark mode", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark" });
    // Sample the <html> background as early as the document exists — this is
    // painted by CSS + color-scheme, with no JS involved.
    await page.goto(`${BASE}/login`, { waitUntil: "commit" });
    const html = await page.evaluate(() => getComputedStyle(document.documentElement).backgroundColor);
    expect(luminance(html)).toBeLessThan(0.3);
  });

  test("inputs, cards and buttons use themed surfaces in dark mode", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark" });
    const email = await signUp(page);
    await login(page, email);
    await page.goto(`${BASE}/profile`);
    await page.getByRole("button", { name: /2\.\s*Basic info/i }).click();

    const input = page.getByLabel(/First name/).first();
    const inputBg = await input.evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(luminance(inputBg), "input background should be dark").toBeLessThan(0.35);

    const inputColor = await input.evaluate((el) => getComputedStyle(el).color);
    expect(luminance(inputColor), "input text should be light").toBeGreaterThan(0.6);
  });

  test("the error boundary is readable in dark mode", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark" });
    const email = await signUp(page);
    await login(page, email);

    await stubProfileApi500(page);
    await page.goto(`${BASE}/profile`);

    const alert = page.getByTestId("section-error");
    await expect(alert).toBeVisible();
    const color = await alert.evaluate((el) => getComputedStyle(el).color);
    const bg = await alert.evaluate((el) => getComputedStyle(el).backgroundColor);
    // Foreground and background must actually differ (no dark-on-dark).
    expect(Math.abs(luminance(color) - luminance(bg))).toBeGreaterThan(0.3);
  });

  test("screenshots for review in both themes", async ({ page }, testInfo) => {
    const email = await signUp(page);
    await login(page, email);

    for (const scheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: scheme });
      for (const route of ["/dashboard", "/profile", "/jobs"]) {
        await page.goto(`${BASE}${route}`);
        await page.waitForTimeout(600);
        await testInfo.attach(`${route.slice(1)}-${scheme}.png`, {
          body: await page.screenshot({ fullPage: true }),
          contentType: "image/png"
        });
      }
    }
  });
});
