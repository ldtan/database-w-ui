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
 * The *floor* on how often the metric strip re-asks for its values, in seconds.
 *
 * A metric is a scan of every record the caller can see, recomputed per request
 * and cached nowhere, while a page of records is one indexed query — so the two
 * do not deserve the same interval even though they answer the same question
 * about the same list. The rows underneath a total are what has to be live; a
 * total a minute old is a total.
 *
 * A floor rather than a value, applied by `useLiveList`: the effective interval
 * is `max(LIVE_REFRESH_SECONDS, this)`, and `LIVE_REFRESH_SECONDS = 0` still
 * disables polling outright. That ordering is the point — a deploy that slows
 * the records down must not leave the cards above them refreshing faster, which
 * an independent setting would allow by omission.
 */
export const METRIC_REFRESH_SECONDS = readNonNegativeNumber(
  import.meta.env.VITE_METRIC_REFRESH_SECONDS,
  60,
);

function readNonNegativeNumber(
  raw: string | undefined,
  fallback: number,
): number {
  if (raw == null || raw === "") return fallback;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 ? n : fallback;
}
