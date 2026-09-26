/**
 * Measuring helpers for widgets that fill the height their row gives them
 * (`fit: "fill"`, see DashboardGrid): a chart that grows, a list that shows
 * as many whole items as fit.
 */
import { useCallback, useLayoutEffect, useRef, useState } from "react";

/** An element's height, kept current: a callback ref and the height. */
export function useElementHeight<T extends HTMLElement>(): [(el: T | null) => void, number | null] {
  const [height, setHeight] = useState<number | null>(null);
  const observer = useRef<ResizeObserver | null>(null);
  const ref = useCallback((el: T | null) => {
    observer.current?.disconnect();
    observer.current = null;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([entry]) => setHeight(Math.round(entry.contentRect.height)));
    ro.observe(el);
    observer.current = ro;
  }, []);
  return [ref, height];
}

/**
 * How many of a list's children fit its box whole, from the top. The list
 * must be `position: relative` (children's offsets are read against it) and
 * lay out every child it may show; those past the count are to be hidden,
 * not removed, so the measurement holds still.
 *
 * Measured after every render and whenever the list or any child changes
 * size (a child's text can wrap, or a font load, with the list's box as it
 * was). `null` until measured, and where ResizeObserver is missing: show all.
 */
export function useFitCount(list: HTMLElement | null): number | null {
  const [count, setCount] = useState<number | null>(null);
  const observer = useRef<ResizeObserver | null>(null);

  const measure = useCallback(() => {
    if (!list) return;
    const box = list.clientHeight;
    let n = 0;
    for (const child of Array.from(list.children) as HTMLElement[]) {
      if (child.offsetTop + child.offsetHeight > box + 0.5) break;
      n += 1;
    }
    setCount(n);
  }, [list]);

  useLayoutEffect(() => {
    if (!list || typeof ResizeObserver === "undefined") {
      setCount(null);
      return;
    }
    const ro = new ResizeObserver(() => measure());
    observer.current = ro;
    return () => {
      ro.disconnect();
      observer.current = null;
    };
  }, [list, measure]);

  // Every render: the children may be new. Re-observe them, and measure.
  useLayoutEffect(() => {
    const ro = observer.current;
    if (!list || !ro) return;
    ro.disconnect();
    ro.observe(list);
    for (const child of Array.from(list.children)) ro.observe(child);
    measure();
  });

  return count;
}
