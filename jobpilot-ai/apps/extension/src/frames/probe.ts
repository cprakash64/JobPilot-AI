/**
 * Per-frame application probe — the sanitized evidence one frame reports about
 * itself so the background can rank frames against each other.
 *
 * The live failure this exists to fix: the application is embedded in a
 * cross-origin iframe. Every frame ran discovery independently and only the top
 * frame owned the widget, so the top frame — which genuinely has no application
 * in its own document — declared a tab-level failure ("JobPilot couldn't
 * identify the application form on this page") without ever knowing that a
 * sibling frame held the entire application.
 *
 * A probe carries COUNTS and SCORES only. It never carries entered values,
 * option text the user typed, tokens, cookies, resume content, or a full URL
 * with query values — a frame URL can itself contain an application id or an
 * email in a query string.
 */

import { resolveApplicationRoot, type FormCandidate } from "../ats/formRoot";

export interface FrameProbe {
  /** Assigned by the background from sender.frameId — never trusted from data. */
  frameId?: number;
  isTopFrame: boolean;
  /** Origin + pathname only. Query and fragment are dropped entirely. */
  sanitizedUrl: string;
  readyState: DocumentReadyState;

  // Control census — the evidence for "is there an application here?"
  formCount: number;
  visibleInputs: number;
  visibleTextareas: number;
  nativeSelects: number;
  ariaHaspopupButtons: number;
  roleComboboxes: number;
  requiredControls: number;
  fileInputs: number;
  openShadowRoots: number;
  applicationLabelsFound: string[];

  // Root resolution outcome for THIS frame.
  candidateCount: number;
  bestScore: number;
  rootConfident: boolean;
  rootKind?: string;
  rootReason?: string;
  rootExplanation?: string;
  topCandidates: Pick<FormCandidate, "fingerprint" | "score" | "signals" | "excluded">[];
}

/** Origin + path, with every query value and fragment removed. */
export function sanitizeUrl(href: string): string {
  try {
    const url = new URL(href);
    return `${url.origin}${url.pathname}`;
  } catch {
    return "(unparseable)";
  }
}

function isVisible(el: Element): boolean {
  const style = el.ownerDocument.defaultView?.getComputedStyle(el);
  if (style && (style.display === "none" || style.visibility === "hidden")) return false;
  // A zero-box control that is nonetheless focusable (Greenhouse hides file
  // inputs behind an Attach button) still counts — see fileInputs below.
  return true;
}

function countVisible(doc: Document, selector: string): number {
  return Array.from(doc.querySelectorAll(selector)).filter(isVisible).length;
}

/** Labels that indicate a real job application rather than site chrome. */
const APPLICATION_LABEL_PATTERNS: [string, RegExp][] = [
  ["first_name", /\bfirst\s*name\b/i],
  ["last_name", /\blast\s*name\b/i],
  ["email", /\be-?mail\b/i],
  ["phone", /\bphone\b/i],
  ["location", /\blocation\b/i],
  ["resume", /\bresume|r[ée]sum[ée]|\bcv\b/i],
  ["cover_letter", /\bcover\s*letter\b/i],
  ["linkedin", /\blinkedin\b/i],
  ["work_authorization", /\bwork\s*authoriz|legally\s*authoriz/i],
  ["sponsorship", /\bsponsorship\b/i],
  ["submit_application", /\b(submit application|apply for this job)\b/i]
];

function findApplicationLabels(doc: Document): string[] {
  // textContent of the body, capped — we only need to know WHICH labels exist,
  // and we deliberately never store the surrounding text.
  const text = (doc.body?.textContent ?? "").slice(0, 20000);
  return APPLICATION_LABEL_PATTERNS.filter(([, re]) => re.test(text)).map(([name]) => name);
}

