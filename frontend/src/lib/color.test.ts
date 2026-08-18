import { describe, expect, it } from "vitest";

import {
  colorSwatches,
  isColor,
  normalizeColor,
  readableTextOn,
} from "./color";
import type { Column } from "./types";

function column(config: Record<string, unknown>): Column {
  return { uuid: "c1", name: "tag", display_name: "Tag", type: "color", config } as Column;
}

describe("normalizeColor", () => {
  it.each([
    ["#ABC", "#aabbcc"],
    ["#abc", "#aabbcc"],
    ["#AABBCC", "#aabbcc"],
    ["  #1D4ED8  ", "#1d4ed8"],
    ["#1d4ed8", "#1d4ed8"],
  ])("puts %s in the stored form %s", (raw, stored) => {
    expect(normalizeColor(raw)).toBe(stored);
  });

  it("leaves something that is not a colour alone rather than guessing", () => {
    // The caller decides what to do with it; inventing a colour here would put a
    // value on screen that is in no cell.
    expect(normalizeColor("rebeccapurple")).toBe("rebeccapurple");
  });
});

describe("isColor", () => {
  it.each(["#1d4ed8", "#ABC", "  #aabbcc  "])("accepts %s", (value) => {
    expect(isColor(value)).toBe(true);
  });

  it.each([
    "1d4ed8",
    "#1d4ed",
    "#1d4ed88",
    "#ggggkk",
    "rgb(29, 78, 216)",
    "",
    null,
    42,
  ])("rejects %s", (value) => {
    expect(isColor(value)).toBe(false);
  });
});

describe("colorSwatches", () => {
  it("normalises the palette so a swatch matches what a cell would store", () => {
    expect(colorSwatches(column({ swatches: ["#ABC", "#1D4ED8"] }))).toEqual([
      "#aabbcc",
      "#1d4ed8",
    ]);
  });

  it("drops entries that are not colours rather than rendering them", () => {
    expect(colorSwatches(column({ swatches: ["#1d4ed8", "octarine"] }))).toEqual([
      "#1d4ed8",
    ]);
  });

  it("is empty when the column has no palette", () => {
    expect(colorSwatches(column({}))).toEqual([]);
    expect(colorSwatches(column({ swatches: null }))).toEqual([]);
  });
});

describe("readableTextOn", () => {
  it("puts dark text on a light fill and light text on a dark one", () => {
    expect(readableTextOn("#ffffff")).toBe("#000000");
    expect(readableTextOn("#000000")).toBe("#ffffff");
    expect(readableTextOn("#1d4ed8")).toBe("#ffffff");
    expect(readableTextOn("#fef08a")).toBe("#000000");
  });

  it("falls back to black rather than throwing on a value it cannot read", () => {
    expect(readableTextOn("octarine")).toBe("#000000");
  });
});
