import { describe, expect, it } from "vitest";

import type { FilterGroup } from "../../lib/types";
import { metricValuesKey } from "../metrics/metricKeys";
import { recordCountKey, recordPagesKey } from "./recordKeys";

const NO_FILTER: FilterGroup = { conjunction: "and", conditions: [] };

describe("recordPagesKey", () => {
  it("names the grid's pages inside the records namespace", () => {
    expect(recordPagesKey("table-1")).toEqual(["records", "table-1", "page"]);
  });

  // The bug this pins: both keys live under ["records", tableUuid] so that one
  // invalidation refreshes the grid and the metric cards together. That makes the
  // prefix unsafe to *write* through, and a page-shaped updater handed a
  // MetricValues throws inside onMutate — which runs before mutationFn, so the
  // PATCH is never sent at all. They must diverge before the write filter's depth.
  it("does not prefix-match the metric values key", () => {
    const pages = recordPagesKey("table-1");
    const metrics = metricValuesKey("table-1", null, "", NO_FILTER);
    expect(metrics.slice(0, pages.length)).not.toEqual(pages);
  });

  // Same hazard, third query under the namespace: the optimistic cell edit writes
  // through `recordPagesKey`, and a rows-shaped updater handed `{ total }` would
  // throw inside onMutate and cost the PATCH.
  it("does not prefix-match the count key", () => {
    const pages = recordPagesKey("table-1");
    const count = recordCountKey("table-1", null, "", NO_FILTER);
    expect(count.slice(0, pages.length)).not.toEqual(pages);
  });
});

describe("recordCountKey", () => {
  it("sits under the records namespace, so a record write refreshes it", () => {
    const key = recordCountKey("table-1", "view-9", "ann", NO_FILTER);
    expect(key.slice(0, 2)).toEqual(["records", "table-1"]);
    expect(key[2]).toBe("count");
  });

  // The whole point of the separate query: the total cannot change between pages
  // of one search, so every page must read the same cache entry. If the page ever
  // reached this key, the server would recount on every page turn — which is the
  // behaviour this replaced.
  it("is the same key for every page, order and grouping of one scope", () => {
    const a = recordCountKey("table-1", "view-9", "ann", NO_FILTER);
    const b = recordCountKey("table-1", "view-9", "ann", NO_FILTER);
    expect(a).toEqual(b);
  });

  it("changes when the view, the search or the filter changes", () => {
    const base = recordCountKey("table-1", "view-9", "ann", NO_FILTER);
    expect(recordCountKey("table-1", "view-8", "ann", NO_FILTER)).not.toEqual(base);
    expect(recordCountKey("table-1", "view-9", "bob", NO_FILTER)).not.toEqual(base);
    expect(
      recordCountKey("table-1", "view-9", "ann", {
        conjunction: "and",
        conditions: [{ column_uuid: "c", operator: "equals", value: "x" }],
      }),
    ).not.toEqual(base);
  });
});
