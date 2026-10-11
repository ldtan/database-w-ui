import type { FilterGroup } from "../../lib/types";

/** Which records a request is about: the view, the search and the filter. */
export interface RecordScope {
  viewUuid: string | null;
  q: string;
  filters: FilterGroup;
}

/** …and how to present them: the order, the grouping, and which page. */
export interface RecordPage extends RecordScope {
  groupBy: string | null;
  sortField: string | undefined;
  sortOrder: 1 | 0 | -1 | null | undefined;
  skip: number;
  limit: number;
}

/**
 * The parameters that decide *which* records come back.
 *
 * Built in one place because two surfaces ask the same question — the grid and the
 * metric strip above it — and a total that describes a different set of records
 * than the rows beneath it is worse than no total. The backend keeps the same
 * split for the same reason: `visible_stmt` answers "which records", and both the
 * listing and the metric scan are built on it.
 *
 * `filters` is always sent, including when it is empty. Omitting it means "use
 * whatever the view saved", which is not the same thing — and the difference is
 * exactly what lets you look past a view that filters.
 */
export function recordScopeParams({ viewUuid, q, filters }: RecordScope) {
  const params = new URLSearchParams();
  if (viewUuid) params.set("view_uuid", viewUuid);
  if (q.trim()) params.set("q", q.trim());
  params.set("filters", JSON.stringify(filters));
  return params;
}

/**
 * The full query for one page of records.
 *
 * Grouping and the sort are both sent, and they are no longer in competition:
 * the server orders by the group column first — which is what keeps a group's
 * records adjacent, and therefore renderable under one subheader — and breaks
 * ties inside each group with the sort. This used to send the group column *as*
 * the sort, which held the groups together by throwing the sort away.
 *
 * Every value is sent explicitly, empty string included, so the page states its
 * whole question rather than leaving the server to fill gaps from the view. The
 * two disagreeing is how a grid ends up showing something nobody selected.
 */
export function recordParams(page: RecordPage): string {
  const params = recordScopeParams(page);
  params.set("skip", String(page.skip));
  params.set("limit", String(page.limit));
  // The total comes from `items/count`, asked once per scope rather than once per
  // page. Left on, every page turn would make the server count every matching
  // record again to arrive at the number it returned last time — 12 ms against a
  // table of 100,000 where the page itself is 4 ms, and 268 ms under a search.
  params.set("total", "false");
  params.set("group_by", page.groupBy ?? "");
  params.set(
    "sort_by",
    page.sortField && page.sortOrder ? page.sortField.replace(/^data\./, "") : "",
  );
  if (page.sortOrder) params.set("sort_dir", page.sortOrder === 1 ? "asc" : "desc");
  return params.toString();
}
