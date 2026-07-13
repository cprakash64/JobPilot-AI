/**
 * Fill orchestration: discover -> map -> fill standard fields, applying the
 * confidence + sensitive policy. Sensitive and low-confidence fields are
 * highlighted for review, never silently filled. Upload targets are returned
 * for the caller to handle (document bytes require the session token, held only
 * in the extension background). Pure and DOM-only — unit-testable under jsdom.
 */

import type { ApplicationSessionData, DiscoveredField, FieldMapping } from "../types";
import { discoverFields } from "./discovery";
import { fillField, highlight } from "./fill";
import { buildMappings } from "./mapping";
import { UPLOAD_FIELDS } from "./taxonomy";

export interface FillSummary {
  filled: number;
  skipped: number;
  reviewRequired: number;
  errors: string[];
  uploadTargets: { uid: string; kind: "resume" | "cover-letter" }[];
  mappings: FieldMapping[];
}

export function scan(root: ParentNode, session: ApplicationSessionData, step = 0): { fields: DiscoveredField[]; mappings: FieldMapping[] } {
  const fields = discoverFields(root, step);
  const { mappings } = buildMappings(fields, session);
  return { fields, mappings };
}

export function applyFill(fields: DiscoveredField[], mappings: FieldMapping[], session: ApplicationSessionData): FillSummary {
  const answers = new Map(session.answers.map((a) => [a.canonical_key, a]));
  const fieldByUid = new Map(fields.map((f) => [f.uid, f]));
  const summary: FillSummary = { filled: 0, skipped: 0, reviewRequired: 0, errors: [], uploadTargets: [], mappings };

  for (const mapping of mappings) {
    const field = fieldByUid.get(mapping.uid);
    if (!field?.element) {
      continue;
    }
    if (UPLOAD_FIELDS.has(mapping.canonicalKey)) {
      summary.uploadTargets.push({ uid: mapping.uid, kind: mapping.canonicalKey === "cover_letter_upload" ? "cover-letter" : "resume" });
      highlight(field.element, "generated");
      continue;
    }
    if (mapping.sensitive) {
      // Never fill — mark for the user to answer directly.
      highlight(field.element, "review");
      summary.reviewRequired += 1;
      continue;
    }
    if (!mapping.safeToAutoFill) {
      if (mapping.requiresReview) {
        highlight(field.element, field.required ? "invalid" : "review");
        summary.reviewRequired += 1;
      }
      continue;
    }
    const answer = answers.get(mapping.canonicalKey);
    if (!answer?.value) {
      summary.reviewRequired += 1;
      continue;
    }
    const outcome = fillField(field, answer.value, { status: mapping.requiresReview ? "review" : "verified" });
    if (outcome.status === "filled") {
      summary.filled += 1;
      if (mapping.requiresReview) summary.reviewRequired += 1;
    } else if (outcome.status === "skipped") {
      summary.skipped += 1;
    } else if (outcome.status === "review_required") {
      summary.reviewRequired += 1;
    } else {
      summary.errors.push(`${mapping.canonicalKey}: ${outcome.reason ?? "error"}`);
    }
  }
  return summary;
}

export function runFill(root: ParentNode, session: ApplicationSessionData, step = 0): FillSummary {
  const { fields, mappings } = scan(root, session, step);
  return applyFill(fields, mappings, session);
}
