/** Structured Employment/Education repeaters.
 *
 * Greenhouse and several custom ATS forms render one empty record followed by
 * an "Add another" action. Scalar canonical answers cannot represent that
 * shape: mapping every Company field to `current_company` overwrites every row
 * with the same job. This module expands the form to the number of saved profile
 * records and fills each row by index from the structured session snapshot.
 */

import type { ApplicationSessionData, DiscoveredField } from "../types";
import { discoverFields } from "./discovery";
import { fillField } from "./fill";

type ExperienceRecord = {
  company?: string;
  title?: string;
  location?: string;
  start_date?: string;
  end_date?: string;
  currently_working?: boolean;
};

type EducationRecord = {
  school?: string;
  degree?: string;
  major?: string;
  minor?: string;
  start_date?: string;
  end_date?: string;
  gpa?: string;
};

export type RepeaterFillResult = {
  recordsRequested: number;
  recordsFound: number;
  fieldsFilled: number;
  failures: string[];
};

// Greenhouse themes do not consistently use semantic heading tags for the
// Employment/Education labels (Lyft currently renders styled divs). Exact-text
// matching keeps this broad selector safe while covering both shapes.
const HEADING_SELECTOR = "h1,h2,h3,h4,h5,h6,legend,[role=heading],div,p,span,strong";
const ACTION_SELECTOR = "button,a,[role=button]";

export async function fillStructuredRepeaters(
  root: ParentNode,
  session: ApplicationSessionData
): Promise<RepeaterFillResult[]> {
  const experience = records<ExperienceRecord>(session.profileData?.experience);
  const education = records<EducationRecord>(session.profileData?.education);
  const results: RepeaterFillResult[] = [];
  if (experience.length) results.push(await fillExperience(root, experience));
  if (education.length) results.push(await fillEducation(root, education));
  return results;
}

async function fillExperience(root: ParentNode, recordsToFill: ExperienceRecord[]): Promise<RepeaterFillResult> {
  const boundary = findBoundary(root, /^(?:employment|work experience|employment history)$/i, /^(?:education|academic history)$/i);
  const result = emptyResult(recordsToFill.length);
  if (!boundary) return result;
  await expand(root, boundary, recordsToFill.length, /^(?:company name|employer|organization)$/i);
  const groups = groupByStart(fieldsWithin(root, boundary), /^(?:company name|employer|organization)$/i);
  result.recordsFound = groups.length;
  reportMissingRows("Employment", result);

  for (let index = 0; index < Math.min(groups.length, recordsToFill.length); index += 1) {
    const group = groups[index];
    const record = recordsToFill[index];
    await fillValue(group, /^(?:company name|employer|organization)$/i, record.company, result);
    await fillValue(group, /^(?:title|job title|position)$/i, record.title, result);
    await fillValue(group, /^(?:location|job location)$/i, record.location, result);
    await fillValue(group, /^start date month$/i, month(record.start_date), result);
    await fillValue(group, /^start date year$/i, year(record.start_date), result);
    await fillValue(group, /^(?:current role|currently work(?:ing)? here|present)$/i, record.currently_working ? "true" : "false", result);
    if (!record.currently_working) {
      await fillValue(group, /^end date month$/i, month(record.end_date), result);
      await fillValue(group, /^end date year$/i, year(record.end_date), result);
    }
  }
  return result;
}

async function fillEducation(root: ParentNode, recordsToFill: EducationRecord[]): Promise<RepeaterFillResult> {
  const boundary = findBoundary(root, /^(?:education|academic history)$/i, /^(?:questions|application questions|additional information)$/i);
  const result = emptyResult(recordsToFill.length);
  if (!boundary) return result;
  await expand(root, boundary, recordsToFill.length, /^(?:school|school name|institution|university|college)$/i);
  const groups = groupByStart(fieldsWithin(root, boundary), /^(?:school|school name|institution|university|college)$/i);
  result.recordsFound = groups.length;
  reportMissingRows("Education", result);

  for (let index = 0; index < Math.min(groups.length, recordsToFill.length); index += 1) {
    const group = groups[index];
    const record = recordsToFill[index];
    await fillValue(group, /^(?:school|school name|institution|university|college)$/i, record.school, result);
    await fillValue(group, /^degree$/i, record.degree, result);
    await fillValue(group, /^(?:discipline|major|field of study)$/i, record.major, result);
    await fillValue(group, /^minor$/i, record.minor, result);
    await fillValue(group, /^start date month$/i, month(record.start_date), result);
    await fillValue(group, /^start date year$/i, year(record.start_date), result);
    await fillValue(group, /^(?:end|graduation) date month$/i, month(record.end_date), result);
    await fillValue(group, /^(?:end|graduation) date year$/i, year(record.end_date), result);
    await fillValue(group, /^(?:major |cumulative )?gpa(?: \(.*\))?$/i, record.gpa, result);
  }
  return result;
}

