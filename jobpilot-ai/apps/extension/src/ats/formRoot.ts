/**
 * Application-form ROOT resolution.
 *
 * The live failure this fixes: `pickApplicationForm` chose the `<form>` with the
 * most inputs and fell back to `document`. Modern Greenhouse job-boards renders
 * the application in React with NO `<form>` element, so the only form on the
 * page was the site's search box — hence "Discovered: 1, Filled: 1" on Samsara,
 * and "Search products, whitepapers, & more..." becoming an application question
 * on MongoDB.
 *
 * Instead we score every candidate container on real application evidence,
 * hard-exclude site chrome (nav/search/newsletter/cookie/footer/header and
 * JobPilot's own widget), and refuse to guess when nothing is clearly the
 * application (APPLICATION_FORM_AMBIGUOUS). Discovery then runs ONLY inside the
 * chosen root — never a global scan.
 */

/** Bumped when the scoring rules change, so diagnostics are comparable. */
export const RESOLVER_VERSION = "2.0.0";

/** A candidate must beat this to be used at all. */
const MIN_CONFIDENT_SCORE = 6;

export interface FormCandidate {
  fingerprint: string;
  score: number;
  signals: string[];
  /** Set when the candidate was hard-excluded; score is then 0. */
  excluded?: string;
  fieldCount: number;
  requiredCount: number;
}

export interface FormRootResult {
  root: ParentNode | null;
  element: Element | null;
  confident: boolean;
  reason?: "APPLICATION_FORM_AMBIGUOUS" | "NO_APPLICATION_FORM";
  candidates: FormCandidate[];
  resolverVersion: string;
  /** How the root was chosen — surfaced in diagnostics so "no root" is never
   * an unexplained verdict. */
  rootKind?: "form" | "container" | "document";
  /** Human-readable explanation of the verdict, for Copy diagnostics. */
  explanation?: string;
}

const FIELD_SELECTOR = 'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]),textarea,select,[role="combobox"],[role="listbox"]';

// --------------------------------------------------------------------------- //
// Hard exclusions — site chrome is never an application form
// --------------------------------------------------------------------------- //
const EXCLUDE_ANCESTOR = 'nav,header,footer,[role="navigation"],[role="banner"],[role="contentinfo"],[role="search"]';
const EXCLUDE_TEXT_RE =
  /\b(search|newsletter|subscribe|subscription|cookie|consent[-_ ]?banner|gdpr|filter|sort|login|log[-_ ]?in|sign[-_ ]?in|sign[-_ ]?up|register|contact[-_ ]?us|demo[-_ ]?request|chat)\b/i;

function exclusionFor(el: Element): string | null {
  // JobPilot's own widget must never be treated as page content.
  if (el.closest("#jobpilot-assisted-apply")) return "jobpilot_widget";
  if (el.closest(EXCLUDE_ANCESTOR)) return "site_chrome";

  const role = (el.getAttribute("role") || "").toLowerCase();
  if (role === "search" || role === "navigation") return "search_or_nav_role";

  const identity = `${el.id} ${el.className} ${el.getAttribute("name") ?? ""} ${el.getAttribute("aria-label") ?? ""} ${el.getAttribute("data-testid") ?? ""}`;
  if (EXCLUDE_TEXT_RE.test(identity)) return `identity_matches_${identity.match(EXCLUDE_TEXT_RE)?.[0]?.toLowerCase()}`;

  const action = (el.getAttribute("action") || "").toLowerCase();
  if (action && EXCLUDE_TEXT_RE.test(action)) return "action_matches_excluded";

  // A form whose only meaningful control is a search box.
  const fields = Array.from(el.querySelectorAll<HTMLElement>(FIELD_SELECTOR));
  const searchy = fields.filter((f) => {
    const type = (f as HTMLInputElement).type;
    const text = `${f.id} ${f.className} ${(f as HTMLInputElement).name ?? ""} ${f.getAttribute("placeholder") ?? ""} ${f.getAttribute("aria-label") ?? ""}`;
    return type === "search" || /\bsearch\b/i.test(text);
  });
  if (fields.length > 0 && searchy.length === fields.length) return "search_only_form";

  if (isHidden(el)) return "hidden";
  return null;
}

function isHidden(el: Element): boolean {
  for (let node: Element | null = el, depth = 0; node && depth < 8; node = node.parentElement, depth += 1) {
    const style = (node.getAttribute("style") || "").toLowerCase();
    if (/display\s*:\s*none|visibility\s*:\s*hidden/.test(style)) return true;
    if ((node as HTMLElement).hidden) return true;
    if (node.getAttribute("aria-hidden") === "true") return true;
  }
  return false;
}

