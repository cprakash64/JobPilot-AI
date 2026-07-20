/**
 * Realistic dropdown COMPONENT simulators for jsdom tests.
 *
 * A bare `<li role="option">` in a fixture is inert: clicking it changes
 * nothing, so a test that asserts "filled" against it is really asserting that
 * JobPilot lies. These helpers attach the same event contract a real React
 * Select / ARIA combobox implements — open on pointer/mouse down, render the
 * menu (optionally async, optionally in a portal), commit a selection on option
 * click, update the displayed value, and close.
 *
 * Our adapter must drive these exactly as it would drive the real thing.
 */

export interface ComponentOptions {
  /** The element that opens the menu (React Select's `__control` wrapper). */
  control: HTMLElement;
  /** The listbox/menu element. Hidden until opened. */
  menu: HTMLElement;
  /** Option labels; rendered into the menu when it opens. */
  options: string[];
  /** Element that shows the current selection (React Select's `singleValue`). */
  display?: HTMLElement;
  multiple?: boolean;
  /** Simulate options that arrive asynchronously. */
  openDelayMs?: number;
  /** Only open via keyboard (ArrowDown) — models a control that ignores clicks. */
  keyboardOnly?: boolean;
  /** Filter options by the typed text of this input. */
  searchInput?: HTMLInputElement;
  /** Render no options until the user types a query (remote autocomplete). */
  requireSearch?: boolean;
  /** Refuse the first N open attempts (retry testing). */
  failOpens?: number;
  /** An input-backed ARIA combobox commits the chosen label into this input. */
  valueInput?: HTMLInputElement;
  /** An always-visible option list (the control IS the listbox): never hidden,
   * no aria-expanded toggling. Hiding it would hide the control itself. */
  alwaysOpen?: boolean;
}

export function attachDropdownComponent(config: ComponentOptions): { openCount: () => number } {
  const { control, menu, options, display, multiple = false } = config;
  let openAttempts = 0;
  const selected = new Set<string>();

  if (config.alwaysOpen) {
    renderOptionsDeferred();
  } else {
    menu.style.display = "none";
    control.setAttribute("aria-expanded", "false");
  }

  const renderOptions = (): void => {
    const filter = config.searchInput?.value?.trim().toLowerCase() ?? "";
    const visible = config.requireSearch && !filter
      ? []
      : filter ? options.filter((o) => o.toLowerCase().includes(filter)) : options;
    menu.innerHTML = "";
    for (const label of visible) {
      const option = menu.ownerDocument.createElement("div");
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", selected.has(label) ? "true" : "false");
      option.textContent = label;
      option.addEventListener("click", () => commit(label));
      menu.appendChild(option);
    }
  };

  function renderOptionsDeferred(): void {
    // Defined after renderOptions via hoisting of the const below; call lazily.
    queueMicrotask(() => renderOptions());
  }

  const openMenu = (): void => {
    if (config.alwaysOpen) {
      renderOptions();
      return;
    }
    openAttempts += 1;
    if (config.failOpens && openAttempts <= config.failOpens) return; // simulate a flaky first open
    const show = (): void => {
      renderOptions();
      menu.style.display = "block";
      control.setAttribute("aria-expanded", "true");
    };
    if (config.openDelayMs) setTimeout(show, config.openDelayMs);
    else show();
  };

  const closeMenu = (): void => {
    if (config.alwaysOpen) return;
    menu.style.display = "none";
    control.setAttribute("aria-expanded", "false");
  };

  const commit = (label: string): void => {
    if (multiple) {
      if (selected.has(label)) selected.delete(label);
      else selected.add(label);
      renderOptions(); // a multi-select menu stays open
    } else {
      selected.clear();
      selected.add(label);
      closeMenu();
    }
    if (display) {
      if (multiple) {
        // Real React Select renders ONE chip element per selected value, not a
        // single joined string — readSelection must return them as an array.
        display.innerHTML = "";
        for (const value of selected) {
          const chip = display.ownerDocument.createElement("span");
          chip.className = "select__multi-value";
          chip.textContent = value;
          display.appendChild(chip);
        }
        display.className = "select__value";
      } else {
        display.textContent = label;
        // Mirror React Select's class names, KEEPING the original hook class.
        display.className = "select__value select__singleValue";
      }
    }
    if (config.valueInput) {
      // An input-backed ARIA combobox commits the selected label into its input.
      config.valueInput.value = multiple ? Array.from(selected).join(", ") : label;
      config.valueInput.dispatchEvent(new Event("input", { bubbles: true }));
    }
    control.dispatchEvent(new Event("change", { bubbles: true }));
  };

  if (!config.keyboardOnly) {
    // React Select opens on mousedown, not click.
    control.addEventListener("mousedown", () => {
      if (control.getAttribute("aria-expanded") === "true") return;
      openMenu();
    });
  }
  control.addEventListener("keydown", (event) => {
    const key = (event as KeyboardEvent).key;
    if (key === "ArrowDown" && control.getAttribute("aria-expanded") !== "true") openMenu();
    if (key === "Escape") closeMenu();
  });
  config.searchInput?.addEventListener("input", () => {
    if (control.getAttribute("aria-expanded") === "true") renderOptions();
  });

  return { openCount: () => openAttempts };
}

