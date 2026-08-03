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

/**
 * The two limits on an import, mirroring `IMPORT_MAX_ROWS` and `IMPORT_MAX_BYTES`
 * in `backend/src/core/config.py`, which are what actually enforce them.
 *
 * Copied rather than fetched, like `canConvert` in `columnTypes.ts`, and with the
 * same obligation: **keep them in step**. Being wrong here is not dangerous — the
 * server still refuses — but a stated limit that does not match the enforced one
 * is worse than no stated limit, so a deployment that overrides either setting has
 * to rebuild the frontend with it too.
 *
 * They are used for what a copy can be trusted with: telling somebody the rules
 * before they pick a file, and turning an oversize one away without spending their
 * upload on it. The row count is not checked here — quoted newlines mean the only
 * honest way to count rows in a CSV is to parse it, which is the server's job.
 */
export const IMPORT_MAX_ROWS = 2000;
export const IMPORT_MAX_BYTES = 10 * 1024 * 1024;

function readNonNegativeNumber(
  raw: string | undefined,
  fallback: number,
): number {
  if (raw == null || raw === "") return fallback;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 ? n : fallback;
}
