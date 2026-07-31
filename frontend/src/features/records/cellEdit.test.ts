import { describe, expect, it } from "vitest";

import type { Column } from "../../lib/types";
import { cellEditKinds, cellPatch, type CellDraft } from "./cellEdit";
import { deserialize, toStored } from "./recordValues";

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
    // Typing a value and putting it back is the same draft state — CellDraft
    // carries only the seed and the current value, so the two gestures are
    // indistinguishable here by construction.
    expect(cellPatch([col], col, draft({ initial: "Ada", value: "Ada" }))).toBeNull();
  });

  it("sends nothing when an untouched temporal value fails to round-trip", () => {
    const col = column({ type: "time" });
    const stored = "14:30";
    const seeded = deserialize(col, stored);

    // The round trip really is lossy — this is the bug's precondition, and
    // without it the test below would pass for the wrong reason.
    expect(toStored(col, seeded)).not.toBe(stored);

    // ...and comparing the draft against its seed absorbs that, rather than
    // inventing an edit out of the difference.
    expect(cellPatch([col], col, draft({ initial: seeded, value: seeded }))).toBeNull();
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

describe("cellEditKinds", () => {
  const field = (col: Column, enabled = true) => ({
    column: col,
    enabled,
    label: col.display_name,
  });

  it("sends a rich-text column to the dialog", () => {
    const col = column({ config: { text_mode: "rich" } });
    expect(cellEditKinds([field(col)]).get("c1")).toBe("dialog");
  });

  it("opens an ordinary column in place", () => {
    expect(cellEditKinds([field(column())]).get("c1")).toBe("inline");
  });

  it("locks a field the form marks read-only", () => {
    expect(cellEditKinds([field(column(), false)]).get("c1")).toBe("locked");
  });
});
