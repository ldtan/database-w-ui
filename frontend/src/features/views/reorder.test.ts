import { describe, expect, it } from "vitest";

import { moveTo } from "./reorder";

const ROWS = ["a", "b", "c", "d"] as const;

describe("moveTo", () => {
  it("moves an entry down and closes the gap behind it", () => {
    expect(moveTo(ROWS, 0, 2)).toEqual(["b", "c", "a", "d"]);
  });

  it("moves an entry up and pushes the rest down", () => {
    expect(moveTo(ROWS, 3, 1)).toEqual(["a", "d", "b", "c"]);
  });

  it("splices rather than swaps, which is what a multi-place drag needs", () => {
    // A swap would give ["d", "b", "c", "a"] — "d" lands correctly but "a" is
    // flung to the far end instead of shifting one place. Adjacent moves cannot
    // tell the two apart, which is why the up/down buttons never exposed this.
    expect(moveTo(ROWS, 0, 3)).toEqual(["b", "c", "d", "a"]);
  });

  it("is a no-op when an entry is moved onto itself", () => {
    expect(moveTo(ROWS, 2, 2)).toEqual([...ROWS]);
  });

  it("is a no-op for out-of-range indices, because both callers can produce them", () => {
    // The up button on the first row, the down button on the last, and a drag that
    // ends outside the table. None of them has anything to do with an exception.
    expect(moveTo(ROWS, 0, -1)).toEqual([...ROWS]);
    expect(moveTo(ROWS, 3, 4)).toEqual([...ROWS]);
    expect(moveTo(ROWS, -1, 0)).toEqual([...ROWS]);
    expect(moveTo(ROWS, 9, 0)).toEqual([...ROWS]);
  });

  it("never adds, drops or duplicates an entry", () => {
    // The invariant that matters to the callers: a view's column_order and a form's
    // fields are saved back whole, so losing an entry here silently drops a column.
    for (let from = 0; from < ROWS.length; from += 1) {
      for (let to = 0; to < ROWS.length; to += 1) {
        expect([...moveTo(ROWS, from, to)].sort()).toEqual([...ROWS].sort());
      }
    }
  });

  it("returns a new array rather than mutating the input", () => {
    const original = [...ROWS];
    moveTo(original, 0, 3);
    expect(original).toEqual([...ROWS]);
  });
});
