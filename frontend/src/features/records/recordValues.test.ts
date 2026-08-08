import { describe, expect, it } from "vitest";

import type { Column, Form } from "../../lib/types";
import {
  clearedNested,
  needsFullEditor,
  resolveFields,
  resolveMergedFields,
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

describe("resolveMergedFields", () => {
  const cols = [
    column({ uuid: "a1", display_name: "Name" }),
    column({ uuid: "a2", display_name: "Age", type: "number" }),
  ];
  const field = (uuid: string, over = {}) => ({
    column_uuid: uuid,
    included: true,
    enabled: true,
    order: 0,
    ...over,
  });

  it("resolves borrowed fields in configured order", () => {
    const merge = {
      link_column_uuid: "L",
      allow_shared: false,
      fields: [field("a2", { order: 0 }), field("a1", { order: 1 })],
    };
    expect(resolveMergedFields(cols, merge).map((f) => f.column.uuid)).toEqual([
      "a2",
      "a1",
    ]);
  });

  /**
   * The inverse of `resolveFields`, and the reason this is a separate function.
   * The linked list is not this form's to enumerate — "all of them" would widen
   * silently as that list gained columns.
   */
  it("treats an empty field list as none, not all", () => {
    const merge = { link_column_uuid: "L", allow_shared: false, fields: [] };
    expect(resolveMergedFields(cols, merge)).toEqual([]);
  });

  it("does not append columns the config never mentions", () => {
    const merge = {
      link_column_uuid: "L",
      allow_shared: false,
      fields: [field("a1")],
    };
    expect(resolveMergedFields(cols, merge).map((f) => f.column.uuid)).toEqual([
      "a1",
    ]);
  });

  it("drops excluded fields", () => {
    const merge = {
      link_column_uuid: "L",
      allow_shared: false,
      fields: [field("a1", { included: false }), field("a2")],
    };
    expect(resolveMergedFields(cols, merge).map((f) => f.column.uuid)).toEqual([
      "a2",
    ]);
  });

  /** How a merged form degrades when the linked list's schema is a 403. */
  it("yields nothing when the linked schema is unavailable", () => {
    const merge = {
      link_column_uuid: "L",
      allow_shared: false,
      fields: [field("a1")],
    };
    expect(resolveMergedFields([], merge)).toEqual([]);
  });

  it("yields nothing when the form is not merged", () => {
    expect(resolveMergedFields(cols, null)).toEqual([]);
    expect(resolveMergedFields(cols, undefined)).toEqual([]);
  });

  it("applies a label override and honours enabled", () => {
    const merge = {
      link_column_uuid: "L",
      allow_shared: false,
      fields: [field("a1", { label_override: " Full name ", enabled: false })],
    };
    const [only] = resolveMergedFields(cols, merge);
    expect(only.label).toBe("Full name");
    expect(only.enabled).toBe(false);
  });
});
