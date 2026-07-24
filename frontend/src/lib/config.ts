// Build-time settings read from Vite env vars, kept in one place so a deploy can
// tune behaviour without editing feature code. A missing or malformed value falls
// back to the default rather than breaking the build.

/**
 * How often an open list refetches to pick up record changes made by *other*
 * people (or your other tabs) — the "live records" polling interval, in seconds.
 * See `useLiveList`. Set `VITE_LIVE_REFRESH_SECONDS` at build time to tune it;
 * `0` disables polling, so records update only on your own edits (the behaviour
 * before this feature existed).
 */
export const LIVE_REFRESH_SECONDS = readNonNegativeNumber(
  import.meta.env.VITE_LIVE_REFRESH_SECONDS,
  10,
);

function readNonNegativeNumber(
  raw: string | undefined,
  fallback: number,
): number {
  if (raw == null || raw === "") return fallback;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 ? n : fallback;
}
