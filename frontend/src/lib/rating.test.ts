import { describe, expect, it } from "vitest";

import { ratingScale, ratingValue, RATING_MAX } from "./rating";
import type { Column } from "./types";

function column(config: Record<string, unknown>): Column {
  return {
    uuid: "c1",
    name: "score",
    display_name: "Score",
    type: "rating",
    config,
  } as Column;
}

describe("ratingScale", () => {
  it("defaults to five", () => {
    expect(ratingScale(column({}))).toBe(5);
  });

  it("takes the scale from the shared `max` key", () => {
    expect(ratingScale(column({ max: 10 }))).toBe(10);
  });

  it.each([0, -1, 2.5, RATING_MAX + 1, "five", null])(
    "falls back to five for a scale of %s the server would have refused",
    (max) => {
      expect(ratingScale(column({ max }))).toBe(5);
    },
  );
});

describe("ratingValue", () => {
  it("reads a whole rating", () => {
    expect(ratingValue(column({}), 3)).toBe(3);
  });

  it("is null for unrated, and zero is unrated", () => {
    // The one rule that shapes this type: there is no nought on the scale, so an
    // unrated record is an empty cell and nothing else.
    expect(ratingValue(column({}), null)).toBeNull();
    expect(ratingValue(column({}), 0)).toBeNull();
    expect(ratingValue(column({}), "4")).toBeNull();
  });

  it("clamps a value stored under a wider scale rather than dropping it", () => {
    // A cell written when the column went to ten still has to render on a column
    // since narrowed to five. The server refuses the *next write* of it; this
    // only decides what is drawn meanwhile.
    expect(ratingValue(column({ max: 5 }), 7)).toBe(5);
  });

  it("rounds, so a stored 3.4 is three glyphs rather than none", () => {
    expect(ratingValue(column({}), 3.4)).toBe(3);
    expect(ratingValue(column({}), 3.6)).toBe(4);
  });
});