// --------------------------------------------------------------------------- //
// Positive evidence
// --------------------------------------------------------------------------- //
const NAME_RE = /\b(first[\s_-]?name|given[\s_-]?name|last[\s_-]?name|family[\s_-]?name|surname|full[\s_-]?name)\b/i;
const EMAIL_RE = /\b(e-?mail)\b/i;
const PHONE_RE = /\b(phone|mobile|telephone)\b/i;
const RESUME_RE = /resume|r[ée]sum[ée]|\bcv\b|curriculum vitae/i;
const APPLY_HEADING_RE = /\b(apply for this job|application form|submit your application|apply now|your application)\b/i;

function fieldText(el: Element): string {
  const input = el as HTMLInputElement;
  const id = el.id;
  const doc = el.ownerDocument;
  const parts = [
    input.name ?? "", el.id, el.getAttribute("aria-label") ?? "",
    el.getAttribute("placeholder") ?? "", el.getAttribute("autocomplete") ?? ""
  ];
  if (id) parts.push(doc.querySelector(`label[for="${cssEscape(id)}"]`)?.textContent ?? "");
  parts.push(el.closest("label")?.textContent ?? "");
  return parts.join(" ");
}

function scoreCandidate(el: Element): { score: number; signals: string[]; fieldCount: number; requiredCount: number } {
  const signals: string[] = [];
  let score = 0;

  const fields = Array.from(el.querySelectorAll<HTMLElement>(FIELD_SELECTOR));
  const texts = fields.map(fieldText);
  const requiredCount = fields.filter(
    (f) => (f as HTMLInputElement).required || f.getAttribute("aria-required") === "true"
  ).length;

  // A resume/CV file input is the single strongest application signal.
  const fileInputs = Array.from(el.querySelectorAll<HTMLInputElement>('input[type="file"]'));
  if (fileInputs.some((f) => RESUME_RE.test(fieldText(f) + " " + (f.closest("div,section,fieldset")?.textContent ?? "")))) {
    score += 5;
    signals.push("resume_upload");
  }

  // An apply/submit control belonging to this container.
  const submits = Array.from(el.querySelectorAll<HTMLElement>('button,[type="submit"],[role="button"]'));
  if (submits.some((b) => /\b(submit application|submit|apply)\b/i.test(b.textContent || b.getAttribute("value") || ""))) {
    score += 5;
    signals.push("submit_control");
  }

  // Identity fields: two or more of name/email/phone.
  const identityHits = [NAME_RE, EMAIL_RE, PHONE_RE].filter((re) => texts.some((t) => re.test(t))).length;
  if (identityHits >= 2) {
    score += 4;
    signals.push(`identity_fields_${identityHits}`);
  } else if (identityHits === 1) {
    score += 1;
    signals.push("identity_fields_1");
  }

  // ATS markers.
  if (el.querySelector('[name^="job_application"],[id^="job_application"],#grnhse_app,#application_form')) {
    score += 4;
    signals.push("greenhouse_markers");
  }
  if (el.closest(".ashby-application-form-container") || el.querySelector('[class*="_fieldEntry"]')) {
    score += 4;
    signals.push("ashby_markers");
  }
  if (el.matches('[data-qa="application-form"],.application-form') || el.querySelector('[data-qa="application-form"]')) {
    score += 4;
    signals.push("lever_markers");
  }
  if (el.querySelector('[data-automation-id]')) {
    score += 3;
    signals.push("workday_markers");
  }

  // An "Apply for this job" heading on the container OR on a close ancestor, so
  // a tight application div inherits the heading its section carries.
  let headingNode: Element | null = el;
  for (let depth = 0; headingNode && depth < 4; headingNode = headingNode.parentElement, depth += 1) {
    const heading = headingNode.querySelector("h1,h2,h3,legend");
    if (heading && APPLY_HEADING_RE.test(heading.textContent || "")) {
      score += 3;
      signals.push("apply_heading");
      break;
    }
  }

  // Concentration of required application controls.
  if (requiredCount >= 3) {
    score += 2;
    signals.push(`required_controls_${requiredCount}`);
  }

  // An application-ish endpoint.
  const action = (el.getAttribute("action") || "").toLowerCase();
  if (/appl(y|ication)|candidate|submission/.test(action)) {
    score += 2;
    signals.push("application_action");
  }

  // A container with essentially no fields is not the application.
  if (fields.length === 0) {
    score = 0;
    signals.push("no_fields");
  }

  return { score, signals, fieldCount: fields.length, requiredCount };
}

