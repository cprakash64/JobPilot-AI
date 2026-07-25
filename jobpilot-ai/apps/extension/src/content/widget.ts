/** Compact, side-panel-independent status UI + review assistant. Isolated in
 * Shadow DOM so ATS styles cannot break it and it cannot affect application
 * form layout. Never submits anything — every action here either fills a
 * field the user explicitly chose an answer for, or saves that answer to the
 * JobPilot profile/answer vault on explicit opt-in. */
import type { LedgerCounts } from "../fields/ledger";

export type WidgetStage = "preparing" | "opening" | "detecting" | "filling" | "uploading" | "review" | "ready" | "failed";

export interface WidgetUpdate {
  stage: WidgetStage;
  filled?: number;
  total?: number;
  message?: string;
  /** false for a terminal failure (expired/consumed/mismatched handoff) —
   * Retry is disabled instead of re-asking the same unanswerable question. */
  recoverable?: boolean;
}

export type ReviewCategory = "required" | "optional" | "sensitive" | "application" | "technical";

export interface ReviewItem {
  /** DiscoveredField.uid for a fillable field, or a synthetic id for a
   * name-confirmation / backend-only unresolved question. */
  id: string;
  kind: "field" | "name_confirm";
  category: ReviewCategory;
  question: string;
  required: boolean;
  reasonText: string;
  /** Real options discovered on the ATS page — never invented. */
  options?: string[];
  control?: string;
  /** A multi-select: the user may pick several options; value is pipe-joined. */
  multiple?: boolean;
  suggestedValue?: string;
  reusable: boolean;
  defaultScope?: "global" | "company";
  resolved?: boolean;
}

export interface ReviewHandlers {
  /** Apply `value` to the field `id` right now. A multi-select passes an ARRAY
   * of chosen labels (never comma-joined text). For kind="name_confirm",
   * `value` is "Given|Family|PreferredFirst|PreferredLast". Returns whether the
   * fill was VERIFIED against the employer page. */
  onFill: (id: string, value: string | string[]) => Promise<boolean>;
  /** Persist `value` for future applications (only offered when reusable). */
  onSave: (id: string, value: string | string[], displayValue: string) => Promise<boolean>;
  onJumpToField: (id: string) => void;
}

/** A learned answer awaiting the user's explicit save decision. Nothing is ever
 * persisted until the user picks a scope here. */
export interface RememberPrompt {
  id: string;
  question: string;
  /** What the user chose, for their own confirmation. Sensitive answers are
   * summarised rather than echoed. */
  chosenSummary: string;
  employer: string | null;
  sensitive: boolean;
  proposedScope: "application" | "global" | "company" | "sensitive" | "none";
  /** When false the proposal is a guess, so no option is preselected. */
  scopeConfident: boolean;
  onDecision: (scope: "application" | "global" | "company" | "sensitive" | "none") => void;
}

const SCOPE_LABEL: Record<string, string> = {
  application: "Use only for this application",
  global: "Remember for all applications",
  company: "Remember only for this company",
  sensitive: "Sensitive — remember only with my explicit consent",
  none: "Don't save"
};

const CATEGORY_LABEL: Record<ReviewCategory, string> = {
  required: "Required information",
  optional: "Optional profile information",
  sensitive: "Sensitive / EEO information",
  application: "Application-specific questions",
  technical: "Unsupported controls or technical failures"
};

