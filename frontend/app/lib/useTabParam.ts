/**
 * A page's tab, kept in the address (`?tab=`) so a link or a reload opens the
 * same one. Anything not among `tabs` is the first tab.
 */
import { useSearchParams } from "react-router";

export function useTabParam<T extends string>(tabs: readonly T[]): [T, (tab: T) => void] {
  const [params, setParams] = useSearchParams();
  const tab = tabs.find((x) => x === params.get("tab")) ?? tabs[0];
  const setTab = (next: T) =>
    setParams(
      (prev) => {
        const out = new URLSearchParams(prev);
        out.set("tab", next);
        return out;
      },
      { replace: true },
    );
  return [tab, setTab];
}