/** Stable, PII-free identifier for diagnostics. */
export function fingerprint(el: Element): string {
  const tag = el.tagName.toLowerCase();
  const id = el.id ? `#${el.id}` : "";
  const cls = typeof el.className === "string" && el.className ? `.${el.className.trim().split(/\s+/).slice(0, 3).join(".")}` : "";
  return `${tag}${id}${cls}`.slice(0, 120);
}

// --------------------------------------------------------------------------- //
// Candidate collection
// --------------------------------------------------------------------------- //
function collectCandidates(doc: Document): Element[] {
  const found = new Set<Element>();
  for (const form of Array.from(doc.querySelectorAll("form"))) found.add(form);

  // React applications frequently render NO <form>. Offer explicit ATS
  // containers and, failing that, the tightest container holding the
  // application's identity fields.
  const containerSelectors = [
    "#grnhse_app", "#application_form", "#application-form", "#application",
    '[id*="application"]', '[class*="application-form"]', '[class*="application_form"]',
    ".ashby-application-form-container", '[data-qa="application-form"]',
    '[data-ui="application-form"]', "main"
  ];
  for (const selector of containerSelectors) {
    for (const el of Array.from(doc.querySelectorAll(selector))) found.add(el);
  }

  const synthesized = synthesizeRoot(doc);
  if (synthesized) found.add(synthesized);
  return Array.from(found);
}

/** Smallest common ancestor of the strongest application fields — the fallback
 * for a form-less React application. */
function synthesizeRoot(doc: Document): Element | null {
  const anchors: Element[] = [];
  for (const el of Array.from(doc.querySelectorAll<HTMLElement>(FIELD_SELECTOR))) {
    if (exclusionFor(el)) continue;
    const text = fieldText(el);
    if (NAME_RE.test(text) || EMAIL_RE.test(text) || PHONE_RE.test(text)) anchors.push(el);
  }
  const resume = Array.from(doc.querySelectorAll<HTMLInputElement>('input[type="file"]')).find((f) =>
    RESUME_RE.test(fieldText(f) + " " + (f.closest("div,section,fieldset")?.textContent ?? ""))
  );
  if (resume) anchors.push(resume);
  if (anchors.length < 2) return null;

  let common: Element | null = anchors[0].parentElement;
  for (const anchor of anchors.slice(1)) {
    while (common && !common.contains(anchor)) common = common.parentElement;
    if (!common) return null;
  }
  // body/documentElement are not returned as a *synthesized container* — that
  // would be an unscored global scan. The document is still reachable, but only
  // through the explicitly scored `documentFallback` below.
  if (!common || common === doc.body || common === doc.documentElement) return null;
  return common;
}

/**
 * Last-resort root: the frame's own document.
 *
 * The live Greenhouse/React failure this fixes: the application is rendered at
 * document level with no <form> and no container whose selector we recognise,
 * so every candidate scored below threshold and the run aborted with
 * "couldn't identify the application form" — on a page where the application
 * was plainly visible.
 *
 * This is NOT a global scan re-introduced by the back door. The document is
 * accepted only when the frame as a whole carries a coherent application
 * control set, and only after the same exclusion rules have removed site
 * chrome. A page whose only form is a search box still scores far too low to
 * qualify, which is what keeps the Fixture-3 case rejected.
 */
function documentFallback(doc: Document): { score: number; signals: string[]; fieldCount: number; requiredCount: number } | null {
  const body = doc.body;
  if (!body) return null;

  // Score the document exactly like any other candidate, so the decision is
  // comparable and explainable rather than special-cased.
  const { score, signals, fieldCount, requiredCount } = scoreCandidate(body);

  // A document root needs MORE evidence than a targeted container: it is the
  // widest possible scope, so the bar is deliberately higher.
  const identitySignals = signals.filter((s) => s.startsWith("identity_fields")).length;
  const hasStrongAnchor =
    signals.includes("resume_upload") || signals.includes("submit_control") || identitySignals > 0;

  if (!hasStrongAnchor) return null;
  if (fieldCount < 3) return null;

  return { score, signals: [...signals, "document_fallback"], fieldCount, requiredCount };
}

// --------------------------------------------------------------------------- //
// Resolution
// --------------------------------------------------------------------------- //
/** Candidates within this margin of the best score are treated as describing the
 * same application, so the TIGHTEST of them wins. */
