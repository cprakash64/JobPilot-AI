import type { CanonicalField, MappingSource } from "./fields/taxonomy";

export type FieldControl = "text" | "textarea" | "select" | "radio" | "checkbox" | "file" | "unknown";

/** A field discovered on the page, with the accessible metadata used to map it. */
export interface DiscoveredField {
  uid: string;
  control: FieldControl;
  inputType: string;
  name: string;
  id: string;
  autocomplete: string;
  placeholder: string;
  ariaLabel: string;
  label: string;
  nearbyText: string;
  sectionHeading: string;
  required: boolean;
  disabled: boolean;
  visible: boolean;
  existingValue: string;
  options: string[];
  validationMessage: string;
  step: number;
  /** Non-serializable back-reference to the live element (omitted in tests/JSON). */
  element?: HTMLElement;
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
  answers: SessionAnswer[];
  unresolvedQuestions: { canonical_key: string; reason?: string }[];
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
}
