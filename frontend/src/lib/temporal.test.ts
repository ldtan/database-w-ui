import { describe, expect, it } from "vitest";

import type { Column, ColumnType } from "./types";
import {
  displayTemporal,
  formatTyped,
  parseTemporal,
  parseTyped,
  serializeTemporal,
  temporalBounds,
  temporalPlaceholder,
  temporalStatus,
} from "./temporal";

/** Enough of a column for the three functions that take one. */
function col(type: ColumnType, config: Record<string, unknown> = {}): Column {
  return { type, config } as unknown as Column;
}

const state = (type: ColumnType, typed: string, config = {}) =>
  temporalStatus(col(type, config), typed).state;

describe("parseTyped", () => {
  it("reads a date the several ways one is written", () => {
    const july12 = new Date(2026, 6, 12);
    for (const text of [
      "2026-07-12",
      "2026-7-12",
      "2026/07/12",
      "20260712",
      "  2026-07-12  ",
    ]) {
      expect(parseTyped("date", text), text).toEqual(july12);
    }
    expect(parseTyped("date", "2026-7-2")).toEqual(new Date(2026, 6, 2));
  });

  it("reads a time, colon-separated or compact", () => {
    expect(parseTyped("time", "14:30")).toEqual(new Date(1970, 0, 1, 14, 30));
    expect(parseTyped("time", "9:05")).toEqual(new Date(1970, 0, 1, 9, 5));
    expect(parseTyped("time", "14:30:45")).toEqual(
      new Date(1970, 0, 1, 14, 30, 45),
    );
    expect(parseTyped("time", "1430")).toEqual(new Date(1970, 0, 1, 14, 30));
    expect(parseTyped("time", "930")).toEqual(new Date(1970, 0, 1, 9, 30));
    expect(parseTyped("time", "143000")).toEqual(
      new Date(1970, 0, 1, 14, 30, 0),
    );
  });

  it("reads a date-time joined by either a T or a space", () => {
    const at1430 = new Date(2026, 6, 12, 14, 30);
    expect(parseTyped("datetime", "2026-07-12T14:30")).toEqual(at1430);
    expect(parseTyped("datetime", "2026-07-12 14:30")).toEqual(at1430);
    expect(parseTyped("datetime", "2026-07-12t14:30")).toEqual(at1430);
    expect(parseTyped("datetime", "2026-07-12T14:30:45")).toEqual(
      new Date(2026, 6, 12, 14, 30, 45),
    );
  });

  it("reads a bare date in a datetime column as midnight", () => {
    // What `datetime.fromisoformat("2026-07-12")` does on the server, and what
    // somebody has typed for the moment before they reach the time.
    expect(parseTyped("datetime", "2026-07-12")).toEqual(new Date(2026, 6, 12));
  });

  it("is null for text that is not a value yet", () => {
    for (const text of ["", "2026-0", "2026-", "2026-07-", "20260"]) {
      expect(parseTyped("date", text), text).toBeNull();
    }
    for (const text of ["14:", "9:0", "14:30:", "14305"]) {
      expect(parseTyped("time", text), text).toBeNull();
    }
    expect(parseTyped("datetime", "2026-07-12T")).toBeNull();
  });

  it("is null for a reading no calendar or clock has", () => {
    for (const text of [
      "2026-13-01",
      "2026-00-01",
      "2026-02-30", // JS rolls this to 2 March; `date.fromisoformat` raises
      "2026-04-31",
      "0000-01-01",
      "20261301",
      "202607123",
    ]) {
      expect(parseTyped("date", text), text).toBeNull();
    }
    for (const text of ["25:00", "12:60", "9:99", "2500", "1234567"]) {
      expect(parseTyped("time", text), text).toBeNull();
    }
  });

  it("refuses a day-first date and anything spelled out", () => {
    expect(parseTyped("date", "12/07/2026")).toBeNull();
    expect(parseTyped("date", "12-07-2026")).toBeNull();
    expect(parseTyped("date", "July 12 2026")).toBeNull();
    expect(parseTyped("datetime", "12/07/2026 14:30")).toBeNull();
    expect(parseTyped("date", "2026-07-12 14:30")).toBeNull(); // wrong type
  });
});

describe("temporalStatus", () => {
  it("calls a prefix partial rather than wrong", () => {
    for (const text of ["2", "20", "2026", "2026-", "2026-0", "2026-07-"]) {
      expect(state("date", text), text).toBe("partial");
    }
    for (const text of ["1", "14", "14:", "9:0", "14:30:", "093"]) {
      expect(state("time", text), text).toBe("partial");
    }
    for (const text of ["2026-07-12T", "2026-07-12 1", "2026-07-12 14:3"]) {
      expect(state("datetime", text), text).toBe("partial");
    }
  });

  it("calls an impossible component invalid without waiting for the rest", () => {
    expect(state("date", "2026-13")).toBe("invalid");
    expect(state("date", "2026-07-32")).toBe("invalid");
    expect(state("time", "25:")).toBe("invalid");
    expect(state("time", "9:6")).toBe("invalid"); // no minute begins with a 6
    expect(state("time", "99")).toBe("invalid");
  });

  it("is empty, silently, for an empty box", () => {
    expect(temporalStatus(col("date"), "")).toEqual({
      state: "empty",
      message: "",
    });
    expect(temporalStatus(col("date"), "   ").message).toBe("");
  });

  it("names the day-first mistake instead of just refusing it", () => {
    const s = temporalStatus(col("date"), "12/07/2026");
    expect(s.state).toBe("invalid");
    expect(s.message).toContain("year first");
    expect(s.message).toContain("2026-07-12");
  });

  it("echoes the server's own wording for a value it cannot read", () => {
    // These strings are `_TEMPORAL` in backend/src/tables/columns/validation.py.
    // If one of them changes there, this is what says so.
    expect(temporalStatus(col("date"), "2026-02-30").message).toContain(
      "a date (YYYY-MM-DD)",
    );
    expect(temporalStatus(col("time"), "25:00").message).toContain(
      "a time (HH:MM[:SS])",
    );
    expect(temporalStatus(col("datetime"), "2026-02-30 14:30").message).toContain(
      "a date-time (ISO-8601)",
    );
  });

  it("spells a finished value back out, with its weekday", () => {
    const s = temporalStatus(col("date"), "2026-07-12");
    expect(s.state).toBe("valid");
    expect(s.date).toEqual(new Date(2026, 6, 12));
    expect(s.message).toContain("2026"); // 12 July 2026 was a Sunday
    expect(s.message).toMatch(/sun/i);
  });
});

