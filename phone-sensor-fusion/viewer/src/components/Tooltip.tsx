// One tooltip for the whole app: any element with a data-tip attribute shows it on hover
// (after a short delay) or keyboard focus, placed below the element, or above it near the
// bottom of the window. Used instead of the browser's own title tooltips.
import {useEffect, useLayoutEffect, useRef, useState} from "react";

interface Tip { text: string; x: number; y: number; below: boolean }

const DELAY_MS = 250;

export default function Tooltips() {
  const [tip, setTip] = useState<Tip | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const current = useRef<Element | null>(null);

  useEffect(() => {
    const show = (el: Element) => {
      const text = el.getAttribute("data-tip");
      if (!text) return;
      const r = el.getBoundingClientRect();
      const below = r.bottom + 120 < window.innerHeight;
      setTip({text, x: r.left + r.width / 2, y: below ? r.bottom + 6 : r.top - 6, below});
    };
    const over = (e: Event) => {
      const el = (e.target as Element | null)?.closest?.("[data-tip]") ?? null;
      if (el === current.current) return;
      current.current = el;
      window.clearTimeout(timer.current);
      if (!el) return setTip(null);
      if (e.type === "focusin") show(el);
      else timer.current = window.setTimeout(() => show(el), tip ? 0 : DELAY_MS);
    };
    const hide = () => { current.current = null; window.clearTimeout(timer.current); setTip(null); };
    document.addEventListener("mouseover", over);
    document.addEventListener("focusin", over);
    document.addEventListener("scroll", hide, true);
    document.addEventListener("mousedown", hide);
    document.addEventListener("keydown", hide);
    return () => {
      document.removeEventListener("mouseover", over);
      document.removeEventListener("focusin", over);
      document.removeEventListener("scroll", hide, true);
      document.removeEventListener("mousedown", hide);
      document.removeEventListener("keydown", hide);
    };
  }, [tip]);

  // centred on the element, but kept inside the window
  const box = useRef<HTMLDivElement>(null);
  const [left, setLeft] = useState<number | null>(null);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el || !tip) return setLeft(null);
    const w = el.offsetWidth;
    setLeft(Math.min(Math.max(tip.x - w / 2, 8), window.innerWidth - 8 - w));
  }, [tip]);

  if (!tip) return null;
  return (
    <div ref={box} role="tooltip" className={`tooltip ${tip.below ? "below" : "above"}`}
         style={{left: left ?? 0, top: tip.y, visibility: left == null ? "hidden" : "visible"}}>
      {tip.text}
    </div>
  );
}
