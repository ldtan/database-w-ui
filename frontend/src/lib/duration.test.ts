import { describe, expect, it } from "vitest";

import {
  durationFormat,
  formatDuration,
  parseDuration,
  type DurationFormat,
} from "./duration";
import type { Column } from "./types";

function column(config: Record<string, unknown>): Column {
  return {
    uuid: "c1",
    name: "length",
    display_name: "Length",
    type: "duration",
    config,
  } as Column;
}

describe("durationFormat", () => {
  it("defaults to h:mm and ignores a format it does not know", () => {
    expect(durationFormat(column({}))).toBe("h:mm");
    expect(durationFormat(column({ duration_format: "fortnights" }))).toBe("h:mm");
    expect(durationFormat(column({ duration_format: "m:ss" }))).toBe("m:ss");
  });
});

describe("parseDuration", () => {
  it("reads a bare number as seconds in every format", () => {
    // The rule the CSV round-trip rests on: the export writes raw seconds, so a
    // bare number can never mean anything else.
    for (const format of ["h:mm", "h:mm:ss", "m:ss", "decimal_hours"] as const) {
      expect(parseDuration(format, "5400")).toBe(5400);
    }
  });

  it.each([
    ["1:30", 5400],
    ["1:30:00", 5400],
    ["0:45:30", 2730],
    ["0:30", 1800],
  ])("reads the colon form %s as %i seconds", (written, seconds) => {
    expect(parseDuration("h:mm", written)).toBe(seconds);
  });

  it("reads two colon-parts against the column's format", () => {
    expect(parseDuration("h:mm", "1:30")).toBe(5400);
    expect(parseDuration("m:ss", "1:30")).toBe(90);
    // Three parts are h:mm:ss whatever the column says — there is no other
    // reading of them.
    expect(parseDuration("m:ss", "1:30:00")).toBe(5400);
  });

  it.each([
    ["90m", 5400],
    ["1.5h", 5400],
    ["1h30m", 5400],
    ["45s", 45],
    ["1h 30m", 5400],
  ])("reads the unit form %s as %i seconds", (written, seconds) => {
    expect(parseDuration("h:mm", written)).toBe(seconds);
  });

  it.each(["1:75", "an hour", "1:2:3:4", "90x", "", "  ", "-60", "1:3o"])(
    "refuses %s rather than guessing",
    (written) => {
      expect(parseDuration("h:mm", written)).toBeNull();
    },
  );
});

describe("formatDuration", () => {
  it.each([
    ["h:mm", 5400, "1:30"],
    ["h:mm", 90, "0:01"],
    ["h:mm:ss", 5400, "1:30:00"],
    ["h:mm:ss", 2730, "0:45:30"],
    ["m:ss", 90, "1:30"],
    ["m:ss", 5400, "90:00"],
    ["decimal_hours", 5400, "1.50"],
  ])("writes %s seconds in %s as %s", (format, seconds, written) => {
    expect(formatDuration(format as DurationFormat, seconds)).toBe(written);
  });

  it("rounds to whole seconds so the formats cannot disagree", () => {
    expect(formatDuration("h:mm:ss", 89.7)).toBe("0:01:30");
  });

  it("renders zero rather than treating it as empty — no time is a duration", () => {
    expect(formatDuration("h:mm", 0)).toBe("0:00");
  });

  it("passes a value it cannot read through rather than inventing one", () => {
    expect(formatDuration("h:mm", null)).toBe("");
    expect(formatDuration("h:mm", "unset")).toBe("unset");
  });
});

describe("round trip", () => {
  it.each([
    // Values each format can actually write. Not decimal_hours: its written form
    // is a bare number, which the parser reads as seconds on purpose —
    // `parseDuration`'s docstring says why, and the editor for that format never
    // goes through the parser.
    ["h:mm" as const, [0, 60, 1800, 5400]],
    ["h:mm:ss" as const, [0, 60, 90, 2730, 5400]],
    ["m:ss" as const, [0, 60, 90, 2730, 5400]],
  ])("%s survives format -> parse", (format, values) => {
    for (const seconds of values) {
      expect(parseDuration(format, formatDuration(format, seconds))).toBe(seconds);
    }
  });

  it("h:mm drops the seconds it does not show, and that is the format's cost", () => {
    // Retyping what an h:mm cell displays therefore truncates it to the minute.
    // Nothing rounds it back up, because the value on screen is the value being
    // read — a format that silently restored a hidden 30 seconds would be worse.
    expect(formatDuration("h:mm", 90)).toBe("0:01");
    expect(parseDuration("h:mm", "0:01")).toBe(60);
  });
});