type Boundary = { start: Element; end: Element | null };

function findBoundary(root: ParentNode, start: RegExp, end: RegExp): Boundary | null {
  const headings = Array.from(root.querySelectorAll(HEADING_SELECTOR));
  const startHeading = headings.find((item) => start.test(clean(item.textContent))) ?? null;
  if (!startHeading) return null;
  const endHeading = headings.find(
    (item) => after(item, startHeading) && end.test(clean(item.textContent))
  ) ?? null;
  return { start: startHeading, end: endHeading };
}

function fieldsWithin(root: ParentNode, boundary: Boundary): DiscoveredField[] {
  return discoverFields(root).filter((field) => {
    const element = field.element;
    return Boolean(element && after(element, boundary.start) && (!boundary.end || before(element, boundary.end)));
  });
}

async function expand(root: ParentNode, boundary: Boundary, wanted: number, startPattern: RegExp): Promise<void> {
  for (let guard = 0; guard < wanted + 2; guard += 1) {
    const beforeCount = fieldsWithin(root, boundary).filter((field) => startPattern.test(label(field))).length;
    if (beforeCount >= wanted) return;
    const action = Array.from(root.querySelectorAll<HTMLElement>(ACTION_SELECTOR)).find((item) =>
      /^\+?\s*add another(?:\s+(?:employment|education))?$/i.test(clean(item.textContent)) &&
      after(item, boundary.start) &&
      (!boundary.end || before(item, boundary.end)) &&
      !item.matches(":disabled") && item.getAttribute("aria-disabled") !== "true"
    );
    if (!action) return;
    action.scrollIntoView?.({ block: "center" });
    action.click();
    await waitFor(() => fieldsWithin(root, boundary).filter((field) => startPattern.test(label(field))).length > beforeCount, 1500);
  }
}

function groupByStart(fields: DiscoveredField[], start: RegExp): DiscoveredField[][] {
  const groups: DiscoveredField[][] = [];
  let current: DiscoveredField[] | null = null;
  for (const field of fields) {
    if (start.test(label(field))) {
      current = [];
      groups.push(current);
    }
    if (current) current.push(field);
  }
  return groups;
}

async function fillValue(
  fields: DiscoveredField[],
  pattern: RegExp,
  rawValue: string | undefined,
  result: RepeaterFillResult
): Promise<void> {
  const value = (rawValue ?? "").trim();
  if (!value) return;
  const field = fields.find((item) => pattern.test(label(item)) && item.element?.isConnected);
  if (!field?.element) return;
  const outcome = await fillField(field, value, {
    status: "verified",
    dropdownSearchValue: value
  });
  if (outcome.status === "filled" || outcome.status === "skipped") {
    field.element.setAttribute("data-jobpilot-repeater", "1");
    result.fieldsFilled += outcome.status === "filled" ? 1 : 0;
  } else {
    result.failures.push(`${label(field)}:${outcome.reason ?? "not filled"}`);
  }
}

function records<T>(value: unknown): T[] {
  return Array.isArray(value)
    ? value.filter((item): item is T => Boolean(item && typeof item === "object"))
    : [];
}

function emptyResult(recordsRequested: number): RepeaterFillResult {
  return { recordsRequested, recordsFound: 0, fieldsFilled: 0, failures: [] };
}

function reportMissingRows(section: string, result: RepeaterFillResult): void {
  if (result.recordsFound < result.recordsRequested) {
    result.failures.push(
      `${section}: found ${result.recordsFound} of ${result.recordsRequested} saved record rows`
    );
  }
}

function label(field: DiscoveredField): string {
  return clean(field.label || field.ariaLabel || field.placeholder || field.name);
}

function clean(value: string | null | undefined): string {
  return (value ?? "").replace(/[\*✱]/g, "").replace(/\s+/g, " ").trim();
}

function after(node: Node, start: Node): boolean {
  return Boolean(start.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING);
}

function before(node: Node, end: Node): boolean {
  return Boolean(node.compareDocumentPosition(end) & Node.DOCUMENT_POSITION_FOLLOWING);
}

function year(value: string | undefined): string {
  return /^(\d{4})/.exec(value ?? "")?.[1] ?? "";
}

function month(value: string | undefined): string {
  const matched = /^\d{4}-(\d{2})/.exec(value ?? "");
  if (!matched) return "";
  const index = Number(matched[1]) - 1;
  return index >= 0 && index < 12
    ? ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"][index]
    : "";
}

async function waitFor(check: () => boolean, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!check() && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 25));
}
