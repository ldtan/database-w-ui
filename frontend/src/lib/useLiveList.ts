import { useEffect, useMemo, useState } from "react";

import { LIVE_REFRESH_SECONDS } from "./config";

// Stop polling after this long with no user activity, even while the tab is
// visible. `refetchIntervalInBackground: false` already handles a *hidden* tab,
// but a visible-but-abandoned one (a list left open on screen) would otherwise
// keep refetching — and, on the free tier, keep the single instance awake — for
// no one. Polling resumes on the next interaction. A one-line change makes this
// env-configurable if a deploy ever needs it.
const IDLE_AFTER_MS = 5 * 60 * 1000; // 5 minutes

// Interactions that count as "still here". `visibilitychange` is included so
// returning to the tab resumes immediately rather than waiting out the timer.
const ACTIVITY_EVENTS = [
  "mousemove",
  "keydown",
  "pointerdown",
  "scroll",
  "touchstart",
  "visibilitychange",
] as const;

/**
 * "Live records": the one seam that keeps an open list current as *other* people
 * (or your other tabs) create, update and delete rows. Spread its result into
 * the list's items `useQuery`:
 *
 *     const live = useLiveList(listUuid);
 *     const items = useQuery({ ...live, queryKey: ["items", listUuid, …], … });
 *
 * Today it **polls**: it hands react-query a `refetchInterval` so the query
 * refetches every `LIVE_REFRESH_SECONDS` (configurable per deploy; `0` disables
 * it). Two guardrails keep the idle cost at zero: polling pauses while the tab is
 * hidden (`refetchIntervalInBackground: false`), and it also pauses after
 * `IDLE_AFTER_MS` without user activity on a still-visible tab. Crucially the
 * refetch is a normal, permissioned `GET /items/`, so a poll can never surface a
 * row the viewer isn't allowed to see.
 *
 * The seam exists so this can become server push (SSE) later **without touching a
 * single call site**: swap this hook's body to open a per-list event stream and
 * call `queryClient.invalidateQueries(["items", listUuid])` on each event, then
 * return `{}`. `listUuid` is already taken for exactly that per-list
 * subscription. See planned-features #8.
 */
export function useLiveList(listUuid: string) {
  const polling = LIVE_REFRESH_SECONDS > 0;
  const [idle, setIdle] = useState(false);

  useEffect(() => {
    if (!polling) return; // nothing to gate when polling is disabled

    let timer: ReturnType<typeof setTimeout>;
    const markActive = () => {
      setIdle(false); // React bails out when already false, so mousemove is cheap
      clearTimeout(timer);
      timer = setTimeout(() => setIdle(true), IDLE_AFTER_MS);
    };

    markActive(); // arm the idle timer on mount
    for (const evt of ACTIVITY_EVENTS)
      window.addEventListener(evt, markActive, { passive: true });
    return () => {
      clearTimeout(timer);
      for (const evt of ACTIVITY_EVENTS)
        window.removeEventListener(evt, markActive);
    };
  }, [polling]);

  return useMemo(() => {
    void listUuid; // reserved for the SSE subscription this hook will grow
    if (!polling || idle) return {} as const; // disabled by config, or idle
    return {
      refetchInterval: LIVE_REFRESH_SECONDS * 1000,
      refetchIntervalInBackground: false,
    } as const;
  }, [listUuid, polling, idle]);
}
