import { describe, expect, it } from "vitest";

import type { Column } from "../../lib/types";
import { cellPatch, type CellDraft } from "./cellEdit";

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

function draft(over: Partial<CellDraft>): CellDraft {
  return { rowUuid: "r1", columnUuid: "c1", initial: null, value: null, ...over };
}

describe("cellPatch", () => {
  it("sends nothing when the draft was never touched", () => {
    const col = column();
    expect(cellPatch([col], col, draft({ initial: "Ada", value: "Ada" }))).toBeNull();
  });

  // The temporal round trip: `deserialize` turns a stored "14:30" into a Date and
  // `serialize` writes it back as "14:30:00". Compared against the stored string
  // that looks like an edit; compared against the seed it is what it is — nothing.
  it("sends nothing when an untouched temporal value fails to round-trip", () => {
    const col = column({ type: "time" });
    const seeded = new Date(2026, 6, 12, 14, 30, 0);
    expect(cellPatch([col], col, draft({ initial: seeded, value: seeded }))).toBeNull();
  });

  it("sends nothing when a typed value is put back the way it was", () => {
    const col = column();
    expect(cellPatch([col], col, draft({ initial: "Ada", value: "Ada" }))).toBeNull();
  });

  it("sends the new value when it changed", () => {
    const col = column();
    expect(cellPatch([col], col, draft({ initial: "Ada", value: "Bob" }))).toEqual({
      c1: "Bob",
    });
  });

  it("sends null when the cell was cleared", () => {
    const col = column();
    expect(cellPatch([col], col, draft({ initial: "Ada", value: "" }))).toEqual({
      c1: null,
    });
  });

  it("clears the lookups nested under a changed column", () => {
    const country = column({ uuid: "country", type: "lookup" });
    const city = column({ uuid: "city", config: { parent_column_uuid: "country" } });
    const columns = [country, city];
    expect(
      cellPatch(columns, country, draft({ columnUuid: "country", initial: "fr", value: "de" })),
    ).toEqual({ country: "de", city: null });
  });
});
