import { describe, expect, it } from "vitest";

import type { Column } from "../../lib/types";
import { resolveDisplayColumn } from "./displayColumn";

/** Only the two fields the resolver reads; the rest of a Column is irrelevant here. */
function col(uuid: string): Column {
  return { uuid, name: uuid, display_name: uuid, type: "text", config: {} } as Column;
}

const first = col("c-first");
const second = col("c-second");

describe("resolveDisplayColumn", () => {
  it("prefers the lookup's configured display column", () => {
    expect(resolveDisplayColumn("c-second", { uuid: "c-first" }, [first, second])).toBe(
      "c-second",
    );
  });

  /** The server takes `display_column_uuid` at face value too. A lookup pointing at a
   *  column that no longer exists is a broken column, and labelling its rows off some
   *  other field would hide that rather than fix it. */
  it("takes a configured column even when it is not among the target's columns", () => {
    expect(resolveDisplayColumn("c-gone", { uuid: "c-first" }, [first, second])).toBe(
      "c-gone",
    );
  });

  it("falls back to the target's primary column", () => {
    expect(resolveDisplayColumn(null, { uuid: "c-second" }, [first, second])).toBe(
      "c-second",
    );
  });

  /** Deleting a column nulls the table's pointer server-side, but a cached table payload
   *  can outlive that — so a primary naming nothing must not win over the first column. */
  it("skips a primary column that has left the table's shape", () => {
    expect(resolveDisplayColumn(null, { uuid: "c-gone" }, [first, second])).toBe(
      "c-first",
    );
  });

  /** The load-bearing step: `primary_column` is only ever set by an explicit PATCH, so
   *  most tables have none and this is the branch that actually runs. */
  it("falls back to the first column, in the order the API returned them", () => {
    expect(resolveDisplayColumn(null, null, [first, second])).toBe("c-first");
    expect(resolveDisplayColumn(undefined, undefined, [second, first])).toBe("c-second");
  });

  /** A target shared as a lookup target only refuses its schema, so the caller has no
   *  columns to offer — and no ability to create a row there either. */
  it("answers null when there is nothing that could be a label", () => {
    expect(resolveDisplayColumn(null, null, [])).toBeNull();
    expect(resolveDisplayColumn("", { uuid: "c-first" }, [])).toBeNull();
  });
});