function countOpenShadowRoots(doc: Document): number {
  let count = 0;
  const walk = (root: ParentNode, depth: number) => {
    if (depth > 6) return;
    for (const el of Array.from(root.querySelectorAll("*"))) {
      const shadow = (el as Element & { shadowRoot?: ShadowRoot | null }).shadowRoot;
      // Closed shadow roots are invisible to us by design; they are reported as
      // a limitation rather than silently ignored.
      if (shadow) {
        count += 1;
        walk(shadow, depth + 1);
      }
    }
  };
  try {
    walk(doc, 0);
  } catch {
    /* a hostile page can throw from a getter; the count is best-effort */
  }
  return count;
}

/** Build this frame's probe. Pure observation — never mutates the page. */
export function probeFrame(doc: Document = document): FrameProbe {
  const root = resolveApplicationRoot(doc);
  const bestScore = root.candidates.reduce((max, c) => Math.max(max, c.score), 0);

  return {
    isTopFrame: doc.defaultView ? doc.defaultView.top === doc.defaultView : true,
    sanitizedUrl: sanitizeUrl(doc.location?.href ?? ""),
    readyState: doc.readyState,

    formCount: doc.querySelectorAll("form").length,
    visibleInputs: countVisible(doc, "input:not([type=hidden])"),
    visibleTextareas: countVisible(doc, "textarea"),
    nativeSelects: countVisible(doc, "select"),
    ariaHaspopupButtons: doc.querySelectorAll('button[aria-haspopup="listbox"],button[aria-haspopup="menu"]').length,
    roleComboboxes: doc.querySelectorAll('[role="combobox"],[role="listbox"]').length,
    requiredControls: doc.querySelectorAll("[required],[aria-required=true]").length,
    // NOT filtered by visibility: Greenhouse hides the real file input behind an
    // "Attach" button, and it is one of the strongest application signals.
    fileInputs: doc.querySelectorAll('input[type="file"]').length,
    openShadowRoots: countOpenShadowRoots(doc),
    applicationLabelsFound: findApplicationLabels(doc),

    candidateCount: root.candidates.length,
    bestScore,
    rootConfident: root.confident,
    rootKind: root.rootKind,
    rootReason: root.reason,
    rootExplanation: root.explanation,
    topCandidates: root.candidates.slice(0, 5).map((c) => ({
      fingerprint: c.fingerprint,
      score: c.score,
      signals: c.signals,
      excluded: c.excluded
    }))
  };
}

/**
 * Rank probes and pick the frame most likely to hold the application.
 *
 * A confidently-resolved root always beats an unresolved one, regardless of
 * which frame is on top: this is the rule that stops the top frame from
 * declaring failure while the iframe holds the whole application.
 */
export function selectApplicationFrame(probes: FrameProbe[]): {
  chosen: FrameProbe | null;
  rejected: { frameId?: number; reason: string }[];
} {
  const rejected: { frameId?: number; reason: string }[] = [];
  if (probes.length === 0) return { chosen: null, rejected };

  const scoreOf = (p: FrameProbe): number => {
    // A resolved root is worth more than any amount of raw control count.
    let value = p.rootConfident ? 1000 + p.bestScore : p.bestScore;
    // Application-specific labels are strong corroboration.
    value += p.applicationLabelsFound.length * 2;
    if (p.fileInputs > 0) value += 3;
    return value;
  };

  const ranked = [...probes].sort((a, b) => scoreOf(b) - scoreOf(a));
  const best = ranked[0];

  for (const probe of ranked.slice(1)) {
    rejected.push({
      frameId: probe.frameId,
      reason: probe.rootConfident
        ? `lower score (${scoreOf(probe)} vs ${scoreOf(best)})`
        : probe.rootReason ?? "no application root resolved"
    });
  }

  // Refuse to pick a frame that resolved nothing AND shows no application
  // evidence — that is the "no application anywhere in this tab" case, which
  // must be reported honestly rather than dressed up as a chosen frame.
  if (!best.rootConfident && best.applicationLabelsFound.length < 2) {
    rejected.unshift({
      frameId: best.frameId,
      reason: best.rootReason ?? "no application root and no application labels"
    });
    return { chosen: null, rejected };
  }

  return { chosen: best, rejected };
}