describe("temporalStatus bounds", () => {
  const bounded = { min: "2026-01-01", max: "2026-12-31" };

  it("passes a value inside the range and on either edge", () => {
    for (const text of ["2026-06-01", "2026-01-01", "2026-12-31"]) {
      // The server compares with `<` / `>`, so a value *on* the bound is fine —
      // which is what "on or after" says.
      expect(state("date", text, bounded), text).toBe("valid");
    }
  });

  it("refuses one outside it, in the server's words", () => {
    const before = temporalStatus(col("date", bounded), "2025-12-31");
    expect(before.state).toBe("outOfRange");
    expect(before.message).toContain("on or after 2026-01-01");

    const after = temporalStatus(col("date", bounded), "2027-01-01");
    expect(after.state).toBe("outOfRange");
    expect(after.message).toContain("on or before 2026-12-31");
  });

  it("bounds a time and a date-time by their own parsers", () => {
    const time = col("time", { min: "09:00", max: "17:00" });
    expect(temporalStatus(time, "08:59").state).toBe("outOfRange");
    expect(temporalStatus(time, "09:00").state).toBe("valid");

    const dt = col("datetime", { min: "2026-01-01T08:00:00" });
    expect(temporalStatus(dt, "2026-01-01 07:59").state).toBe("outOfRange");
    expect(temporalStatus(dt, "2026-01-01 08:00").state).toBe("valid");
  });

  it("leaves a filter's value unbounded", () => {
    // A condition is a question, not a cell: `validate_scalar` never sees it, so
    // "everything before this column's minimum" must stay askable.
    expect(temporalStatus(col("date", bounded), "2025-12-31", "query").state).toBe(
      "valid",
    );
  });

  it("declines to judge a bound carrying a timezone", () => {
    // `parseTemporal` ignores offsets by design, so comparing against a zoned bound
    // would be a guess — and the server refuses to compare these at all.
    for (const min of ["2026-01-01T00:00:00Z", "2026-01-01T00:00:00+08:00"]) {
      const c = col("datetime", { min });
      expect(temporalBounds(c).min, min).toBeNull();
      expect(temporalStatus(c, "2025-01-01 00:00").state, min).toBe("valid");
    }
  });

  it("reads a plain bound", () => {
    const { min, max } = temporalBounds(col("date", bounded));
    expect(min).toEqual(new Date(2026, 0, 1));
    expect(max).toEqual(new Date(2026, 11, 31));
    expect(temporalBounds(col("date"))).toEqual({ min: null, max: null });
  });
});

describe("round trips", () => {
  const cases: [ColumnType, Date][] = [
    ["date", new Date(2026, 6, 12)],
    ["time", new Date(1970, 0, 1, 14, 30)],
    ["time", new Date(1970, 0, 1, 14, 30, 45)],
    ["datetime", new Date(2026, 6, 12, 14, 30)],
    ["datetime", new Date(2026, 6, 12, 14, 30, 45)],
  ];

  it("re-reads everything it writes", () => {
    for (const [type, d] of cases) {
      expect(parseTyped(type, formatTyped(type, d)), formatTyped(type, d)).toEqual(
        d,
      );
    }
  });

  it("shows a stored value the same way the grid does", () => {
    // The property the inline editor rests on: opening a cell must not appear to
    // change it.
    const stored: [ColumnType, string][] = [
      ["date", "2026-07-12"],
      ["time", "14:30"],
      ["time", "14:30:00"],
      ["time", "14:30:45"],
      ["datetime", "2026-07-12T14:30:00"],
      ["datetime", "2026-07-12T14:30:45"],
    ];
    for (const [type, raw] of stored) {
      const d = parseTemporal(type, raw)!;
      expect(formatTyped(type, d), raw).toBe(displayTemporal(type, raw));
    }
  });

  it("settles after one canonicalisation", () => {
    for (const [type, d] of cases) {
      const stored = serializeTemporal(type, d);
      expect(parseTyped(type, stored)).toEqual(d);
      expect(serializeTemporal(type, parseTyped(type, stored)!)).toBe(stored);
    }
    // The lossy step `cellEdit.ts` documents: what is typed is not what is stored.
    expect(serializeTemporal("time", parseTyped("time", "14:30")!)).toBe(
      "14:30:00",
    );
  });
});

describe("temporalPlaceholder", () => {
  it("is the mask the server names in its own error", () => {
    expect(temporalPlaceholder("date")).toBe("YYYY-MM-DD");
    expect(temporalPlaceholder("time")).toBe("HH:MM");
    expect(temporalPlaceholder("datetime")).toBe("YYYY-MM-DD HH:MM");
  });
});