/**
 * Give the Samsara/Greenhouse fixture's CUSTOM dropdowns real component
 * behaviour, so a test exercises the same contract as the live application:
 * the country combobox, the sponsorship combobox, and the "Where have you
 * learned about Samsara?" multi-select. Native <select> controls need no
 * simulation — the browser implements them.
 */
export function activateSamsaraDropdowns(doc: Document): void {
  const country = doc.getElementById("country");
  const countryMenu = doc.getElementById("country-menu");
  if (country && countryMenu) {
    const value = doc.createElement("span");
    value.className = "select__value";
    country.appendChild(value);
    attachDropdownComponent({
      control: country,
      menu: countryMenu,
      options: ["United States", "Canada", "India"],
      display: value
    });
  }

  const sponsor = doc.getElementById("sponsor") as HTMLInputElement | null;
  const sponsorMenu = doc.getElementById("sponsor-menu");
  if (sponsor && sponsorMenu) {
    attachDropdownComponent({
      control: sponsor,
      menu: sponsorMenu,
      options: ["Yes", "No"],
      valueInput: sponsor
    });
  }

  const learned = doc.getElementById("learned");
  if (learned) {
    // An always-rendered multi-select list: options live inside the control.
    const value = doc.createElement("span");
    value.className = "select__value";
    learned.parentElement?.appendChild(value);
    attachDropdownComponent({
      control: learned,
      menu: learned,
      multiple: true,
      alwaysOpen: true,
      options: ["LinkedIn", "Instagram", "Friend or colleague", "Industry event", "Other"],
      display: value
    });
  }
}

/** Build a React-Select-shaped control whose menu renders in a document.body
 * portal — the shape that defeats "look for the menu next to the control". */
export function mountPortalReactSelect(doc: Document, opts: { id: string; label: string; options: string[]; required?: boolean }): HTMLElement {
  doc.body.innerHTML += `
    <form id="${opts.id}-form">
      <div class="field"${opts.required ? ' data-required="true"' : ""}>
        <span id="${opts.id}-label">${opts.label}</span>
        <div id="${opts.id}" class="select__control" role="combobox"
             aria-labelledby="${opts.id}-label" aria-expanded="false" aria-controls="${opts.id}-menu">
          <span class="select__placeholder">Select...</span>
          <span class="select__value"></span>
          <input class="select__input" type="text" />
        </div>
      </div>
    </form>`;
  // The menu lives in a portal at the END of body, far from the control.
  const portal = doc.createElement("div");
  portal.className = "select__menu-portal";
  portal.innerHTML = `<div id="${opts.id}-menu" class="select__menu" role="listbox"></div>`;
  doc.body.appendChild(portal);

  const control = doc.getElementById(opts.id)!;
  attachDropdownComponent({
    control,
    menu: doc.getElementById(`${opts.id}-menu`)!,
    options: opts.options,
    display: control.querySelector<HTMLElement>(".select__value")!,
    searchInput: control.querySelector<HTMLInputElement>(".select__input")!
  });
  return control;
}
