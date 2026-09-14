"use strict";

// Shared select-only combobox. Keep focus on the trigger while navigating a list
// rendered outside cards, so their overflow rules cannot clip the menu.
class WorkbenchDropdown {
  static opened = null;

  constructor(root, { label, placeholder = "请选择", onChange }) {
    this.root = root;
    this.label = label;
    this.placeholder = placeholder;
    this.onChange = onChange;
    this.choices = [];
    this.value = null;
    this.active = -1;
    this.search = "";
    this.searchAt = 0;
    root.classList.add("dropdown");
    this.trigger = document.createElement("button");
    this.trigger.type = "button";
    this.trigger.id = `${root.id}-trigger`;
    this.trigger.className = "dropdown-trigger";
    this.trigger.setAttribute("role", "combobox");
    this.trigger.setAttribute("aria-haspopup", "listbox");
    this.trigger.setAttribute("aria-expanded", "false");
    this.caption = document.createElement("span");
    this.caption.className = "dropdown-caption";
    const chevron = document.createElement("span");
    chevron.className = "dropdown-chevron";
    chevron.setAttribute("aria-hidden", "true");
    this.trigger.append(this.caption, chevron);
    root.replaceChildren(this.trigger);

    this.menu = document.createElement("div");
    this.menu.id = `${root.id}-options`;
    this.menu.className = "dropdown-menu";
    this.menu.setAttribute("role", "listbox");
    this.menu.setAttribute("aria-label", label);
    this.menu.hidden = true;
    document.body.append(this.menu);
    this.trigger.setAttribute("aria-controls", this.menu.id);
    this.trigger.addEventListener("click", () => this.isOpen ? this.close() : this.open());
    this.trigger.addEventListener("keydown", (event) => this.keydown(event));
    this.menu.addEventListener("pointerdown", (event) => {
      if (event.pointerType === "mouse" && event.target.closest(".dropdown-option"))
        event.preventDefault();
    });
    this.update([], null);
  }

  get isOpen() { return WorkbenchDropdown.opened === this; }

  update(choices, value, disabled = false) {
    const activeValue = this.choices[this.active]?.value;
    const changed = JSON.stringify(choices) !== JSON.stringify(this.choices);
    this.choices = choices;
    this.value = value;
    const current = choices.find((choice) => choice.value === value);
    const caption = current?.text || this.placeholder;
    this.caption.textContent = caption;
    this.trigger.title = caption;
    this.trigger.setAttribute("aria-label", `${this.label}：${caption}`);
    this.trigger.disabled = disabled || !choices.some((choice) => !choice.disabled);
    if (changed) {
      const scroll = this.menu.scrollTop;
      this.menu.replaceChildren(...choices.map((choice, index) => {
        const option = document.createElement("div");
        option.id = `${this.root.id}-option-${index}`;
        option.className = "dropdown-option";
        option.setAttribute("role", "option");
        option.setAttribute("aria-disabled", String(!!choice.disabled));
        const body = document.createElement("span");
        body.className = "dropdown-option-body";
        const text = document.createElement("span");
        text.textContent = choice.text;
        body.append(text);
        if (choice.description) {
          const description = document.createElement("small");
          description.textContent = choice.description;
          body.append(description);
        }
        const check = document.createElement("span");
        check.className = "dropdown-check";
        check.textContent = "✓";
        check.setAttribute("aria-hidden", "true");
        option.append(body, check);
        option.addEventListener("click", () => this.choose(index));
        return option;
      }));
      this.menu.scrollTop = scroll;
      this.active = choices.findIndex((choice) => choice.value === activeValue && !choice.disabled);
      if (this.active < 0)
        this.active = choices.findIndex((choice) => choice.value === value && !choice.disabled);
      if (this.active < 0) this.active = choices.findIndex((choice) => !choice.disabled);
    }
    this.highlight();
    if (this.trigger.disabled) this.close();
    else if (this.isOpen) this.place();
  }

  open() {
    if (this.trigger.disabled) return;
    WorkbenchDropdown.opened?.close();
    WorkbenchDropdown.opened = this;
    this.search = "";
    this.active = this.choices.findIndex((choice) => choice.value === this.value && !choice.disabled);
    if (this.active < 0) this.active = this.choices.findIndex((choice) => !choice.disabled);
    this.menu.hidden = false;
    this.trigger.setAttribute("aria-expanded", "true");
    this.place();
    this.highlight(true);
  }

  close() {
    if (!this.isOpen) return;
    this.menu.hidden = true;
    this.trigger.setAttribute("aria-expanded", "false");
    this.trigger.removeAttribute("aria-activedescendant");
    WorkbenchDropdown.opened = null;
  }

