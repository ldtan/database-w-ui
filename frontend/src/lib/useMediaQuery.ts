import { useCallback, useMemo, useSyncExternalStore } from "react";

/** The width below which a table stops being readable and the UI switches to
 *  cards. Matches the 640px the CSS and Tailwind's `sm:` already use — one number,
 *  so a layout cannot half-change. */
export const SMALL_SCREEN = "(max-width: 640px)";

/** The width at which the navigation stops being a drawer you open and becomes a
 *  rail that is simply there. Deliberately *not* SMALL_SCREEN: a tablet is wide
 *  enough for a table but not for a table plus a 16rem sidebar, so the two
 *  questions — "does a grid fit?" and "does a sidebar fit?" — get two numbers. */
export const DESKTOP_SCREEN = "(min-width: 1024px)";

/** The width above which a row keeps its actions as separate buttons instead of
 *  collapsing them into a single "⋮". A third number rather than a reuse of
 *  DESKTOP_SCREEN: the records grid is at its *narrowest* just above 1024px,
 *  where the sidebar appears and takes 17.25rem off the page without the window
 *  having grown by as much to pay for it. Pinning the actions column spends its
 *  width on every screen, so that is exactly where it has to be cheap. */
export const WIDE_SCREEN = "(min-width: 1280px)";

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