export function createWidget(actions: {
  retry: () => void;
  clear: () => void;
  complete: () => void;
  diagnostics?: () => void;
  /** Toggle "Teach JobPilot" observation. */
  teach?: (enabled: boolean) => void;
}): {
  update: (value: WidgetUpdate) => void;
  showReview: (items: ReviewItem[], handlers: ReviewHandlers, counts?: LedgerCounts) => void;
  refreshCounts: (counts: LedgerCounts) => void;
  /** Step the widget out of the way while a dropdown menu is being driven, so
   * its overlay can never intercept an option click (section L). */
  setInteractionMode: (active: boolean) => void;
  /** Ask the user whether (and at what scope) to remember a learned answer. */
  askToRemember: (prompt: RememberPrompt) => void;
  destroy: () => void;
} {
  // A prior content script can leave a frozen widget behind after the
  // extension is reloaded. The newly connected instance owns the UI and
  // replaces that inert host rather than creating a duplicate panel.
  document.getElementById("jobpilot-assisted-apply")?.remove();
  const host = document.createElement("div");
  host.id = "jobpilot-assisted-apply";
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `
    <style>
      :host{all:initial}
      *,*::before,*::after{box-sizing:border-box}
      .box{
        --ink:#17211c;--muted:#657069;--line:rgba(66,84,73,.16);--accent:#176b46;
        position:fixed;right:18px;bottom:18px;z-index:2147483647;
        width:min(372px,calc(100vw - 24px));max-height:min(82vh,780px);
        display:flex;flex-direction:column;overflow:hidden;
        color:var(--ink);border:1px solid rgba(255,255,255,.72);border-radius:22px;
        background:linear-gradient(150deg,rgba(255,255,255,.92),rgba(245,249,246,.78));
        box-shadow:0 24px 70px rgba(22,35,27,.20),0 2px 10px rgba(22,35,27,.08),inset 0 1px 0 rgba(255,255,255,.9);
        -webkit-backdrop-filter:blur(24px) saturate(150%);backdrop-filter:blur(24px) saturate(150%);
        font:13px/1.45 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",sans-serif;
        letter-spacing:-.006em
      }
      header{display:flex;align-items:center;gap:10px;min-height:58px;padding:11px 14px 11px 16px;border-bottom:1px solid var(--line);background:rgba(255,255,255,.42);font-size:14px;font-weight:660;cursor:pointer}
      .dot{width:9px;height:9px;border-radius:50%;background:#21a267;box-shadow:0 0 0 4px rgba(33,162,103,.11);flex-shrink:0}
      .title{letter-spacing:-.01em}
      .body{padding:14px 14px 12px;overflow:auto;scrollbar-width:thin;scrollbar-color:rgba(79,96,86,.28) transparent}
      .message{font-size:13px;color:#354139}
      .count{color:var(--muted);margin-top:3px;font-size:12px}
      .counts-row{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;margin-top:11px;font-size:11px;color:var(--muted)}
      .counts-row span{padding:7px 9px;border:1px solid rgba(71,92,79,.10);border-radius:10px;background:rgba(255,255,255,.54)}
      .counts-row b{display:block;margin-top:1px;color:var(--ink);font-size:13px;font-weight:660}
      .actions{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-top:11px;padding-top:11px;border-top:1px solid var(--line)}
      button{min-height:34px;border:1px solid rgba(60,86,70,.18);background:rgba(255,255,255,.68);color:#244334;border-radius:10px;padding:7px 10px;cursor:pointer;font:inherit;font-weight:560;transition:background .16s ease,border-color .16s ease,transform .16s ease,box-shadow .16s ease}
      button:hover{background:rgba(255,255,255,.95);border-color:rgba(37,104,68,.34);box-shadow:0 3px 12px rgba(31,57,42,.08)}
      button:active{transform:scale(.985)}
      button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid rgba(30,116,76,.22);outline-offset:2px}
      button:disabled{opacity:.42;cursor:default;box-shadow:none;transform:none}
      .collapse{margin-left:auto;width:32px;min-height:32px;border:0;background:rgba(255,255,255,.48);padding:0;border-radius:50%;font-size:18px;font-weight:400;color:#53615a}
      .collapsed .body{display:none}
      .failed .dot{background:#c85a3e}
      .review .dot{background:#d69416;box-shadow:0 0 0 4px rgba(214,148,22,.12)}
      .review-toggle{position:relative;width:100%;text-align:left;margin-top:12px;padding:10px 34px 10px 12px;border-color:rgba(35,105,69,.20);background:rgba(235,246,239,.72);font-weight:650}
      .review-toggle::after{content:"⌄";position:absolute;right:12px;top:8px;color:#647068;font-size:16px}
      .review-toggle[aria-expanded="true"]::after{content:"⌃"}
      .review-panel{display:none;margin-top:12px}
      .review-panel.open{display:block}
      .group-title{font-size:10px;font-weight:720;text-transform:uppercase;letter-spacing:.095em;color:#748078;margin:17px 2px 7px}
      .group-title:first-child{margin-top:2px}
      .item{border:1px solid rgba(54,87,67,.13);border-radius:16px;padding:13px;margin-bottom:9px;background:rgba(255,255,255,.62);box-shadow:0 1px 0 rgba(255,255,255,.8),0 5px 18px rgba(32,54,41,.035)}
      .item.resolved{opacity:.55}
      .item .q{font-size:14px;line-height:1.3;font-weight:670;letter-spacing:-.012em}
      .item .why{color:var(--muted);font-size:12px;line-height:1.42;margin-top:5px}
      .item .req{display:inline-flex;vertical-align:1px;margin-left:6px;padding:2px 6px;border-radius:999px;background:#fff0eb;color:#9b3f2a;font-size:9px;font-weight:740;letter-spacing:.03em;text-transform:uppercase}
      .item select,.item input[type=text]{width:100%;min-height:42px;margin-top:11px;padding:8px 10px;border:1px solid rgba(57,93,70,.24);border-radius:11px;background:rgba(255,255,255,.82);color:var(--ink);font:inherit;font-size:13px;box-shadow:inset 0 1px 2px rgba(26,43,33,.035)}
      .item input::placeholder{color:#8a938d}
      .item label.save{display:flex;align-items:flex-start;gap:8px;margin-top:10px;font-size:11px;line-height:1.35;color:#46534b}
      .item label.save input{accent-color:var(--accent);margin-top:1px}
      .item .row{display:flex;gap:7px;margin-top:10px}
      .item .row button{flex:1}
      .item .row button:first-child{background:#1b704a;border-color:#1b704a;color:white;box-shadow:0 5px 14px rgba(27,112,74,.17)}
      .item .row button:first-child:hover{background:#155f3e;border-color:#155f3e}
      .item .row button+button{background:rgba(255,255,255,.58);border-color:rgba(60,86,70,.16);color:#53615a;box-shadow:none}
      .empty{color:var(--muted);font-size:12px;padding:8px 2px}
      .teach-panel{margin-top:8px}
      .teach-item{border:1px solid rgba(173,134,45,.22);background:rgba(255,249,231,.76);border-radius:14px;padding:12px;margin-bottom:8px}
      .teach-item .q{font-weight:600}
      .teach-item .scopes{display:flex;flex-direction:column;gap:4px;margin-top:6px}
      .teach-item .scopes label{display:flex;align-items:center;gap:6px;font-size:11px}
      [data-a="continue"],[data-a="next"]{background:rgba(235,246,239,.78);border-color:rgba(35,105,69,.20)}
      [data-a="complete"]{grid-column:1/-1;background:#1b704a;border-color:#1b704a;color:#fff}
      [data-a="teach"][aria-pressed="true"]{background:#e9f5ed;border-color:#2f8f5b;font-weight:650}
      .more-actions{grid-column:1/-1}
      .more-actions summary{list-style:none;text-align:center;color:#667169;font-size:11px;cursor:pointer;padding:6px;border-radius:8px}
      .more-actions summary::-webkit-details-marker{display:none}
      .more-actions summary:hover{background:rgba(255,255,255,.48)}
      .more-grid{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-top:7px}
      .more-grid button:first-child{grid-column:1/-1}
      .interacting{opacity:.25}
      .interacting .body{display:none}
      @media(max-width:520px){.box{right:12px;bottom:12px}.counts-row{grid-template-columns:1fr}}
      @media(prefers-reduced-motion:reduce){button{transition:none}}
    </style>
    <section class="box" aria-live="polite">
      <header><span class="dot"></span><span class="title">Preparing</span><button class="collapse" type="button" aria-label="Collapse JobPilot" title="Collapse">−</button></header>
      <div class="body">
        <div class="message">Preparing your application…</div>
        <div class="count"></div>
        <div class="counts-row"></div>
        <button class="review-toggle" type="button" aria-expanded="false" style="display:none"></button>
        <div class="review-panel"></div>
        <div class="teach-panel"></div>
        <div class="actions">
          <button data-a="retry">Retry autofill</button>
          <button data-a="continue">Continue filling</button>
          <button data-a="next">Jump to next issue</button>
          <button data-a="complete">Mark application complete</button>
          <details class="more-actions">
            <summary>More options</summary>
            <div class="more-grid">
              <button data-a="clear">Clear JobPilot-filled fields</button>
              <button data-a="teach" aria-pressed="false" title="Watch how you answer, and offer to remember it">Teach JobPilot</button>
              <button data-a="diagnostics" title="Copy sanitized field diagnostics (no personal data)">Copy diagnostics</button>
            </div>
          </details>
        </div>
      </div>
    </section>`;
  document.documentElement.appendChild(host);
  const box = root.querySelector<HTMLElement>(".box")!;
  const reviewToggle = root.querySelector<HTMLButtonElement>(".review-toggle")!;
  const reviewPanel = root.querySelector<HTMLElement>(".review-panel")!;
  const countsRow = root.querySelector<HTMLElement>(".counts-row")!;

  const collapseBtn = root.querySelector<HTMLButtonElement>(".collapse")!;
  collapseBtn.addEventListener("click", () => {
    const collapsed = box.classList.toggle("collapsed");
    collapseBtn.textContent = collapsed ? "+" : "−";
    collapseBtn.setAttribute("aria-label", collapsed ? "Expand JobPilot" : "Collapse JobPilot");
    collapseBtn.title = collapsed ? "Expand" : "Collapse";
  });
  root.querySelector('[data-a="retry"]')?.addEventListener("click", actions.retry);
  root.querySelector('[data-a="continue"]')?.addEventListener("click", actions.retry);
  root.querySelector('[data-a="clear"]')?.addEventListener("click", actions.clear);
  const completeBtn = root.querySelector<HTMLButtonElement>('[data-a="complete"]')!;
  completeBtn.addEventListener("click", () => {
    if (!completeBtn.disabled) actions.complete();
  });
  completeBtn.disabled = true; // stays disabled until a ledger with no required blanks arrives
  const diagnosticsBtn = root.querySelector<HTMLButtonElement>('[data-a="diagnostics"]')!;
  if (actions.diagnostics) diagnosticsBtn.addEventListener("click", actions.diagnostics);
  else diagnosticsBtn.style.display = "none";
  reviewToggle.addEventListener("click", () => {
    const open = reviewPanel.classList.toggle("open");
    reviewToggle.setAttribute("aria-expanded", String(open));
  });
  const teachPanel = root.querySelector<HTMLElement>(".teach-panel")!;
  const teachBtn = root.querySelector<HTMLButtonElement>('[data-a="teach"]')!;
  if (actions.teach) {
    teachBtn.addEventListener("click", () => {
      const enabled = teachBtn.getAttribute("aria-pressed") !== "true";
      teachBtn.setAttribute("aria-pressed", String(enabled));
      teachBtn.textContent = enabled ? "Stop teaching" : "Teach JobPilot";
      actions.teach!(enabled);
    });
  } else {
    teachBtn.style.display = "none";
  }

  let items: ReviewItem[] = [];
  let handlers: ReviewHandlers | null = null;
  let ledgerCounts: LedgerCounts | null = null;

  /** Completion is BLOCKED while any required control is still blank — either a
   * ledger-reported required blank, or a pending required review item (incl.
   * name confirmation). "Ready for review" ≠ "complete": the user still submits. */
  function updateCompleteGate(): void {
    const pendingRequired = items.some((i) => !i.resolved && i.required);
    const requiredBlank = (ledgerCounts?.requiredBlank ?? 0) > 0 || pendingRequired;
    completeBtn.disabled = requiredBlank;
    completeBtn.title = requiredBlank
      ? "Every required field must be filled before you can mark this application complete."
      : "";
  }

  root.querySelector('[data-a="next"]')?.addEventListener("click", () => {
    const next = items.find((i) => !i.resolved);
    if (!next) {
      document.querySelector<HTMLElement>('[data-jobpilot-status="review"],[data-jobpilot-status="invalid"]')?.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    box.classList.remove("collapsed");
    reviewPanel.classList.add("open");
    reviewToggle.setAttribute("aria-expanded", "true");
    handlers?.onJumpToField(next.id);
    root.querySelector<HTMLElement>(`[data-item="${cssId(next.id)}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  });

  function cssId(id: string): string {
    return id.replace(/"/g, "");
  }

  function renderCounts(): void {
    // Counts derive from the ledger when available (the single source of truth);
    // the item-derived fallback keeps the widget usable in isolation/tests.
    const resolvedHere = items.filter((i) => i.resolved).length;
    const c = ledgerCounts;
    if (c) {
      // Render the ledger's own numbers verbatim — it is refreshed (refreshCounts)
      // every time the user resolves an item, so no local re-derivation is needed
      // and the displayed counts can never drift from the ledger.
      countsRow.innerHTML =
        `<span>Discovered: <b>${c.discovered}</b></span>` +
        `<span>Filled: <b>${c.filled}</b></span>` +
        `<span>Needs information: <b>${c.needsInformation}</b></span>` +
        `<span>Needs confirmation: <b>${c.needsConfirmation}</b></span>` +
        `<span>Sensitive/consent: <b>${c.sensitive}</b></span>` +
        `<span>Technical issues: <b>${c.technical}</b></span>` +
        `<span>Optional skipped: <b>${c.optionalSkipped}</b></span>`;
    } else {
      const needsInfo = items.filter((i) => !i.resolved && (i.category === "required" || i.category === "optional")).length;
      const needsConfirm = items.filter((i) => !i.resolved && i.category === "sensitive").length;
      const technical = items.filter((i) => !i.resolved && i.category === "technical").length;
      countsRow.innerHTML = items.length
        ? `<span>Filled: <b>${resolvedHere}</b></span><span>Needs information: <b>${needsInfo}</b></span><span>Needs confirmation: <b>${needsConfirm}</b></span><span>Technical issues: <b>${technical}</b></span>`
        : "";
    }
    const remaining = items.filter((i) => !i.resolved).length;
    reviewToggle.style.display = items.length ? "" : "none";
    reviewToggle.textContent = remaining > 0 ? `${remaining} question${remaining === 1 ? "" : "s"} need your input` : "All caught up";
    updateCompleteGate();
  }

  function renderReview(): void {
    reviewPanel.innerHTML = "";
    const order: ReviewCategory[] = ["required", "sensitive", "optional", "application", "technical"];
    const pending = items.filter((i) => !i.resolved);
    if (pending.length === 0) {
      reviewPanel.innerHTML = `<div class="empty">Nothing left to review.</div>`;
      renderCounts();
      return;
    }
    for (const category of order) {
      const inGroup = pending.filter((i) => i.category === category);
      if (inGroup.length === 0) continue;
      const heading = document.createElement("div");
      heading.className = "group-title";
      heading.textContent = CATEGORY_LABEL[category];
      reviewPanel.appendChild(heading);
      for (const item of inGroup) reviewPanel.appendChild(renderItem(item));
    }
    renderCounts();
  }

  function renderItem(item: ReviewItem): HTMLElement {
    const el = document.createElement("div");
    el.className = "item";
    el.setAttribute("data-item", item.id);
    const reqBadge = item.required ? `<span class="req">Required</span>` : "";
    el.innerHTML = `
      <div class="q">${escapeHtml(item.question)}${reqBadge}</div>
      <div class="why">${escapeHtml(item.reasonText)}</div>`;

    if (item.kind === "name_confirm") {
      const parts = (item.suggestedValue || "").split("|");
      const given = textInput("First / given name", parts[0] || "");
      const middle = textInput("Middle name (optional)", parts[1] || "");
      const family = textInput("Last / family name", parts[2] || "");
      const prefFirst = textInput("Preferred first name (optional)", parts[3] || "");
      const prefLast = textInput("Preferred last name (optional)", parts[4] || "");
      el.appendChild(given);
      el.appendChild(middle);
      el.appendChild(family);
      el.appendChild(prefFirst);
      el.appendChild(prefLast);
      const row = document.createElement("div");
      row.className = "row";
      const saveBtn = document.createElement("button");
      saveBtn.type = "button";
      saveBtn.textContent = "Confirm and fill";
      saveBtn.addEventListener("click", async () => {
        saveBtn.disabled = true;
        // first|middle|last|preferredFirst|preferredLast. Middle may be blank
        // (many people have none). Blank preferred fields fall back to the legal
        // name, for the common "enter your legal name" preferred-name field.
        const value = [
          given.value.trim(),
          middle.value.trim(),
          family.value.trim(),
          prefFirst.value.trim(),
          prefLast.value.trim()
        ].join("|");
        const ok = given.value.trim() && family.value.trim() ? await handlers?.onFill(item.id, value) : false;
        if (ok) markResolved(item.id);
        else saveBtn.disabled = false;
      });
      row.appendChild(saveBtn);
      el.appendChild(row);
      if (item.resolved) el.classList.add("resolved");
      return el;
    }

    // How to read the chosen value back — a pipe-joined string for multi-select,
    // the single value otherwise.
    if (item.control === "file") {
      const row = document.createElement("div");
      row.className = "row";
      const attachBtn = document.createElement("button");
      attachBtn.type = "button";
      attachBtn.textContent = "Show attach field";
      attachBtn.addEventListener("click", () => handlers?.onJumpToField(item.id));
      row.appendChild(attachBtn);
      if (!item.required) {
        const skip = document.createElement("button");
        skip.type = "button";
        skip.textContent = "Skip";
        skip.addEventListener("click", () => markResolved(item.id));
        row.appendChild(skip);
      }
      el.appendChild(row);
      return el;
    }

    let readValue: () => string | string[];
    let displayValue: () => string;
    if (item.multiple && item.options && item.options.length > 0) {
      // Multi-select: real checkboxes, one per discovered option. The user may
      // pick several; each is filled deterministically. Never a single-select.
      const boxes: HTMLInputElement[] = [];
      for (const opt of item.options) {
        const row = document.createElement("label");
        row.className = "save";
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.value = opt;
        boxes.push(cb);
        row.appendChild(cb);
        row.appendChild(document.createTextNode(opt));
        el.appendChild(row);
      }
      readValue = () => boxes.filter((b) => b.checked).map((b) => b.value);
      displayValue = () => boxes.filter((b) => b.checked).map((b) => b.value).join(", ");
    } else if (item.options && item.options.length > 0) {
      const select = document.createElement("select");
      select.appendChild(new Option("Select an answer…", ""));
      for (const opt of item.options) select.appendChild(new Option(opt, opt));
      el.appendChild(select);
      readValue = () => select.value.trim();
      displayValue = () => select.options[select.selectedIndex]?.text ?? select.value;
    } else {
      const input = document.createElement("input");
      input.type = "text";
      input.placeholder = inputPlaceholder(item);
      el.appendChild(input);
      readValue = () => input.value.trim();
      displayValue = () => input.value.trim();
    }

    let saveCheckbox: HTMLInputElement | null = null;
    if (item.reusable) {
      const label = document.createElement("label");
      label.className = "save";
      saveCheckbox = document.createElement("input");
      saveCheckbox.type = "checkbox";
      saveCheckbox.checked = true;
      const scopeNote = item.defaultScope === "company" ? " (this employer only)" : "";
      label.appendChild(saveCheckbox);
      label.appendChild(document.createTextNode(`Save for future applications${scopeNote}`));
      el.appendChild(label);
    }

    const row = document.createElement("div");
    row.className = "row";
    const fillBtn = document.createElement("button");
    fillBtn.type = "button";
    fillBtn.textContent = item.reusable ? "Save and fill" : "Use this answer";
    fillBtn.addEventListener("click", async () => {
      const value = readValue();
      if (Array.isArray(value) ? value.length === 0 : !value) return;
      fillBtn.disabled = true;
      const filled = await handlers?.onFill(item.id, value);
      if (filled && saveCheckbox?.checked) {
        await handlers?.onSave(item.id, value, displayValue());
      }
      if (filled) markResolved(item.id);
      else fillBtn.disabled = false;
    });
    row.appendChild(fillBtn);
    if (!item.required) {
      const skip = document.createElement("button");
      skip.type = "button";
      skip.textContent = "Skip";
      skip.addEventListener("click", () => markResolved(item.id));
      row.appendChild(skip);
    }
    el.appendChild(row);
    if (item.resolved) el.classList.add("resolved");
    return el;
  }

  function markResolved(id: string): void {
    const item = items.find((i) => i.id === id);
    if (item) item.resolved = true;
    renderReview();
  }

  return {
    update(value) {
      const labels: Record<WidgetStage, string> = { preparing:"Preparing", opening:"Opening application", detecting:"Detecting fields", filling:"Filling", uploading:"Uploading resume", review:"Needs review", ready:"Ready for review", failed:"Failed" };
      root.querySelector<HTMLElement>(".title")!.textContent = labels[value.stage];
      root.querySelector<HTMLElement>(".message")!.textContent = value.message ?? labels[value.stage];
      root.querySelector<HTMLElement>(".count")!.textContent = value.total == null ? "" : `Filled ${value.filled ?? 0} of ${value.total}`;
      box.classList.toggle("failed", value.stage === "failed"); box.classList.toggle("review", value.stage === "review");
      // A terminal failure (expired/consumed/mismatched handoff) must not
      // offer a Retry that just re-asks the same unanswerable question.
      const terminal = value.stage === "failed" && value.recoverable === false;
      const retryBtn = root.querySelector<HTMLButtonElement>('[data-a="retry"]');
      const continueBtn = root.querySelector<HTMLButtonElement>('[data-a="continue"]');
      if (retryBtn) { retryBtn.disabled = terminal; retryBtn.title = terminal ? "Reopen the application from JobPilot instead." : ""; }
      if (continueBtn) { continueBtn.disabled = terminal; continueBtn.title = terminal ? "Reopen the application from JobPilot instead." : ""; }
    },
    showReview(nextItems, nextHandlers, counts) {
      items = nextItems.map((i) => ({ ...i }));
      handlers = nextHandlers;
      ledgerCounts = counts ?? null;
      renderReview();
    },
    refreshCounts(counts) {
      ledgerCounts = counts;
      renderCounts();
    },
    askToRemember(prompt) {
      teachPanel.appendChild(renderRememberPrompt(prompt));
      box.classList.remove("collapsed");
    },
    setInteractionMode(active) {
      // Collapse to the header and become click-through. The widget is fixed
      // bottom-right, exactly where a dropdown menu often opens; leaving it
      // interactive would let it swallow the option click.
      box.classList.toggle("interacting", active);
      host.style.pointerEvents = active ? "none" : "";
    },
    destroy: () => host.remove()
  };
}

/** Render the "Should JobPilot remember this answer?" card. Every option is an
 * explicit user choice — there is no default that silently saves. */
function renderRememberPrompt(prompt: RememberPrompt): HTMLElement {
  const el = document.createElement("div");
  el.className = "teach-item";
  el.setAttribute("data-teach", prompt.id);
  const scopeNote = prompt.employer && !prompt.sensitive ? ` (${prompt.employer})` : "";
  el.innerHTML = `
    <div class="q">Should JobPilot remember this answer?</div>
    <div class="why">${escapeHtml(prompt.question)}</div>
    <div class="why"><b>${escapeHtml(prompt.chosenSummary)}</b>${escapeHtml(scopeNote)}</div>`;

  const scopes = document.createElement("div");
  scopes.className = "scopes";
  // A sensitive answer may ONLY be stored via its explicit-consent option.
  const choices: RememberPrompt["proposedScope"][] = prompt.sensitive
    ? ["sensitive", "application", "none"]
    : ["application", "global", "company", "none"];
  const name = `scope-${prompt.id}`;
  for (const scope of choices) {
    const label = document.createElement("label");
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = name;
    radio.value = scope;
    // Only preselect when JobPilot is confident; otherwise the user must choose.
    if (prompt.scopeConfident && scope === prompt.proposedScope) radio.checked = true;
    label.appendChild(radio);
    label.appendChild(document.createTextNode(SCOPE_LABEL[scope]));
    scopes.appendChild(label);
  }
  el.appendChild(scopes);

  const row = document.createElement("div");
  row.className = "row";
  const save = document.createElement("button");
  save.type = "button";
  save.textContent = "Confirm";
  save.addEventListener("click", () => {
    const chosen = scopes.querySelector<HTMLInputElement>("input:checked");
    if (!chosen) return; // no silent default — the user must decide
    save.disabled = true;
    prompt.onDecision(chosen.value as RememberPrompt["proposedScope"]);
    el.remove();
  });
  row.appendChild(save);
  const dismiss = document.createElement("button");
  dismiss.type = "button";
  dismiss.textContent = "Not now";
  dismiss.addEventListener("click", () => {
    prompt.onDecision("none");
    el.remove();
  });
  row.appendChild(dismiss);
  el.appendChild(row);
  return el;
}

function textInput(placeholder: string, value: string): HTMLInputElement {
  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = placeholder;
  input.value = value;
  return input;
}

function inputPlaceholder(item: ReviewItem): string {
  const question = item.question.toLowerCase();
  if (question.includes("school") || question.includes("university")) return "e.g. Arizona State University";
  if (question.includes("degree")) return "e.g. Master's Degree";
  if (question.includes("major") || question.includes("field of study")) return "e.g. Computer Science";
  if (question.includes("calling code")) return "e.g. United States (+1)";
  if (question.includes("country")) return "Enter a country";
  if (question.includes("graduat")) return "YYYY";
  return "Enter your answer";
}

function escapeHtml(text: string): string {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}
