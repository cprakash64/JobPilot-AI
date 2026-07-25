/**
 * Section B/C — cross-origin embedded ATS, in a real browser.
 *
 * Two local static servers give two genuinely different origins
 * (127.0.0.1:PORT_A embeds localhost:PORT_B), so the iframe is cross-origin in
 * the way the live page is. `file://` or `srcdoc` cannot reproduce that
 * isolation, and would prove nothing.
 *
 * These tests exercise the discovery/root logic through the real bundled code
 * inside a real frame. They do NOT drive the full MV3 extension: loading an
 * unpacked extension and reproducing the background handshake is a separate,
 * larger harness (see the report's remaining-work section).
 */

import { expect, test, type Page } from "@playwright/test";
import { createServer, type Server } from "node:http";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const HARNESS = path.join(here, "bundle", "harness.js");

/** Serve one fixture directory on a fixed host so origins really differ. */
function serve(host: string, port: number): Promise<Server> {
  const server = createServer((req, res) => {
    const name = (req.url ?? "/").split("?")[0].replace(/^\//, "") || "index.html";
    try {
      const body = readFileSync(path.join(here, "fixtures", name));
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(body);
    } catch {
      res.writeHead(404).end("not found");
    }
  });
  return new Promise((resolve) => server.listen(port, host, () => resolve(server)));
}

const CARRIER = { host: "127.0.0.1", port: 5199 };
const ATS = { host: "localhost", port: 5198 };
const carrierOrigin = `http://${CARRIER.host}:${CARRIER.port}`;
const atsOrigin = `http://${ATS.host}:${ATS.port}`;

let servers: Server[] = [];

test.beforeAll(async () => {
  servers = [await serve(CARRIER.host, CARRIER.port), await serve(ATS.host, ATS.port)];
});

test.afterAll(async () => {
  await Promise.all(servers.map((s) => new Promise((r) => s.close(r))));
});

/**
 * Inject the bundled adapters into a specific frame and probe it there.
 *
 * `waitForSelector("#ats-frame")` only waits for the iframe ELEMENT; the
 * embedded document can still be empty. Probing then measures a blank frame and
 * reports no application — so wait for a control inside the frame first.
 */
async function probeIn(page: Page, frameOrigin: string, readySelector?: string) {
  // Match on ORIGIN, not a path substring: the carrier page's own URL carries
  // "?embed=<ats url>", so a substring match would select the TOP frame and
  // silently probe the wrong document.
  const frame = page.frames().find((f) => f.url().startsWith(frameOrigin));
  if (!frame) throw new Error(`frame not found for origin: ${frameOrigin}`);
  if (readySelector) await frame.waitForSelector(readySelector);
  await frame.evaluate(readFileSync(HARNESS, "utf-8"));
  return frame.evaluate(() => window.JobPilotHarness.probeFrame());
}

test("the carrier top frame is cross-origin from the embedded application", async ({ page }) => {
  await page.goto(`${carrierOrigin}/career-page.html?embed=${atsOrigin}/ats-application.html`);
  await page.waitForSelector("#ats-frame");

  const urls = page.frames().map((f) => f.url());
  expect(urls.some((u) => u.startsWith(carrierOrigin))).toBe(true);
  expect(urls.some((u) => u.startsWith(atsOrigin))).toBe(true);

  // Genuinely cross-origin: the top frame cannot reach into the child.
  const blocked = await page.evaluate(() => {
    const iframe = document.getElementById("ats-frame") as HTMLIFrameElement;
    try {
      return iframe.contentDocument === null;
    } catch {
      return true;
    }
  });
  expect(blocked, "iframe must be cross-origin for this test to mean anything").toBe(true);
});

test("the carrier frame resolves NO application root (only a search box)", async ({ page }) => {
  await page.goto(`${carrierOrigin}/career-page.html?embed=${atsOrigin}/ats-application.html`);
  await page.waitForSelector("#ats-frame");

  const probe = await probeIn(page, carrierOrigin);
  expect(probe.rootConfident).toBe(false);
  // ...and it must say why, rather than failing silently.
  expect(probe.rootExplanation).toBeTruthy();
});

test("the embedded ATS frame resolves the application at document level", async ({ page }) => {
  await page.goto(`${carrierOrigin}/career-page.html?embed=${atsOrigin}/ats-application.html`);
  await page.waitForSelector("#ats-frame");

  const probe = await probeIn(page, atsOrigin, "#first_name");
  expect(probe.rootConfident).toBe(true);
  // No <form> exists in that document at all.
  expect(probe.formCount).toBe(0);
  expect(probe.rootKind).toBe("document");
  // The hidden resume input behind the Attach button must still be seen.
  expect(probe.fileInputs).toBeGreaterThanOrEqual(2);
  expect(probe.applicationLabelsFound).toContain("first_name");
  expect(probe.applicationLabelsFound).toContain("resume");
});

test("frame ranking picks the ATS frame over the carrier frame", async ({ page }) => {
  await page.goto(`${carrierOrigin}/career-page.html?embed=${atsOrigin}/ats-application.html`);
  await page.waitForSelector("#ats-frame");

  const carrier = await probeIn(page, carrierOrigin);
  const ats = await probeIn(page, atsOrigin, "#first_name");

  const chosen = await page.evaluate(
    ([a, b]) => window.JobPilotHarness.selectApplicationFrame([a, b]).chosen,
    [{ ...carrier, frameId: 0 }, { ...ats, frameId: 7 }]
  );
  expect(chosen?.frameId).toBe(7);
});

test("the same application at top level (no iframe) also resolves", async ({ page }) => {
  await page.goto(`${atsOrigin}/ats-application.html`);
  await page.waitForSelector("#first_name");

  const probe = await probeIn(page, atsOrigin, "#first_name");
  expect(probe.rootConfident).toBe(true);
  expect(probe.isTopFrame).toBe(true);
});

test("probes never carry entered values", async ({ page }) => {
  await page.goto(`${atsOrigin}/ats-application.html`);
  await page.fill("#first_name", "SENSITIVE-FIRST");
  await page.fill("#email", "sensitive@example.com");

  const probe = await probeIn(page, atsOrigin, "#first_name");
  const serialized = JSON.stringify(probe);
  expect(serialized).not.toContain("SENSITIVE-FIRST");
  expect(serialized).not.toContain("sensitive@example.com");
});

test("probe URLs drop query values", async ({ page }) => {
  await page.goto(`${atsOrigin}/ats-application.html?token=SECRET-TOKEN&email=a@b.com`);
  const probe = await probeIn(page, atsOrigin, "#first_name");
  expect(probe.sanitizedUrl).not.toContain("SECRET-TOKEN");
  expect(probe.sanitizedUrl).not.toContain("a@b.com");
  expect(probe.sanitizedUrl).toBe(`${atsOrigin}/ats-application.html`);
});

test("nothing in the discovery path submits the application", async ({ page }) => {
  await page.goto(`${atsOrigin}/ats-application.html`);
  await probeIn(page, atsOrigin, "#first_name");
  const submitted = await page.evaluate(() => (window as never as { __APPLICATION_SUBMITTED__?: boolean }).__APPLICATION_SUBMITTED__);
  expect(submitted).toBeFalsy();
});