const NESTED_MARGIN = 4;

export function resolveApplicationRoot(doc: Document): FormRootResult {
  const candidates: FormCandidate[] = [];
  const scored: { el: Element; score: number }[] = [];

  for (const el of collectCandidates(doc)) {
    const excluded = exclusionFor(el);
    if (excluded) {
      candidates.push({ fingerprint: fingerprint(el), score: 0, signals: [], excluded, fieldCount: 0, requiredCount: 0 });
      continue;
    }
    const { score, signals, fieldCount, requiredCount } = scoreCandidate(el);
    candidates.push({ fingerprint: fingerprint(el), score, signals, fieldCount, requiredCount });
    if (score > 0) scored.push({ el, score });
  }

  candidates.sort((a, b) => b.score - a.score);
  const bestScore = scored.reduce((max, s) => Math.max(max, s.score), 0);

  if (bestScore < MIN_CONFIDENT_SCORE) {
    // No container qualified. Before giving up, consider the frame's own
    // document: a React application rendered at document level with no <form>
    // and no recognisable wrapper is a real and common shape, and refusing it
    // is what produced "couldn't identify the application form" on a page whose
    // application was fully visible.
    const fallback = documentFallback(doc);
    if (fallback && fallback.score >= MIN_CONFIDENT_SCORE) {
      candidates.unshift({
        fingerprint: "document",
        score: fallback.score,
        signals: fallback.signals,
        fieldCount: fallback.fieldCount,
        requiredCount: fallback.requiredCount
      });
      return {
        root: doc,
        element: doc.body,
        confident: true,
        candidates,
        resolverVersion: RESOLVER_VERSION,
        rootKind: "document",
        explanation:
          `No container scored >= ${MIN_CONFIDENT_SCORE}, but the frame document itself scored ` +
          `${fallback.score} with ${fallback.fieldCount} controls ` +
          `(${fallback.signals.join(", ")}). Using the frame document as the root.`
      };
    }

    return {
      root: null,
      element: null,
      confident: false,
      reason: "NO_APPLICATION_FORM",
      candidates,
      resolverVersion: RESOLVER_VERSION,
      explanation:
        `Best candidate scored ${bestScore} (threshold ${MIN_CONFIDENT_SCORE}); ` +
        `${candidates.length} candidate(s) considered, ` +
        `${candidates.filter((c) => c.excluded).length} excluded as site chrome. ` +
        (fallback
          ? `The frame document scored ${fallback.score}, still below threshold.`
          : `The frame document carried no application anchor (no resume upload, submit control, or identity fields).`)
    };
  }

  // Contenders describe the same application when each nests inside the others
  // (`main` ⊃ `#application` ⊃ `form`). Then the innermost is the right root: it
  // excludes the most surrounding page chrome. Sibling contenders instead mean
  // we genuinely cannot tell which form is the application.
  const contenders = scored.filter((s) => s.score >= bestScore - NESTED_MARGIN);
  const innermost = tightestNested(contenders);
  if (!innermost) {
    return {
      root: null, element: null, confident: false, reason: "APPLICATION_FORM_AMBIGUOUS",
      candidates, resolverVersion: RESOLVER_VERSION,
      explanation:
        `${contenders.length} candidates scored within ${NESTED_MARGIN} of the best (${bestScore}) ` +
        `but do not nest, so they describe different forms. Refusing to guess between them.`
    };
  }

  return {
    root: innermost, element: innermost, confident: true, candidates,
    resolverVersion: RESOLVER_VERSION,
    rootKind: innermost.tagName.toLowerCase() === "form" ? "form" : "container",
    explanation: `Chose <${innermost.tagName.toLowerCase()}> scoring ${bestScore} (threshold ${MIN_CONFIDENT_SCORE}).`
  };
}

/** The innermost element when every contender nests in a single chain; null when
 * they are siblings (ambiguous — never mix fields from two forms). */
function tightestNested(contenders: { el: Element; score: number }[]): Element | null {
  if (contenders.length === 0) return null;
  const innermost = contenders.reduce((a, b) => (b.el.contains(a.el) ? a : a.el.contains(b.el) ? b : a));
  const allNest = contenders.every((c) => c.el === innermost.el || c.el.contains(innermost.el));
  return allNest ? innermost.el : null;
}

function cssEscape(value: string): string {
  if (typeof CSS !== "undefined" && typeof CSS.escape === "function") return CSS.escape(value);
  return value.replace(/["\\]/g, "\\$&");
}
