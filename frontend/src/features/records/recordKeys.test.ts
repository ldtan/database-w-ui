import { describe, expect, it } from "vitest";

import type { FilterGroup } from "../../lib/types";
import { metricValuesKey } from "../metrics/metricKeys";
import { recordPagesKey } from "./recordKeys";

const NO_FILTER: FilterGroup = { conjunction: "and", conditions: [] };

describe("recordPagesKey", () => {
  it("names the grid's pages inside the records namespace", () => {
    expect(recordPagesKey("list-1")).toEqual(["items", "list-1", "page"]);
  });

  // The bug this pins: both keys live under ["items", listUuid] so that one
  // invalidation refreshes the grid and the metric cards together. That makes the
  // prefix unsafe to *write* through, and a page-shaped updater handed a
  // MetricValues throws inside onMutate — which runs before mutationFn, so the
  // PATCH is never sent at all. They must diverge before the write filter's depth.
  it("does not prefix-match the metric values key", () => {
    const pages = recordPagesKey("list-1");
    const metrics = metricValuesKey("list-1", null, "", NO_FILTER);
    expect(metrics.slice(0, pages.length)).not.toEqual(pages);
  });
});
