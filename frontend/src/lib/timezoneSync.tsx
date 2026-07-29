import { useEffect, useRef } from "react";

import { api } from "./api";
import { useAuth, useCurrentUser } from "./auth";
import { queryClient } from "./queryClient";
import type { ApiUser } from "./types";

/**
 * Reports this browser's timezone to the account, so the server can resolve a
 * `today`/`now` column default on the user's calendar rather than its own.
 *
 * One-way, unlike its neighbour ThemeSync, and deliberately not folded into it: a
 * zone is not a preference someone expresses on a device, it is a fact about where
 * they are, and the browser is the only party that knows it. There is no "the
 * account wins on sign-in" half to write — the account's copy is a cache of this
 * value, never a rival to it.
 *
 * Pushed whenever it differs from what the account holds, not only when that is
 * empty. Re-reporting costs one PATCH on the loads that follow a move, and makes
 * the stored zone self-healing rather than frozen at whatever it was the first time
 * the user signed in. Two devices in different zones will each correct the other,
 * which is the right answer for "where is this person now": the one they are
 * actually using last.
 *
 * Nothing on screen depends on this, so it fails silently. The cost of a lost write
 * is that a `today` default lands in the previous behaviour — UTC — until the next
 * load retries.
 */
export function TimezoneSync() {
  const { isAuthenticated } = useAuth();
  const me = useCurrentUser();

  const uuid = me?.uuid;
  const stored = me?.settings?.timezone;

  // What has been sent for whom, so that the cache update our own PATCH triggers
  // does not read as a fresh difference and queue a second identical write. Keyed
  // by account as well as zone: this component outlives any one session.
  const pushed = useRef<string | null>(null);

  useEffect(() => {
    if (!isAuthenticated || !uuid) {
      pushed.current = null;
      return;
    }

    // Absent only on a browser too old to report one, which leaves the account on
    // whatever it already had — there is nothing better to offer it.
    const local = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (!local || local === stored) return;

    const attempt = `${uuid}:${local}`;
    if (pushed.current === attempt) return;
    pushed.current = attempt;

    api
      .patch<ApiUser>("/auth/users/me/settings", { timezone: local })
      .then((user) => queryClient.setQueryData(["me"], user))
      .catch(() => {
        // Forget the attempt so a later render can retry. A zone the server's tz
        // database does not recognise is a 422 and will keep failing; that costs
        // one refused request per load and leaves the UTC fallback in place.
        pushed.current = null;
      });
  }, [isAuthenticated, uuid, stored]);

  return null;
}
