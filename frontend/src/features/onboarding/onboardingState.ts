/**
 * What a person has already been shown, remembered on the device.
 *
 * localStorage rather than the account: a tour is a frontend affordance, and an
 * account-level flag would mean a column, a migration and an endpoint for a
 * boolean whose worst failure is offering a tour to somebody who has already
 * taken it. Keyed by username all the same — two accounts sharing a browser each
 * get their own first run rather than inheriting the other's.
 *
 * Every read is defensive. Storage can be unavailable (Safari's private mode
 * throws on write), and the value can be anything at all if an older version of
 * this app — or another tab — wrote it. A first-run banner is not worth a blank
 * page, so anything unreadable is treated as "nothing seen yet".
 */

const KEY = "dbui.onboarding.v1";

export interface OnboardingState {
  /** True once the welcome has been answered, whichever way it was answered. */
  welcomed: boolean;
  /** Ids of the tours that have been finished or skipped. */
  seen: string[];
}

const NOTHING_SEEN: OnboardingState = { welcomed: false, seen: [] };

type Stored = Record<string, Partial<OnboardingState> | undefined>;

function readAll(): Stored {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? (parsed as Stored) : {};
  } catch {
    return {};
  }
}

export function readOnboarding(username: string): OnboardingState {
  const mine = readAll()[username];
  return {
    welcomed: mine?.welcomed === true,
    seen: Array.isArray(mine?.seen) ? mine.seen.filter((s) => typeof s === "string") : [],
  };
}

export function writeOnboarding(username: string, next: OnboardingState): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...readAll(), [username]: next }));
  } catch {
    // A device that will not remember this is a device that gets asked again.
    // That is a nuisance, not a failure, and nothing here is worth throwing over.
  }
}

export { NOTHING_SEEN };
