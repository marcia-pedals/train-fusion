// A two-level checkbox input: groups (e.g. routes), each with its items (e.g. patterns).
// Use with view() or Generators.input(); its value is the array of checked item keys.
// - A group's checkbox turns all its items on or off, and shows as indeterminate when only
//   some are on.
// - With `onSelect`, each item's name is also a separate click target that selects the item
//   (the checkbox only shows / hides it). The element then has setSelected(key | null), to
//   highlight the selected row, and show(key), to check an item that's off.

export interface PickerItem { key: string; label: string }
export interface PickerGroup { label: string; color?: string; items: PickerItem[] }
export interface PickerOptions { label?: string; hint?: string; vertical?: boolean; onSelect?: (key: string) => void }

const STYLE = `
.gc-name { cursor: pointer; border-radius: 3px; padding: 0 3px; }
.gc-name:hover, .gc-name:focus-visible { text-decoration: underline; outline: none; }
.gc-row.gc-selected .gc-name { background: var(--theme-foreground-faintest); font-weight: 600; }
`;

export function groupedCheckbox(groups: PickerGroup[], {label = "", hint = "", vertical = false, onSelect}: PickerOptions = {}) {
  const root = document.createElement("form") as HTMLFormElement & {
    value: string[]; setSelected: (key: string | null) => void; show: (key: string) => void;
  };
  root.style.cssText = vertical
    ? "display: flex; flex-direction: column; gap: 0.6rem; font: 12px var(--sans-serif); margin: 0;"
    : "display: flex; flex-wrap: wrap; gap: 0.5rem 2rem; font: 13px var(--sans-serif); margin: 0.5rem 0 1rem;";
  const style = document.createElement("style");
  style.textContent = STYLE;
  root.append(style);
  if (label || hint) {
    const head = document.createElement("div");
    head.style.cssText = "flex-basis: 100%;";
    if (label) {
      const l = document.createElement("div");
      l.textContent = label;
      l.style.fontWeight = "600";
      head.append(l);
    }
    if (hint) {
      const h = document.createElement("div");
      h.textContent = hint;
      h.style.cssText = "color: var(--theme-foreground-muted);";
      head.append(h);
    }
    root.append(head);
  }

  const boxes: {group: HTMLInputElement; items: {key: string; box: HTMLInputElement; row: HTMLElement}[]}[] = [];
  const sync = () => {
    for (const {group, items} of boxes) {
      const n = items.filter((i) => i.box.checked).length;
      group.checked = n === items.length;
      group.indeterminate = n > 0 && n < items.length;
    }
  };
  const update = () => {
    sync();
    root.value = boxes.flatMap(({items}) => items.filter((i) => i.box.checked).map((i) => i.key));
    root.dispatchEvent(new Event("input", {bubbles: true}));
  };

  for (const g of groups) {
    const col = document.createElement("div");
    const head = document.createElement("div");
    head.style.cssText = "display: flex; gap: 0.35rem; align-items: center; font-weight: 600;";
    const gbox = document.createElement("input");
    gbox.type = "checkbox";
    gbox.checked = true;
    gbox.setAttribute("aria-label", `show all of ${g.label}`);
    const gname = document.createElement("span");
    gname.textContent = g.label;
    if (g.color) gname.style.borderBottom = `3px solid ${g.color}`;
    head.append(gbox, gname);
    col.append(head);
    const items = g.items.map((it) => {
      const row = document.createElement("div");
      row.className = "gc-row";
      row.style.cssText = "display: flex; gap: 0.35rem; align-items: center; margin-left: 1.3rem;";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = true;
      box.setAttribute("aria-label", `show ${it.label}`);
      box.addEventListener("change", update);
      const name = document.createElement("span");
      name.textContent = it.label;
      if (onSelect) {
        name.className = "gc-name";
        name.tabIndex = 0;
        name.setAttribute("role", "button");
        name.addEventListener("click", () => onSelect(it.key));
        name.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(it.key); }
        });
      }
      row.append(box, name);
      col.append(row);
      return {key: it.key, box, row};
    });
    gbox.addEventListener("change", () => {
      for (const i of items) i.box.checked = gbox.checked;
      update();
    });
    boxes.push({group: gbox, items});
    root.append(col);
  }

  root.addEventListener("submit", (e) => e.preventDefault());
  // the checkboxes' own input events would reach view() before the value is updated
  root.addEventListener("input", (e) => { if (e.target !== root) e.stopPropagation(); }, true);
  root.value = boxes.flatMap(({items}) => items.map((i) => i.key));
  root.setSelected = (key) => {
    for (const {items} of boxes) for (const i of items) i.row.classList.toggle("gc-selected", i.key === key);
  };
  root.show = (key) => {
    const item = boxes.flatMap(({items}) => items).find((i) => i.key === key);
    if (item && !item.box.checked) { item.box.checked = true; update(); }
  };
  return root;
}
