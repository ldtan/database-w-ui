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
export const recordPagesKey = (listUuid: string) =>
  ["items", listUuid, "page"] as const;
