import type { CanonicalField, MappingSource } from "./fields/taxonomy";

export type FieldControl =
  | "text"
  | "textarea"
  | "select"
  | "radio"
  | "checkbox"
  | "file"
  | "contenteditable"
  | "combobox"
  | "listbox"
  | "unknown";

/** A field discovered on the page, with the accessible metadata used to map it. */
export interface DiscoveredField {
  /** Stable across rescans of the same live element (backed by a WeakMap), so
   * the field ledger MERGES rather than churns when the SPA re-renders. */
  uid: string;
  /** Identifies the frame this control lives in (top vs. a nested iframe), so
   * the ledger can aggregate controls discovered across frames without collision. */
  frameId: string;
  control: FieldControl;
  inputType: string;
  name: string;
  id: string;
  autocomplete: string;
  placeholder: string;
  ariaLabel: string;
  label: string;
  /** Which wrapper rule produced `label` (diagnostics; see resolveQuestion). */
  labelSource: string;
  /** Lower-cased, whitespace-collapsed, asterisk/decoration-stripped label —
   * the join key for de-duplicating wrappers and grouping conditional follow-ups. */
  normalizedLabel: string;
  nearbyText: string;
  sectionHeading: string;
  required: boolean;
  disabled: boolean;
  visible: boolean;
  /** true for `<select multiple>` / `aria-multiselectable` multi-selects. */
  multiple: boolean;
  /** true when this is a custom (non-native) control — ARIA combobox/listbox or
   * a React-Select style widget — so the ledger records it even if unsupported. */
  custom: boolean;
  existingValue: string;
  options: string[];
  validationMessage: string;
  step: number;
  /** Non-serializable back-reference to the live element (omitted in tests/JSON). */
  element?: HTMLElement;
}

/** A DOM control that was intentionally NOT treated as an application field.
 * Recorded (never silently dropped) so diagnostics can explain the exclusion. */
export interface ExcludedControl {
  reason: "honeypot" | "hidden" | "disabled" | "navigation" | "duplicate" | "non_actionable";
  control: string;
  label: string;
}

export interface FieldMapping {
  uid: string;
  canonicalKey: CanonicalField;
  confidence: number;
  mappingSource: MappingSource;
  safeToAutoFill: boolean;
  requiresReview: boolean;
  sensitive: boolean;
  explanation: string;
}

export interface FieldMappingResult {
  mappings: FieldMapping[];
  unmapped: string[];
}

export interface FillOutcome {
  uid: string;
  status: "filled" | "skipped" | "review_required" | "error";
  reason?: string;
  /** Present for dropdown-like controls: which adapter ran, the option labels
   * the employer control ACTUALLY offers (surfaced to the widget), the verified
   * selection, and the exact failure code. Structural type to avoid a module
   * cycle with fields/dropdown. */
  dropdown?: {
    adapterId?: string;
    reasonCode?: string;
    options: string[];
    selected: string[];
  };
}

export interface DetectionResult {
  atsId: string;
  matched: boolean;
  confidence: number;
}

/** The subset of session data the extension is allowed to hold, from the API. */
export interface ApplicationSessionData {
  sessionId: number;
  atsType: string | null;
  officialUrl: string;
  jobTitle: string | null;
  company: string | null;
  profileData?: Record<string, unknown>;
  answers: SessionAnswer[];
  unresolvedQuestions: {
    canonical_key: string;
    reason?: string;
    /** "confirm_name" for the structured given/family name prompt;
     * "answer_on_employer_page" for sensitive categories. */
    action?: string;
    /** Naive-split suggestion for a name confirmation — a prefilled,
     * user-editable starting point, never auto-applied. */
    suggested_value?: string;
    has_saved_value?: boolean;
  }[];
  documents?: {
    resume: { status: string; documentId: number | null; downloadPath: string | null };
    coverLetter: { status: string; documentId: number | null; downloadPath: string | null };
  };
}

export interface SessionAnswer {
  canonical_key: string;
  value: string;
  display_value: string;
  source: string;
  confidence: number;
  sensitive: boolean;
  requires_review: boolean;
  verified: boolean;
}

export interface PageContext {
  url: string;
  document: Document;
}

/** ATS adapter contract. Deterministic code always performs DOM interaction. */
export interface ATSAdapter {
  id: string;
  displayName: string;
  detect(context: PageContext): DetectionResult;
  discoverFields(context: PageContext): DiscoveredField[];
  mapFields(fields: DiscoveredField[], session: ApplicationSessionData): FieldMappingResult;
  /** Locate the final submit control — for WARNING only; never clicked. */
  findSubmitControl(context: PageContext): HTMLElement | null;
  /** Locate a non-final page transition. Implemented only by dedicated,
   * conservative adapters; the content script still gates every click on a
   * fully-complete current page. */
  findNextControl?(context: PageContext): HTMLElement | null;
  /** True only when the ATS explicitly identifies its final review/preview step. */
  isReviewPage?(context: PageContext): boolean;
}
