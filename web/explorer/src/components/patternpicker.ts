// A two-level checkbox input: groups (e.g. routes), each with its items (e.g. patterns).
// Use with view(); its value is the array of checked item keys. A group's checkbox turns
// all its items on or off, and shows as indeterminate when only some are on.

export interface PickerItem { key: string; label: string }
export interface PickerGroup { label: string; color?: string; items: PickerItem[] }

export function groupedCheckbox(groups: PickerGroup[], {label = "", vertical = false}: {label?: string; vertical?: boolean} = {}) {
  const root = document.createElement("form");
  root.style.cssText = vertical
    ? "display: flex; flex-direction: column; gap: 0.6rem; font: 12px var(--sans-serif); margin: 0;"
    : "display: flex; flex-wrap: wrap; gap: 0.5rem 2rem; font: 13px var(--sans-serif); margin: 0.5rem 0 1rem;";
  if (label) {
    const l = document.createElement("div");
    l.textContent = label;
    l.style.cssText = "flex-basis: 100%; font-weight: 600;";
    root.append(l);
  }
  const boxes: {group: HTMLInputElement; items: {key: string; box: HTMLInputElement}[]}[] = [];
  const sync = () => {
    for (const {group, items} of boxes) {
      const n = items.filter((i) => i.box.checked).length;
      group.checked = n === items.length;
      group.indeterminate = n > 0 && n < items.length;
    }
  };
  const update = () => {
    sync();
    (root as any).value = boxes.flatMap(({items}) => items.filter((i) => i.box.checked).map((i) => i.key));
    root.dispatchEvent(new Event("input", {bubbles: true}));
  };
  for (const g of groups) {
    const col = document.createElement("div");
    const head = document.createElement("label");
    head.style.cssText = "display: flex; gap: 0.35rem; align-items: center; font-weight: 600;";
    const gbox = document.createElement("input");
    gbox.type = "checkbox";
    gbox.checked = true;
    const gname = document.createElement("span");
    gname.textContent = g.label;
    if (g.color) gname.style.borderBottom = `3px solid ${g.color}`;
    head.append(gbox, gname);
    col.append(head);
    const items = g.items.map((it) => {
      const row = document.createElement("label");
      row.style.cssText = "display: flex; gap: 0.35rem; align-items: center; margin-left: 1.3rem;";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = true;
      box.addEventListener("change", update);
      const name = document.createElement("span");
      name.textContent = it.label;
      row.append(box, name);
      col.append(row);
      return {key: it.key, box};
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
  (root as any).value = boxes.flatMap(({items}) => items.map((i) => i.key));
  return root;
}
