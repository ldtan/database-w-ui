// Build-time settings read from Vite env vars, kept in one place so a deploy can
// tune behaviour without editing feature code. A missing or malformed value falls
// back to the default rather than breaking the build.

/**
 * The "live records" **fallback** polling interval, in seconds.
 *
 * An open list normally holds an SSE stream and refetches when the server says its
 * records moved (`useLiveList`, `liveStream.ts`), so on a healthy deployment this
 * interval is not used at all. It is what the app falls back to when the stream
 * cannot be held open — a proxy that buffers `text/event-stream`, a backend with
 * `SSE_ENABLED=False`, a network that forbids them.
 *
 * Set `VITE_LIVE_REFRESH_SECONDS` at build time to tune it. `0` disables the
 * fallback outright, which leaves a deployment whose stream is blocked refreshing
 * only on your own edits — so leave it set.
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
 * A floor rather than a value, applied by `useLiveList`, and it does the same job
 * on both transports: under the polling fallback the effective interval is
 * `max(LIVE_REFRESH_SECONDS, this)`, and under push it **throttles the
 * invalidation** — the grid refreshes on the event, the strip at most this often
 * however many events arrive. Keeping it through the swap was not optional: a busy
 * list can signal far faster than the timer ever ticked, so without the floor every
 * write would put a full scan in front of every viewer.
 * `LIVE_REFRESH_SECONDS = 0` still disables the fallback outright. The ordering is
 * the point — a deploy that slows the records down must not leave the cards above
 * them refreshing faster, which an independent setting would allow by omission.
 */
export const METRIC_REFRESH_SECONDS = readNonNegativeNumber(
  import.meta.env.VITE_METRIC_REFRESH_SECONDS,
  60,
);

/**
 * The *floor* on how often the paginator's total is re-asked, in seconds.
 *
 * The count sits between a page of records and a metric in what it costs, and it
 * used to poll on the same clock as the cheapest of the three. A page is one
 * indexed query stopped by `LIMIT`; the count has no limit to stop it, so it reads
 * and tests every matching record. Measured on a 100k list: 4 ms for the page,
 * 12 ms for the count unfiltered — and **268 ms for the count under a search**,
 * because a search that matches little still has to prove it by looking at
 * everything. A tab left open on a searched list was spending that every ten
 * seconds with nobody reading it.
 *
 * Shorter than {@link METRIC_REFRESH_SECONDS} on purpose: a total is a number on
 * screen and beside a paginator, where being visibly behind the rows it describes
 * is worse than it is for a metric card. Same floor semantics as the metric one —
 * applied by `useLiveList` on both transports, so `LIVE_REFRESH_SECONDS = 0` still
 * disables the fallback and a slower records clock still wins.
 *
 * Your own edits do not wait for it: the count key is invalidated alongside the
 * items key, so this interval only ever carries *other people's* changes.
 */
export const COUNT_REFRESH_SECONDS = readNonNegativeNumber(
  import.meta.env.VITE_COUNT_REFRESH_SECONDS,
  30,
);

/**
 * How many live records a list may hold before the app suggests archiving, or `0`
 * to never suggest it.
 *
 * 25,000 because that is roughly where the operations whose cost *scales with the
 * live row count* start to be felt on the free tier's 0.1 CPU — search first
 * (~7.7 µs per row per search on a twenty-column list, so ~1 s of wall time), then
 * metrics that fall back to the Python scan at ~20 µs a record. The default paged
 * listing is not among them: it is a few milliseconds whatever the list size,
 * which is why this is a suggestion about a specific kind of slowness rather than
 * a general "your list is big" warning. See docs/capacity-and-performance.md for
 * the measurements and the caveats — chiefly that column count drives the search
 * cost as much as row count does.
 */
export const ARCHIVE_HINT_THRESHOLD = readNonNegativeNumber(
  import.meta.env.VITE_ARCHIVE_HINT_THRESHOLD,
  25000,
);

function readNonNegativeNumber(
  raw: string | undefined,
  fallback: number,
): number {
  if (raw == null || raw === "") return fallback;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 ? n : fallback;
}
