/**
 * Dropdown adapter contract.
 *
 * Every dropdown — native select, ARIA combobox, React Select, Greenhouse's
 * custom control, a searchable combobox, a multi-select, or a radio group that
 * behaves like options — is driven through this one interface. Automatic fill
 * and the user's own choice in the JobPilot widget both go through it; there is
 * no separate simplified path.
 *
 * The cardinal rule: a click attempt is NEVER success. `select()` must be
 * followed by `verify()` reading real DOM state before anything is reported
 * filled.
 */

import type { DiscoveredField } from "../../types";

/** Exact failure codes surfaced to diagnostics and the review widget. */
export type DropdownReason =
  | "SKIPPED_NO_TARGET"
  | "DROPDOWN_NOT_VISIBLE"
  | "DROPDOWN_DISABLED"
  | "DROPDOWN_OPEN_FAILED"
  | "LISTBOX_NOT_FOUND"
  | "OPTIONS_NOT_FOUND"
  | "OPTION_NOT_AVAILABLE"
  | "ANSWER_MISSING"
  | "ANSWER_NEEDS_CONFIRMATION"
  | "DROPDOWN_SELECTION_FAILED"
  | "DROPDOWN_VERIFICATION_FAILED";

/** Progress events (section N). Never carries the user's answer value — only
 * structural facts and counts. */
export type DropdownEventName =
  | "FIELD_DISCOVERED"
  | "DROPDOWN_ADAPTER_SELECTED"
  | "DROPDOWN_OPEN_ATTEMPT"
  | "DROPDOWN_OPENED"
  | "OPTIONS_DISCOVERED"
  | "ANSWER_SOURCE"
  | "OPTION_MATCHED"
  | "OPTION_CLICKED"
  | "SELECTION_VERIFIED";

export interface DropdownEvent {
  event: DropdownEventName | DropdownReason;
  uid: string;
  /** Adapter id, option COUNT, answer SOURCE — never an answer value/label. */
  detail?: Record<string, string | number | boolean>;
}

export interface DropdownOption {
  /** Stable-ish identity for the option element (id, or a derived index key). */
  id: string;
  label: string;
  normalizedLabel: string;
  disabled: boolean;
  selected: boolean;
  element: HTMLElement;
}

export interface OpenResult {
  ok: boolean;
  reason?: DropdownReason;
  /** The resolved menu/listbox — may live in a document.body portal. */
  listbox?: HTMLElement | null;
}

export interface SelectResult {
  ok: boolean;
  reason?: DropdownReason;
}

export interface DropdownAdapter {
  id: string;
  canHandle(field: DiscoveredField): boolean;
  open(field: DiscoveredField): Promise<OpenResult>;
  getOptions(field: DiscoveredField, listbox?: HTMLElement | null): Promise<DropdownOption[]>;
  select(field: DiscoveredField, option: DropdownOption): Promise<SelectResult>;
  verify(field: DiscoveredField, expected: DropdownOption | string): Promise<boolean>;
  close(field: DiscoveredField): Promise<void>;
  /** Currently-selected labels read from real DOM state. Empty = blank, which
   * includes any placeholder ("Select...", "Choose...", …). Drives completeness. */
  readSelection(field: DiscoveredField): string[];
}

/** Where a chosen answer came from — ordered by trust (section E). */
export type AnswerSource =
  | "user_confirmed_saved"
  | "verified_profile"
  | "exact_option_label"
  | "controlled_alias"
  | "semantic_llm"
  | "user_prompt_required";

export interface DropdownFillResult {
  ok: boolean;
  reason?: DropdownReason;
  adapterId?: string;
  answerSource?: AnswerSource;
  /** Verified selected labels after the operation. */
  selected: string[];
  /** Option labels the control actually offers — surfaced to the widget. */
  options: string[];
  events: DropdownEvent[];
}
