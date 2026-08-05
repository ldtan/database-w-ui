/** Query keys for a list's records, and the namespace they share.
 *
 * `["items", listUuid]` is a *namespace* rather than a key. The grid's pages sit
 * under it and so do the metric cards' values (see `metricKeys`), because a
 * record changing goes stale for both at the same moment — so the one
 * `invalidateQueries({ queryKey: ["items", listUuid] })` spelled at a dozen call
 * sites refreshes everything that reads the records, and a call site that adds a
 * record gets it right without knowing what else is listening.
 *
 * What that costs is that the prefix is safe to *invalidate* and unsafe to
 * *write*. `setQueriesData` hands its updater every query the filter matches, so
 * an updater shaped like a page of records is also handed the metric values,
 * whose data has no `items` in it at all — and a throwing updater takes its
 * mutation down with it, since `onMutate` runs before `mutationFn`. So every
 * query under the namespace names itself in the segment after the list —
 * `"page"` here, `"metrics"` there — and anything writing into the cache targets
 * that longer prefix instead of the namespace.
 */
import type { FilterGroup } from "../../lib/types";

export const recordPagesKey = (listUuid: string) =>
  ["items", listUuid, "page"] as const;

/** How many records match — the paginator's total, keyed on the *scope* only.
 *
 * Deliberately not keyed on the page, the order or the grouping, because none of
 * them changes the answer. That is the whole point of it being a separate query:
 * the total rode on the page request, so turning a page re-asked a question whose
 * answer could not have moved, and the server counted every matching record again
 * to return the same number. Sharing a key across the pages of one query is what
 * makes the cache do that arithmetic once.
 *
 * The scope it *is* keyed on — view, search, filter — matches `metricValuesKey`
 * exactly, and for the same reason: both ask "which records", and both go stale at
 * the moment the records do. Living under `["items", listUuid]` is what gets them
 * refreshed by the invalidations already spelled at every call site that writes a
 * record; `"count"` is what keeps this query distinguishable from those, per the
 * rule above.
 */
export const recordCountKey = (
  listUuid: string,
  viewUuid: string | null,
  q: string,
  filters: FilterGroup,
) => ["items", listUuid, "count", viewUuid, q, filters] as const;
