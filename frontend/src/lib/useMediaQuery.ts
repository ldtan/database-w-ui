import { useCallback, useMemo, useSyncExternalStore } from "react";

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
  // One MediaQueryList per query, not one per call: React asks for the snapshot on
  // every render and after every change, and `window.matchMedia(query)` would
  // build a fresh object each time for the sake of reading one boolean off it.
  const mql = useMemo(() => window.matchMedia(query), [query]);
  const subscribe = useCallback(
    (onChange: () => void) => {
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    },
    [mql],
  );
  return useSyncExternalStore(
    subscribe,
    () => mql.matches,
    () => false, // server/prerender: assume the desktop layout
  );
}
