/**
 * Application-surface activation.
 *
 * Some career pages do not render the application at all until the user reveals
 * it. On the live Airbnb page the "Role overview" tab is selected on load, the
 * Greenhouse iframe does not exist yet, and the application only appears after
 * activating a control whose accessible name is "Switch to application form"
 * (visible text: "Apply Now").
 *
 * Autofill previously ran, found no application in the top document, and stored
 * a terminal failure — before the application had any chance to exist.
 *
 * This module finds a control that merely REVEALS the application. The user has
 * already asked to "Open and autofill application", so revealing it is within
 * what they requested. Everything that could transmit data, authenticate, or
 * commit the application is excluded by name, and the reason a control was
 * judged safe is recorded so the decision is auditable.
 */

export interface ActivationCandidate {
  element: HTMLElement;
  /** Why this control was considered a safe application-surface control. */
  reason: string;
  /** Higher wins. */
  score: number;
}

/**
 * Controls that must NEVER be activated automatically.
 *
 * Checked before any positive rule, and matched against the accessible name,
 * the visible text and the control's own attributes — a control only has to
 * look like one of these once to be excluded permanently.
 */
const FORBIDDEN_PATTERNS: RegExp[] = [
  /\bsubmit\b/i,                       // "Submit application"
  /\bquick apply\b/i,                  // "Quick Apply with MyGreenhouse" — sends data
  /\bmygreenhouse\b/i,
  /\bsign\s*(in|up)\b/i,
  /\blog\s*(in|out)\b/i,
  /\bcontinue with\b/i,                // OAuth buttons
  /\bautofill with\b/i,                // third-party autofill integrations
  /\bupload\b/i,
  /\battach\b/i,
  /\bsend\b/i,
  /\bagree\b/i,
  /\baccept\b/i,
  /\bconsent\b/i,
  /\bdelete\b/i,
  /\bwithdraw\b/i
];

/** Accessible name for a control, using the same precedence the AT would. */
export function accessibleName(el: Element): string {
  const labelledBy = el.getAttribute("aria-labelledby");
  if (labelledBy) {
    const text = labelledBy
      .split(/\s+/)
      .map((id) => el.ownerDocument.getElementById(id)?.textContent ?? "")
      .join(" ")
      .trim();
    if (text) return text;
  }
  const ariaLabel = el.getAttribute("aria-label");
  if (ariaLabel?.trim()) return ariaLabel.trim();
  return (el.textContent ?? "").trim();
}

function normalize(text: string): string {
  return text.toLowerCase().replace(/\s+/g, " ").trim();
}

function isVisible(el: HTMLElement): boolean {
  const style = el.ownerDocument.defaultView?.getComputedStyle(el);
  if (style && (style.display === "none" || style.visibility === "hidden")) return false;
  if (el.hasAttribute("disabled") || el.getAttribute("aria-disabled") === "true") return false;
  return true;
}

/** True when ANY text associated with this control is forbidden. */
export function isForbiddenControl(el: Element): boolean {
  const haystack = [
    accessibleName(el),
    el.textContent ?? "",
    el.getAttribute("aria-label") ?? "",
    el.getAttribute("title") ?? "",
    el.getAttribute("name") ?? "",
    el.id
  ].join(" ");
  return FORBIDDEN_PATTERNS.some((re) => re.test(haystack));
}

/**
 * Find controls that reveal the application, best first.
 *
 * Deliberately conservative: a control is NOT eligible merely because it
 * contains the word "Apply" ("Apply filters", "Apply for other roles",
 * "Applied" …). It must either say explicitly that it switches to the
 * application form, or be a tab named Application, or be an Apply control that
 * additionally behaves like navigation (a tab, or a control pointing at an
 * application anchor).
 */
export function findActivationCandidates(root: ParentNode = document): ActivationCandidate[] {
  const candidates: ActivationCandidate[] = [];
  const controls = Array.from(
    root.querySelectorAll<HTMLElement>('button,[role="tab"],[role="button"],a[href]')
  );

  for (const el of controls) {
    if (!isVisible(el)) continue;
    // JobPilot's own UI is never page content.
    if (el.closest("#jobpilot-assisted-apply")) continue;
    if (isForbiddenControl(el)) continue;

    const name = normalize(accessibleName(el));
    const description = normalize(el.getAttribute("aria-description") ?? "");
    const role = (el.getAttribute("role") || "").toLowerCase();

    // 1. Strongest: the control states that it switches to the application form.
    //    This is exactly the live Airbnb control ("Switch to application form",
    //    rendered as "Apply Now").
    if (/switch to (the )?application( form)?/.test(name) || /switch to (the )?application( form)?/.test(description)) {
      candidates.push({ element: el, reason: "accessible_name_switches_to_application_form", score: 100 });
      continue;
    }

    // 2. A tab literally named "Application" inside a tablist.
    if (role === "tab" && /^application\b/.test(name)) {
      candidates.push({ element: el, reason: "tab_named_application", score: 80 });
      continue;
    }

    // 3. An "Apply now" control that is ALSO navigation-like. The extra
    //    requirement is what keeps "Apply filters" and stray marketing CTAs out.
    if (/^apply( now)?$/.test(name)) {
      const navigational =
        role === "tab" ||
        el.hasAttribute("aria-controls") ||
        (el.getAttribute("href") ?? "").startsWith("#") ||
        el.closest('[role="tablist"]') !== null;
      if (navigational) {
        candidates.push({ element: el, reason: "apply_control_in_tablist_or_anchor", score: 60 });
      }
    }
  }

  return candidates.sort((a, b) => b.score - a.score);
}

/**
 * Choose the single control to activate.
 *
 * Returns null when the page offers no unambiguous surface control, and when
 * two equally-strong candidates disagree — guessing between them could take the
 * user somewhere they did not ask to go.
 */
export function selectActivationControl(root: ParentNode = document): ActivationCandidate | null {
  const candidates = findActivationCandidates(root);
  if (candidates.length === 0) return null;
  const best = candidates[0];
  const tied = candidates.filter((c) => c.score === best.score);
  if (tied.length > 1) return null;
  return best;
}
