import { useEffect, useRef } from "react";

import { api } from "./api";
import { useAuth, useCurrentUser } from "./auth";
import { queryClient } from "./queryClient";
import { useTheme } from "./theme";
import type { ApiUser, UserSettings } from "./types";

/**
 * Ties this device's theme to the account's, so preferences follow the user.
 *
 * Renders nothing. It lives here rather than inside `ThemeProvider` because that
 * provider is mounted outside `AuthProvider` (see `main.tsx` — it has to wrap
 * everything that paints) and so cannot ask who is signed in. This component sits
 * on the inside of that boundary and bridges the two.
 *
 * The rules, in order of how often they matter:
 *
 * - **The account wins on sign-in.** Whatever the server holds is applied over this
 *   device's cached choice, once per session. That is what makes a preference set
 *   on a laptop show up on a phone.
 * - **The device wins afterwards.** Every later change here is pushed up, and
 *   becomes what the next device to sign in receives.
 * - **A change made on another device lands on the next load, not live.** Consistent
 *   with the rest of the app, which turns off refetch-on-focus and refreshes on
 *   explicit invalidation rather than polling.
 * - **Nothing crosses an account boundary.** Everything below is keyed to whose
 *   settings we are holding, because this component outlives any one session.
 */
export function ThemeSync() {
  const { isAuthenticated } = useAuth();
  const me = useCurrentUser();
  const { theme, mode, revision, applyRemote } = useTheme();

  const settings = me?.settings;
  const uuid = me?.uuid;

  // What the server is known to hold, and null before we have heard from it. Both
  // directions read it: an inbound apply records the value first, so the re-render
  // it causes finds nothing to push and the two directions do not chase each other.
  const serverValue = useRef<UserSettings | null>(null);
  // Whose values those are. This component never unmounts, so without it a second
  // account signing in on the same tab would be synced against the first one's.
  const serverUuid = useRef<string | null>(null);
  // The local revision as of sign-in, to tell "the user has already picked
  // something in this session" from "these are just the values the page loaded with".
  const revisionAtSignIn = useRef(revision);

  useEffect(() => {
    if (!isAuthenticated) {
      // Signed out: forget the account's values so the next sign-in adopts fresh
      // ones, and re-baseline the revision so toggles made on the login screen do
      // not read as a pending change belonging to whoever logs in next.
      serverValue.current = null;
      serverUuid.current = null;
      revisionAtSignIn.current = revision;
      return;
    }
    if (!settings || !uuid) return;

    if (serverUuid.current !== null && serverUuid.current !== uuid) {
      // A different account, without the sign-out above having run — a session that
      // expired mid-use, say. Everything held here belongs to the previous user: the
      // values, and a revision counting their changes. Start over, or the incoming
      // account both inherits their theme and has its own overwritten with it.
      serverValue.current = null;
      revisionAtSignIn.current = revision;
    }
    serverUuid.current = uuid;

    if (serverValue.current === null) {
      serverValue.current = settings;
      // Adopt the account's copy — unless the user has already touched the controls
      // since signing in. /me takes a moment to arrive, and a click that lands in
      // that window is newer than what the server sent, so it gets pushed below
      // instead of being undone under their hands.
      if (revision === revisionAtSignIn.current) {
        applyRemote(settings.theme, settings.mode);
        return;
      }
    }

    if (serverValue.current.theme === theme && serverValue.current.mode === mode) {
      return;
    }

    const previous = serverValue.current;
    // Only what actually differs. Sending both keys every time would defeat the
    // merge the endpoint is built around: a tab that toggled dark would also push
    // whatever theme its own state happens to hold, reverting a palette chosen in
    // another tab, or one this device has not heard about yet.
    const patch: Partial<UserSettings> = {};
    if (previous.theme !== theme) patch.theme = theme;
    if (previous.mode !== mode) patch.mode = mode;
    // Recorded before the request, not after: the PATCH resolving updates the cached
    // user, which re-runs this effect, and without this it would queue a second
    // identical write.
    serverValue.current = { theme, mode };
    api
      .patch<ApiUser>("/auth/users/me/settings", patch)
      .then((user) => queryClient.setQueryData(["me"], user))
      .catch(() => {
        // Leave the device on the user's choice — they can see it, and it is in
        // localStorage — but forget that the server has it, so the next change
        // retries rather than assuming the save landed.
        serverValue.current = previous;
      });
  }, [isAuthenticated, uuid, settings, theme, mode, revision, applyRemote]);

  return null;
}
