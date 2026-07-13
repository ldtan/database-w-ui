import { useCallback, useSyncExternalStore } from "react";

/** The width below which a table stops being readable and the UI switches to
 *  cards. Matches the 640px the CSS and Tailwind's `sm:` already use — one number,
 *  so a layout cannot half-change. */
export const SMALL_SCREEN = "(max-width: 640px)";

/**
 * Subscribe to a media query, re-rendering when it starts or stops matching.
 *
 * `useSyncExternalStore` rather than an effect + state: matchMedia is an external
 * store, and this way the first render already has the right answer instead of
 * painting the desktop layout and then correcting itself.
 */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const mql = window.matchMedia(query);
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    },
    [query],
  );
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => false, // server/prerender: assume the desktop layout
  );
}
