/**
 * Which bottom corner the phone's navigation button sits in: the side of the
 * hand the user holds the phone with. Kept per browser -- it is about the
 * device in hand, and it must be right on the first paint, before any request
 * could come back.
 */
import { useCallback, useEffect, useState } from "react";

export type NavHand = "left" | "right";

export const NAV_HAND_KEY = "llm-port-nav-hand";

function read(): NavHand {
  try {
    return window.localStorage.getItem(NAV_HAND_KEY) === "left" ? "left" : "right";
  } catch {
    return "right";
  }
}

export function useNavHand(): [NavHand, (hand: NavHand) => void] {
  const [hand, setHandState] = useState<NavHand>(read);

  useEffect(() => {
    // Another tab switched hands.
    const onStorage = (e: StorageEvent) => {
      if (e.key === NAV_HAND_KEY) setHandState(read());
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const setHand = useCallback((next: NavHand) => {
    setHandState(next);
    try {
      window.localStorage.setItem(NAV_HAND_KEY, next);
    } catch {
      // Storage blocked (private window): the choice lasts for this page.
    }
  }, []);

  return [hand, setHand];
}
