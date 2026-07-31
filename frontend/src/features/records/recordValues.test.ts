import { describe, expect, it } from "vitest";

import type { Column } from "../../lib/types";
import { clearedNested, toStored } from "./recordValues";

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

describe("toStored", () => {
  it.each([[undefined], [null], [""], [[]]])(
    "collapses %j to null",
    (value) => {
      expect(toStored(column(), value)).toBeNull();
    },
  );

  it("keeps a value that is merely falsy", () => {
    expect(toStored(column({ type: "number" }), 0)).toBe(0);
    expect(toStored(column({ type: "checkbox" }), false)).toBe(false);
  });

  it("serializes a Date for its column type", () => {
    const date = new Date(2026, 6, 12, 14, 30, 0);
    expect(toStored(column({ type: "date" }), date)).toBe("2026-07-12");
    expect(toStored(column({ type: "time" }), date)).toBe("14:30:00");
  });
});

describe("clearedNested", () => {
  const columns = [
    column({ uuid: "country" }),
    column({ uuid: "city", config: { parent_column_uuid: "country" } }),
    column({ uuid: "street", config: { parent_column_uuid: "city" } }),
    column({ uuid: "unrelated" }),
  ];

  it("clears children and grandchildren of a changed parent", () => {
    expect(clearedNested(columns, "country")).toEqual({
      city: null,
      street: null,
    });
  });

  it("clears nothing for a column with no children", () => {
    expect(clearedNested(columns, "unrelated")).toEqual({});
  });
});