  choose(index) {
    const choice = this.choices[index];
    if (!choice || choice.disabled || this.trigger.disabled) return;
    this.close();
    this.trigger.focus({ preventScroll: true });
    this.onChange(choice.value);
  }

  highlight(scroll = false) {
    for (const [index, option] of [...this.menu.children].entries()) {
      option.setAttribute("aria-selected", String(this.choices[index].value === this.value));
      option.classList.toggle("active", index === this.active);
    }
    const active = this.menu.children[this.active];
    if (this.isOpen && active) {
      this.trigger.setAttribute("aria-activedescendant", active.id);
      if (scroll) {
        const top = active.offsetTop;
        const bottom = top + active.offsetHeight;
        if (top < this.menu.scrollTop) this.menu.scrollTop = top;
        else if (bottom > this.menu.scrollTop + this.menu.clientHeight)
          this.menu.scrollTop = bottom - this.menu.clientHeight;
      }
    }
  }

  keydown(event) {
    if (event.isComposing || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "Tab") { this.close(); return; }
    if (event.key === "Escape") {
      if (this.isOpen) { event.preventDefault(); event.stopPropagation(); this.close(); }
      return;
    }
    const keys = ["ArrowDown", "ArrowUp", "Home", "End", "Enter", " "];
    if (!keys.includes(event.key) && event.key.length !== 1) return;
    event.preventDefault();
    const wasOpen = this.isOpen;
    if (!wasOpen) this.open();
    if (!this.isOpen) return;
    if (event.key === "Enter" || event.key === " ") {
      if (wasOpen) this.choose(this.active);
      return;
    }
    const enabled = this.choices.map((choice, index) => choice.disabled ? -1 : index)
      .filter((index) => index >= 0);
    const position = enabled.indexOf(this.active);
    if (event.key === "Home") this.active = enabled[0];
    else if (event.key === "End") this.active = enabled.at(-1);
    else if (event.key === "ArrowDown" && wasOpen)
      this.active = enabled[Math.min(position + 1, enabled.length - 1)];
    else if (event.key === "ArrowUp" && wasOpen)
      this.active = enabled[Math.max(position - 1, 0)];
    else if (event.key.length === 1) {
      const now = Date.now();
      this.search = now - this.searchAt < 700 ? this.search + event.key : event.key;
      this.searchAt = now;
      const letters = [...this.search.toLocaleLowerCase()];
      const prefix = letters.every((letter) => letter === letters[0]) ? letters[0] : letters.join("");
      const start = prefix.length === 1 ? position + 1 : Math.max(position, 0);
      const index = enabled.map((_, offset) => enabled[(start + offset) % enabled.length])
        .find((index) => this.choices[index].text.toLocaleLowerCase().startsWith(prefix));
      if (index !== undefined) this.active = index;
    }
    this.highlight(true);
  }

  place() {
    const rect = this.trigger.getBoundingClientRect();
    const width = window.innerWidth;
    const height = window.innerHeight;
    if (!rect.width || !rect.height || rect.bottom < 0 || rect.top > height) {
      this.close();
      return;
    }
    const margin = 10, gap = 6;
    const menuWidth = Math.min(Math.max(rect.width, 280), width - margin * 2);
    this.menu.style.width = `${menuWidth}px`;
    this.menu.style.left = `${Math.max(margin, Math.min(rect.left, width - margin - menuWidth))}px`;
    const below = height - rect.bottom - margin - gap;
    const above = rect.top - margin - gap;
    const upward = below < Math.min(this.menu.scrollHeight, 320) && above > below;
    this.menu.style.maxHeight = `${Math.max(0, Math.min(320, upward ? above : below))}px`;
    this.menu.style.top = `${upward ? rect.top - gap - this.menu.getBoundingClientRect().height : rect.bottom + gap}px`;
  }
}

document.addEventListener("pointerdown", (event) => {
  const dropdown = WorkbenchDropdown.opened;
  if (dropdown && !dropdown.root.contains(event.target) && !dropdown.menu.contains(event.target))
    dropdown.close();
});
document.addEventListener("focusin", (event) => {
  const dropdown = WorkbenchDropdown.opened;
  if (dropdown && !dropdown.root.contains(event.target) && !dropdown.menu.contains(event.target))
    dropdown.close();
});
window.addEventListener("resize", () => WorkbenchDropdown.opened?.place());
document.addEventListener("scroll", (event) => {
  const dropdown = WorkbenchDropdown.opened;
  if (dropdown && !dropdown.menu.contains(event.target)) dropdown.place();
}, true);
