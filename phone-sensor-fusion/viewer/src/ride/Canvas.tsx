// A canvas that fills its box at the screen's pixel density and redraws when its size or
// what it draws changes.
import {useEffect, useLayoutEffect, useRef, useState} from "react";

export type Draw = (ctx: CanvasRenderingContext2D, width: number, height: number) => void;

export default function Canvas({draw, height, className}: {draw: Draw; height: number; className?: string}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const el = ref.current!;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !width) return;
    const dpr = window.devicePixelRatio || 1;
    el.width = Math.round(width * dpr);
    el.height = Math.round(height * dpr);
    const ctx = el.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    draw(ctx, width, height);
  }, [draw, width, height]);

  return <canvas ref={ref} className={className} style={{height, width: "100%", display: "block"}} />;
}
