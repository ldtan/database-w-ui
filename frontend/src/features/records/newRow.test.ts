import { describe, expect, it } from "vitest";

import type { Column, ListItem } from "../../lib/types";
import { isNewRow, landedOnPage, newRowItem, NEW_ROW_UUID } from "./newRow";
import { buildRecordData, type ResolvedField } from "./recordValues";

function column(over: Partial<Column> = {}): Column {
  return {
    uuid: "c1",
    name: "c1",
    display_name: "C1",
    type: "text",
    config: {},
    ...over,
  } as Column;
}

const field = (col: Column, enabled = true): ResolvedField => ({
  column: col,
  enabled,
  label: col.display_name,
});

const item = (uuid: string): ListItem =>
  ({ uuid, data: {}, permissions_inherit: true }) as ListItem;

describe("isNewRow", () => {
  it("knows the draft row", () => {
    expect(isNewRow(NEW_ROW_UUID)).toBe(true);
  });

  it("does not answer to a record's uuid", () => {
    expect(isNewRow("6f1c2f0e-6b3a-4a3e-8a2f-1d9b0c7e5a11")).toBe(false);
  });

  it("carries the draft's values as a row the grid can render", () => {
    const row = newRowItem({ c1: "Ada" });
    expect(row.uuid).toBe(NEW_ROW_UUID);
    expect(row.data).toEqual({ c1: "Ada" });
  });
});

describe("landedOnPage", () => {
  it("finds the record the refetch brought back", () => {
    expect(landedOnPage([item("a"), item("b")], "b")).toBe(true);
  });

  it("says no when the sort or filter put it elsewhere", () => {
    expect(landedOnPage([item("a")], "b")).toBe(false);
  });

  it("says no rather than throwing when the page is not loaded", () => {
    expect(landedOnPage(undefined, "b")).toBe(false);
  });
});

// The draft holds *stored* values — the same shape a record's `data` has — which is
// what lets `renderCell` and `InlineCellEditor` treat the draft row as a record. The
// POST body therefore needs no helper of its own: `buildRecordData`'s create branch
// already does the two things a new record needs, and its serialize pass is a no-op
// on a value that never left stored form. These pin that, because it is the whole
// reason there is no second body-builder here.
describe("buildRecordData over a stored-form draft", () => {
  it("leaves an already-serialized temporal value alone", () => {
    const col = column({ type: "date" });
    expect(buildRecordData([field(col)], { c1: "2026-08-07" }, false)).toEqual({
      c1: "2026-08-07",
    });
  });

  it("omits an empty cell so the column's default still applies", () => {
    const col = column();
    // Every spelling of "nothing typed here": never opened, opened and cleared,
    // and an emptied multi-select.
    expect(
      buildRecordData([field(col)], {}, false),
    ).toEqual({});
    expect(buildRecordData([field(col)], { c1: null }, false)).toEqual({});
    expect(buildRecordData([field(col)], { c1: "" }, false)).toEqual({});
    expect(buildRecordData([field(col)], { c1: [] }, false)).toEqual({});
  });

  it("drops a field the create form marks read-only", () => {
    const col = column();
    expect(buildRecordData([field(col, false)], { c1: "Ada" }, false)).toEqual({});
  });
});
