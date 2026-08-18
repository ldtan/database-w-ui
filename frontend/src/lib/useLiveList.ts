import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient, type QueryKey } from "@tanstack/react-query";

import { LIVE_REFRESH_SECONDS } from "./config";
import { subscribeToList, type LiveStatus } from "./liveStream";

export interface LiveListOptions {
  /** The query (or query prefix) an event should invalidate. */
  queryKey: QueryKey;
  /**
   * The least often this caller's query may be refreshed, in seconds.
   *
   * A floor, never a value, and it can only ever slow a query down: the metric strip
   * scans every record the caller can see, so it asks for `METRIC_REFRESH_SECONDS`
   * and gets at most one refresh a minute however busy the list is. Under polling
   * this raised the interval; under push it throttles the invalidation, which is the
   * same promise about cost kept by a different mechanism. It also stays the
   * fallback interval when polling takes over.
   */
  minSeconds?: number;
}

/**
 * "Live records": the one seam that keeps an open list current as *other* people
 * (or your other tabs) create, update and delete rows. Spread its result into the
 * list's items `useQuery`:
 *
 *     const live = useLiveList(listUuid, { queryKey: recordPagesKey(listUuid) });
 *     const items = useQuery({ ...live, queryKey: [...], … });
 *
 * **It pushes, and falls back to polling.** The hook subscribes to
 * `GET /lists/{uuid}/events` (see `liveStream.ts`) and invalidates `queryKey` when
 * the server says that list changed — so a row someone else adds shows up in well
 * under a second and an idle tab costs nothing at all. While that stream is open it
 * returns `{}`, and react-query is left alone. If the stream will not stay open —
 * a proxy that buffers event streams, a deployment with `SSE_ENABLED=False`, a
 * network that forbids them — it returns the `refetchInterval` this hook used to
 * return unconditionally, and the list refreshes every `LIVE_REFRESH_SECONDS`
 * exactly as before. Nothing about the fallback is new code; it is the old
 * behaviour, kept, for the case where push cannot be had.
 *
 * **A live event never carries record data.** It says "this list changed" and
 * nothing else, and the refetch it provokes is an ordinary permissioned
 * `GET /items/` — so push can no more surface a row the viewer may not see than
 * polling could. That is the constraint the whole design is built around; see
 * docs/known-gaps.md.
 *
 * **Why this takes a `queryKey` when the polling version did not.** Under polling,
 * each caller's interval acted on its own query and nothing had to be named. An
 * invalidation has to be aimed. Aiming it at the whole `["items", listUuid]`
 * namespace would be simpler and is wrong: the metric values live under that prefix
 * too (see `metricKeys`), and they are a scan of every record the caller can see —
 * refreshing them on every write, per viewer, costs more than the polling this
 * replaces. So each caller names what it wants refreshed and keeps its own floor.
 */
export function useLiveList(listUuid: string, options: LiveListOptions) {
  const { queryKey, minSeconds = 0 } = options;
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<LiveStatus>("paused");

  // Both change identity on most renders — `queryKey` is built inline at the call
  // site — and neither should re-subscribe the stream. Read through refs so the
  // effect below depends on the list alone.
  const keyRef = useRef(queryKey);
  keyRef.current = queryKey;
  const floorRef = useRef(minSeconds);
  floorRef.current = minSeconds;

  useEffect(() => {
    // Leading-edge throttle: the first event after a quiet period refreshes at
    // once, and anything inside the window is dropped rather than queued. Dropping
    // is right precisely because the events carry nothing — a refresh that has
    // already happened covers every change before it, so a trailing call would
    // re-ask a question just answered. With `minSeconds` at 0 (the grid) every
    // event gets through, and the coalescing in `liveStream` is what stops a bulk
    // write from becoming a burst of refetches.
    let lastAt = 0;
    const onChange = () => {
      const now = Date.now();
      if (now - lastAt < floorRef.current * 1000) return;
      lastAt = now;
      void queryClient.invalidateQueries({ queryKey: keyRef.current });
    };
    return subscribeToList(listUuid, { onChange, onStatus: setStatus });
  }, [listUuid, queryClient]);

  return useMemo(() => {
    // `0` means "no polling" rather than "as fast as possible", so the floor is
    // applied only once there is an interval to raise.
    const seconds =
      LIVE_REFRESH_SECONDS > 0 ? Math.max(LIVE_REFRESH_SECONDS, minSeconds) : 0;
    // "live" — the stream has it covered. "paused" — the tab is hidden or the user
    // has been away five minutes, so nothing should refresh; that guard used to
    // live in this hook and now lives in `liveStream`, where the stream and the
    // timer can be governed by one answer instead of two.
    if (status !== "degraded" || seconds === 0) return {} as const;
    return {
      refetchInterval: seconds * 1000,
      refetchIntervalInBackground: false,
    } as const;
  }, [status, minSeconds]);
}
