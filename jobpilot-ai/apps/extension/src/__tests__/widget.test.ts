/**
 * Review-widget behavior (task section G/K): unresolved questions render
 * with their real discovered options, "Save and fill" invokes both the fill
 * and save handlers, a company-scoped item is labeled as such, and resolving
 * an item removes it from the pending list without a page reload.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createWidget, type ReviewHandlers, type ReviewItem } from "../content/widget";

// The widget uses attachShadow({ mode: "closed" }) so ATS page scripts can't
// reach into it — which also hides it from Element.shadowRoot in a real
// browser (and in jsdom). Capture the root via the attachShadow call itself,
// exactly as the browser's own devtools/testing hooks would.
let capturedRoot: ShadowRoot | null = null;
let originalAttachShadow: typeof Element.prototype.attachShadow;

beforeEach(() => {
  originalAttachShadow = Element.prototype.attachShadow;
  Element.prototype.attachShadow = function (init: ShadowRootInit) {
    const root = originalAttachShadow.call(this, { ...init, mode: "open" });
    capturedRoot = root;
    return root;
  };
});

afterEach(() => {
  Element.prototype.attachShadow = originalAttachShadow;
  capturedRoot = null;
  document.body.innerHTML = "";
});

function mountWidget() {
  return createWidget({ retry: () => {}, clear: () => {}, complete: () => {} });
}

function shadowRoot(): ShadowRoot {
  return capturedRoot!;
}

describe("review widget", () => {
  it("replaces a frozen widget left by an invalidated content script", () => {
    const old = document.createElement("div");
    old.id = "jobpilot-assisted-apply";
    old.dataset.instance = "stale";
    document.documentElement.appendChild(old);

    const widget = mountWidget();
    const current = document.querySelectorAll("#jobpilot-assisted-apply");
    expect(current).toHaveLength(1);
    expect((current[0] as HTMLElement).dataset.instance).not.toBe("stale");
    widget.destroy();
  });

  it("renders an unresolved dropdown item with the real ATS options and a company-scope note", () => {
    const widget = mountWidget();
    const items: ReviewItem[] = [
      {
        id: "uid-1", kind: "field", category: "application", question: "How did you hear about us?",
        required: false, reasonText: "JobPilot doesn't have a confirmed answer for this question.",
        options: ["LinkedIn", "Referral", "Other"], control: "select", reusable: true, defaultScope: "company"
      }
    ];
    const handlers: ReviewHandlers = { onFill: vi.fn(async () => true), onSave: vi.fn(async () => true), onJumpToField: vi.fn() };
    widget.showReview(items, handlers);

    const root = shadowRoot();
    const select = root.querySelector("select")!;
    expect(Array.from(select.options).map((o) => o.value)).toEqual(expect.arrayContaining(["LinkedIn", "Referral", "Other"]));
    expect(root.textContent).toContain("this employer only");
    widget.destroy();
  });

  it("'Save and fill' calls onFill then onSave with the chosen value, and resolves the item", async () => {
    const widget = mountWidget();
    const items: ReviewItem[] = [
      {
        id: "uid-2", kind: "field", category: "optional", question: "Pronouns",
        required: false, reasonText: "Needs your input.", options: ["He/him", "She/her", "They/them"],
        control: "select", reusable: true, defaultScope: "global"
      }
    ];
    const onFill = vi.fn(async () => true);
    const onSave = vi.fn(async () => true);
    widget.showReview(items, { onFill, onSave, onJumpToField: vi.fn() });

    const root = shadowRoot();
    const select = root.querySelector("select") as HTMLSelectElement;
    select.value = "They/them";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    const fillBtn = Array.from(root.querySelectorAll("button")).find((b) => b.textContent?.includes("Save and fill"))!;
    fillBtn.click();
    await Promise.resolve();
    await Promise.resolve();

    expect(onFill).toHaveBeenCalledWith("uid-2", "They/them");
    expect(onSave).toHaveBeenCalledWith("uid-2", "They/them", "They/them");
    widget.destroy();
  });

  it("does not offer a save checkbox for a non-reusable (custom/application-specific) question", () => {
    const widget = mountWidget();
    const items: ReviewItem[] = [
      {
        id: "uid-3", kind: "field", category: "application", question: "Why do you want to work here?",
        required: false, reasonText: "Needs your input.", control: "text", reusable: false
      }
    ];
    widget.showReview(items, { onFill: vi.fn(async () => true), onSave: vi.fn(async () => true), onJumpToField: vi.fn() });
    const root = shadowRoot();
    expect(root.querySelector('input[type="checkbox"]')).toBeNull();
    widget.destroy();
  });

  it("uses a specific placeholder instead of a generic answer prompt", () => {
    const widget = mountWidget();
    const items: ReviewItem[] = [
      {
        id: "uid-degree", kind: "field", category: "required",
        question: "What type of degree did you earn?", required: true,
        reasonText: "Choose the closest degree level offered by the employer.",
        control: "text", reusable: true
      }
    ];
    widget.showReview(items, { onFill: vi.fn(async () => true), onSave: vi.fn(async () => true), onJumpToField: vi.fn() });
    expect((shadowRoot().querySelector('input[type="text"]') as HTMLInputElement).placeholder).toBe("e.g. Master's Degree");
    expect(shadowRoot().textContent).not.toContain("Fill this application");
    widget.destroy();
  });

  it("a name-confirmation item shows first/middle/last separately and never offers a save checkbox", async () => {
    const widget = mountWidget();
    const items: ReviewItem[] = [
      {
        id: "name_confirm", kind: "name_confirm", category: "required", question: "Confirm your name",
        required: true, reasonText: "Confirm how your name should be split.",
        // first|middle|last|preferredFirst|preferredLast
        suggestedValue: "Chandra|Prakash|Pandey||", reusable: false
      }
    ];
    const onFill = vi.fn(async () => true);
    widget.showReview(items, { onFill, onSave: vi.fn(async () => true), onJumpToField: vi.fn() });

    const root = shadowRoot();
    const inputs = root.querySelectorAll('input[type="text"]');
    // The middle name is its own field — this is what stops "Prakash Pandey"
    // from being offered as the last name.
    expect((inputs[0] as HTMLInputElement).value).toBe("Chandra");
    expect((inputs[1] as HTMLInputElement).value).toBe("Prakash");
    expect((inputs[2] as HTMLInputElement).value).toBe("Pandey");
    // Five inputs: first, middle, last, preferred first, preferred last (A/D).
    expect(inputs.length).toBe(5);
    expect(root.querySelector('input[type="checkbox"]')).toBeNull();

    const confirmBtn = Array.from(root.querySelectorAll("button")).find((b) => b.textContent?.includes("Confirm and fill"))!;
    confirmBtn.click();
    await Promise.resolve();
    await Promise.resolve();
    // Blank preferred fields are carried as empty segments (filled from the legal
    // name downstream). first|middle|last|preferredFirst|preferredLast.
    expect(onFill).toHaveBeenCalledWith("name_confirm", "Chandra|Prakash|Pandey||");
    widget.destroy();
  });

  it("resolving an item updates the pending count without any page reload", async () => {
    const widget = mountWidget();
    const items: ReviewItem[] = [
      { id: "uid-4", kind: "field", category: "optional", question: "Q1", required: false, reasonText: "x", control: "text", reusable: false },
      { id: "uid-5", kind: "field", category: "optional", question: "Q2", required: false, reasonText: "x", control: "text", reusable: false }
    ];
    widget.showReview(items, { onFill: vi.fn(async () => true), onSave: vi.fn(async () => true), onJumpToField: vi.fn() });
    const root = shadowRoot();
    expect(root.querySelector(".review-toggle")?.textContent).toContain("2 questions");

    // Resolve uid-5 via Skip first (it's optional) — this re-renders the
    // whole panel, so uid-4's controls are queried fresh afterward.
    const skipBtn = root.querySelector('[data-item="uid-5"] button:not(:first-of-type)') as HTMLButtonElement;
    skipBtn?.click();

    const freshInput = root.querySelector('[data-item="uid-4"] input') as HTMLInputElement;
    freshInput.value = "answer";
    freshInput.dispatchEvent(new Event("change", { bubbles: true }));
    const fillBtn = root.querySelector('[data-item="uid-4"] button') as HTMLButtonElement;
    fillBtn.click();
    await Promise.resolve();
    await Promise.resolve();

    expect(root.querySelector(".review-toggle")?.textContent).toContain("All caught up");
    widget.destroy();
  });
});

describe("Teach JobPilot — consent before anything is remembered", () => {
  it("never saves without an explicit scope choice", () => {
    const widget = mountWidget();
    const onDecision = vi.fn();
    widget.askToRemember({
      id: "t1", question: "Where did you hear about us?", chosenSummary: "Employee referral",
      employer: "Samsara", sensitive: false, proposedScope: "company", scopeConfident: false, onDecision
    });
    const root = shadowRoot();
    // Nothing is preselected when the scope proposal is a guess.
    expect(root.querySelector('.teach-item input:checked')).toBeNull();
    // Confirming without choosing does nothing at all.
    const confirm = Array.from(root.querySelectorAll(".teach-item button")).find((b) => b.textContent === "Confirm") as HTMLButtonElement;
    confirm.click();
    expect(onDecision).not.toHaveBeenCalled();
    widget.destroy();
  });

  it("records the scope the user actually picked", () => {
    const widget = mountWidget();
    const onDecision = vi.fn();
    widget.askToRemember({
      id: "t2", question: "Country", chosenSummary: "United States",
      employer: "Samsara", sensitive: false, proposedScope: "global", scopeConfident: true, onDecision
    });
    const root = shadowRoot();
    const company = root.querySelector<HTMLInputElement>('.teach-item input[value="company"]')!;
    company.checked = true;
    (Array.from(root.querySelectorAll(".teach-item button")).find((b) => b.textContent === "Confirm") as HTMLButtonElement).click();
    expect(onDecision).toHaveBeenCalledWith("company");
    widget.destroy();
  });

  it("a SENSITIVE answer only offers explicit-consent scopes (never 'all applications')", () => {
    const widget = mountWidget();
    widget.askToRemember({
      id: "t3", question: "Gender identity", chosenSummary: "(sensitive answer)",
      employer: "Samsara", sensitive: true, proposedScope: "sensitive", scopeConfident: true,
      onDecision: vi.fn()
    });
    const root = shadowRoot();
    const values = Array.from(root.querySelectorAll<HTMLInputElement>(".teach-item input[type=radio]")).map((r) => r.value);
    expect(values).toContain("sensitive");
    expect(values).not.toContain("global");
    expect(values).not.toContain("company");
    widget.destroy();
  });

  it("'Not now' declines without saving", () => {
    const widget = mountWidget();
    const onDecision = vi.fn();
    widget.askToRemember({
      id: "t4", question: "Pronouns", chosenSummary: "They/them", employer: null,
      sensitive: false, proposedScope: "global", scopeConfident: true, onDecision
    });
    const root = shadowRoot();
    (Array.from(root.querySelectorAll(".teach-item button")).find((b) => b.textContent === "Not now") as HTMLButtonElement).click();
    expect(onDecision).toHaveBeenCalledWith("none");
    widget.destroy();
  });
});
