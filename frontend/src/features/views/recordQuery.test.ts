import { describe, expect, it } from "vitest";

import type { FilterGroup } from "../../lib/types";
import { recordParams, recordScopeParams } from "./recordQuery";

const NO_FILTER: FilterGroup = { conjunction: "and", conditions: [] };

const PAGE = {
  viewUuid: null,
  q: "",
  filters: NO_FILTER,
  groupBy: null,
  sortField: undefined,
  sortOrder: undefined,
  skip: 0,
  limit: 25,
} as const;

describe("recordParams", () => {
  // The grid reads its total from `items/count`, which is cached across the pages
  // of one query. Drop this and nothing breaks visibly -- the number stays right,
  // because the header comes back too -- but the server silently goes back to
  // counting every matching record on every page turn.
  it("declines the total, which the count query supplies instead", () => {
    expect(new URLSearchParams(recordParams(PAGE)).get("total")).toBe("false");
  });

  it("sends the filter even when it is empty", () => {
    // Omitting it would mean "use whatever the view saved", which is a different
    // question and not the one the grid is asking.
    const params = new URLSearchParams(recordParams(PAGE));
    expect(params.get("filters")).toBe(JSON.stringify(NO_FILTER));
  });

  it("strips the grid's `data.` prefix off the sort column", () => {
    const params = new URLSearchParams(
      recordParams({ ...PAGE, sortField: "data.col-1", sortOrder: -1 }),
    );
    expect(params.get("sort_by")).toBe("col-1");
    expect(params.get("sort_dir")).toBe("desc");
  });
});

describe("recordScopeParams", () => {
  // The count endpoint takes only what changes *which* records match. Sending a
  // page or an order would invite a caller to re-ask a question whose answer
  // cannot have moved -- and would be a second spelling of the cache key.
  it("carries the scope and nothing about the page or the order", () => {
    const params = recordScopeParams({
      viewUuid: "view-9",
      q: "ann",
      filters: NO_FILTER,
    });
    expect(params.get("view_uuid")).toBe("view-9");
    expect(params.get("q")).toBe("ann");
    expect(params.get("filters")).toBe(JSON.stringify(NO_FILTER));
    for (const absent of ["skip", "limit", "sort_by", "group_by", "total"]) {
      expect(params.get(absent)).toBeNull();
    }
  });
});
