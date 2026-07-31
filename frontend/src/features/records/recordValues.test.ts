import { describe, expect, it } from "vitest";

import type { Column, Form } from "../../lib/types";
import {
  clearedNested,
  needsFullEditor,
  resolveFields,
  textMode,
  toStored,
} from "./recordValues";

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

describe("textMode", () => {
  it.each([
    [{ type: "text", config: { text_mode: "rich" } }, "rich"],
    [{ type: "text", config: { text_mode: "multiline" } }, "multiline"],
    [{ type: "text", config: { text_mode: "single" } }, "single"],
    [{ type: "text", config: {} }, "single"],
    [{ type: "text", config: { text_mode: "typo" } }, "single"],
    [{ type: "number", config: { text_mode: "rich" } }, "single"],
  ])("reads %j as %s", (over, expected) => {
    expect(textMode(column(over as Partial<Column>))).toBe(expected);
  });
});

describe("needsFullEditor", () => {
  it("is true only for rich and multiline text", () => {
    expect(needsFullEditor(column({ config: { text_mode: "rich" } }))).toBe(true);
    expect(needsFullEditor(column({ config: { text_mode: "multiline" } }))).toBe(true);
    expect(needsFullEditor(column())).toBe(false);
    expect(needsFullEditor(column({ type: "link" }))).toBe(false);
  });
});

describe("resolveFields and server-stamped columns", () => {
  const name = column({ uuid: "name" });
  const stamped = column({ uuid: "updated", config: { onupdate: "now" } });

  it("marks an onupdate column read-only with no form config", () => {
    const fields = resolveFields([name, stamped], null);
    expect(fields.map((f) => [f.column.uuid, f.enabled])).toEqual([
      ["name", true],
      ["updated", false],
    ]);
  });

  it("keeps it read-only even when the form config enables it", () => {
    const form = {
      config: {
        fields: [
          { column_uuid: "name", included: true, enabled: true, order: 0 },
          { column_uuid: "updated", included: true, enabled: true, order: 1 },
        ],
      },
    } as unknown as Form;
    const fields = resolveFields([name, stamped], form);
    expect(fields.find((f) => f.column.uuid === "updated")?.enabled).toBe(false);
  });

  // Read-only, not absent: a stamp is exactly the sort of thing you want to see
  // on a record you are editing.
  it("still returns the column so a form can show it", () => {
    const fields = resolveFields([name, stamped], null);
    expect(fields.map((f) => f.column.uuid)).toContain("updated");
  });

  it("leaves an ordinary column alone", () => {
    expect(resolveFields([name], null)[0].enabled).toBe(true);
  });
});
