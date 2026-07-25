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
 */
export function ThemeSync() {
  const { isAuthenticated } = useAuth();
  const me = useCurrentUser();
  const { theme, mode, revision, applyRemote } = useTheme();

  const settings = me?.settings;

  // What the server is known to hold, and null before we have heard from it. Both
  // directions read it: an inbound apply records the value first, so the re-render
  // it causes finds nothing to push and the two directions do not chase each other.
  const serverValue = useRef<UserSettings | null>(null);
  // The local revision as of sign-in, to tell "the user has already picked
  // something in this session" from "these are just the values the page loaded with".
  const revisionAtSignIn = useRef(revision);

  useEffect(() => {
    if (!isAuthenticated) {
      // Signed out: forget the account's values so the next sign-in adopts fresh
      // ones, and re-baseline the revision so toggles made on the login screen do
      // not read as a pending change belonging to whoever logs in next.
      serverValue.current = null;
      revisionAtSignIn.current = revision;
      return;
    }
    if (!settings) return;

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
    const next = { theme, mode };
    // Recorded before the request, not after: the PATCH resolving updates the cached
    // user, which re-runs this effect, and without this it would queue a second
    // identical write.
    serverValue.current = next;
    api
      .patch<ApiUser>("/auth/users/me/settings", next)
      .then((user) => queryClient.setQueryData(["me"], user))
      .catch(() => {
        // Leave the device on the user's choice — they can see it, and it is in
        // localStorage — but forget that the server has it, so the next change
        // retries rather than assuming the save landed.
        serverValue.current = previous;
      });
  }, [isAuthenticated, settings, theme, mode, revision, applyRemote]);

  return null;
}
