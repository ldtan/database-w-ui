import type { Column, ColumnType } from "./types";

// Date/time columns store a wall-clock string: "2026-07-12", "14:30:00", or
// "2026-07-12T14:30:00". They are calendar values, not instants — a birth date or
// a meeting time does not shift because you flew to another timezone. So parse and
// format the components in local time. Going through Date#toISOString() would
// convert via UTC and land on the previous day for anyone west of Greenwich.
//
// The same rule is why the one `Intl.DateTimeFormat` in this file is confined to
// `temporalStatus`: it writes the *sentence* under a box ("Sunday, 12 July 2026"),
// never a value. Nothing formatted by it is stored, sent or compared.
//
// One thing local time costs us, and it is unavoidable with a `Date`: on the day a
// zone springs forward, a wall-clock reading in the missing hour does not exist
// locally, so `02:30` snaps to `03:30` when the box canonicalises. The picker has
// always done this; typing now does it too, and the server — which stores the
// string it is given — is the one place the reading survives intact.

const TEMPORAL: readonly ColumnType[] = ["date", "time", "datetime"];

export function isTemporal(type: ColumnType): boolean {
  return TEMPORAL.includes(type);
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

export function fmtDate(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function fmtTime(d: Date, withSeconds = true): string {
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return withSeconds ? `${hm}:${pad(d.getSeconds())}` : hm;
}

/** Date -> the string the API stores. */
export function serializeTemporal(type: ColumnType, d: Date): string {
  if (type === "date") return fmtDate(d);
  if (type === "time") return fmtTime(d);
  return `${fmtDate(d)}T${fmtTime(d)}`;
}

const DATE_RE = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{1,2}):(\d{2})(?::(\d{2}))?)?/;
const TIME_RE = /^(\d{1,2}):(\d{2})(?::(\d{2}))?/;

/**
 * Stored string -> Date for the pickers. Null when the value is absent or not a
 * shape we recognize, so a malformed cell degrades to raw text instead of an
 * "Invalid Date". Any timezone suffix is ignored: the stored wall-clock reading is
 * what we mean, and it is what we write back.
 */
export function parseTemporal(type: ColumnType, raw: unknown): Date | null {
  if (raw === null || raw === undefined || raw === "") return null;
  const s = String(raw);

  if (type === "time") {
    const m = TIME_RE.exec(s);
    if (!m) return null;
    return new Date(1970, 0, 1, +m[1], +m[2], m[3] ? +m[3] : 0);
  }

  const m = DATE_RE.exec(s);
  if (!m) return null;
  const [, y, mo, da, h, mi, se] = m;
  if (type === "date") return new Date(+y, +mo - 1, +da);
  return new Date(+y, +mo - 1, +da, h ? +h : 0, mi ? +mi : 0, se ? +se : 0);
}

/**
 * The dynamic values a *filter* on a temporal column may be written against.
 *
 * A condition holds a literal, so "due this month or earlier" is a date somebody
 * typed and a view saved in January still filters on January in March. These two
 * words are resolved when the question is asked instead — by
 * `columns/validation.resolve_filter_token` on the server, which owns this table,
 * and by `resolveFilterToken` below for the one filter the browser evaluates itself
 * (a form field's condition).
 *
 * A `datetime` column takes both, "today" meaning this morning's midnight: an ISO
 * date-time sorts lexically and every reading of today is at or after it, so
 * `>= today` is the rest of the day. `date` and `time` each have only the one word
 * that means anything to them.
 */
export const FILTER_TOKENS: Readonly<Record<string, readonly string[]>> = {
  date: ["today"],
  time: ["now"],
  datetime: ["now", "today"],
};

export function filterTokens(type: ColumnType): readonly string[] {
  return FILTER_TOKENS[type] ?? [];
}

/** How a token reads on a control: "today" -> "Today". */
export function filterTokenLabel(token: string): string {
  return token === "today" ? "Today" : "Now";
}

/**
 * A filter value with any token resolved against this browser's clock; anything
 * else passed straight through.
 *
 * The local clock rather than UTC, matching the server: these columns hold a
 * wall-clock reading, and the account's stored zone is a cache of this browser's
 * (see `timezoneSync`), so the two agree about what day it is.
 */
export function resolveFilterToken(type: ColumnType, value: unknown): unknown {
  if (typeof value !== "string" || !filterTokens(type).includes(value)) {
    return value;
  }
  const now = new Date();
  if (type === "date") return fmtDate(now);
  if (type === "time") return fmtTime(now);
  if (value === "today") return `${fmtDate(now)}T00:00:00`;
  return `${fmtDate(now)}T${fmtTime(now)}`;
}

/**
 * A Date as it is written for a person: canonical, seconds dropped unless they say
 * something.
 *
 * The one string both a grid cell and the box you type in show — which is what makes
 * opening an inline editor a no-op on screen rather than an apparent edit. It is
 * deliberately *not* `serializeTemporal`: a `datetime` reads with a space where it
 * stores a `T`, and drops `:00` seconds it always stores. `parseTyped` accepts
 * everything this emits, so the box round-trips against itself.
 */
export function formatTyped(type: ColumnType, d: Date): string {
  const withSeconds = d.getSeconds() !== 0;
  if (type === "date") return fmtDate(d);
  if (type === "time") return fmtTime(d, withSeconds);
  return `${fmtDate(d)} ${fmtTime(d, withSeconds)}`;
}

/** Stored string -> what a grid cell shows. Seconds are dropped unless meaningful. */
export function displayTemporal(type: ColumnType, raw: unknown): string {
  const d = parseTemporal(type, raw);
  if (!d) return String(raw); // unrecognized: show it rather than hide it
  return formatTyped(type, d);
}

// ---------------------------------------------------------------------------
// Typing one in
// ---------------------------------------------------------------------------

/**
 * What the text in a box is, on the way to being a value.
 *
 * The distinction that earns this type is `partial` vs `invalid`. A date is typed a
 * character at a time and passes through strings that are not dates yet — `2026-0`
 * on the way to `2026-07-12` — and a box that turns red at `2026-0` is a box that is
 * red almost all the time. So a *prefix of something that could work* is `partial`,
 * and only a finished-or-impossible reading is `invalid`. It is the same rule
 * `phoneStatus` follows, for the same reason.
 *
 * "Impossible" is checked as early as it can be seen, though: `2026-13` and `25:` are
 * `invalid` on the keystroke that makes them so, because no completion of either is a
 * date or a time and there is nothing to be gained by waiting.
 */
type Reading =
  | { state: "empty" }
  | { state: "partial" }
  | { state: "invalid"; dayFirst?: true }
  | { state: "complete"; date: Date };

/**
 * A field that is *finished* — its value must fall in [lo, hi].
 *
 * `digits` is what makes `2026-7-2` a July date while `14:3` is still being typed:
 * months and days are written either way round here, but a minute is two digits or
 * it is not a minute yet.
 */
function fieldValue(
  f: string,
  lo: number,
  hi: number,
  digits: 1 | 2 = 1,
): number | null {
  if (!new RegExp(`^\\d{${digits},2}$`).test(f)) return null;
  const n = Number(f);
  return n >= lo && n <= hi ? n : null;
}

/** Could `f` still *grow* into a value in [lo, hi]? Two digits never can. */
function fieldPrefix(f: string, lo: number, hi: number): boolean {
  if (f === "") return true;
  if (!/^\d$/.test(f)) return false;
  for (let n = lo; n <= hi; n++) if (pad(n).startsWith(f)) return true;
  return false;
}

/** A trailing field: it may be a finished value or still be growing into one. */
function trailing(
  f: string,
  lo: number,
  hi: number,
  digits: 1 | 2 = 1,
): Reading {
  return fieldValue(f, lo, hi, digits) !== null || fieldPrefix(f, lo, hi)
    ? { state: "partial" }
    : { state: "invalid" };
}

/**
 * Y/M/D -> a Date, or `invalid`.
 *
 * The round-trip check is the whole point: `new Date(2026, 1, 30)` is 2 March, while
 * `date.fromisoformat("2026-02-30")` raises — and this module must never call
 * something valid that the server will refuse.
 *
 * It checks the *date* components only, never the clock. On the day a zone springs
 * forward `new Date(y, m, d, 2, 30)` comes back at 03:30, and verifying the hour
 * would reject a reading the server stores without complaint.
 */
function calendarDate(
  y: number,
  mo: number,
  da: number,
  h = 0,
  mi = 0,
  se = 0,
): Reading {
  if (y < 1) return { state: "invalid" };
  const d = new Date(y, mo - 1, da, h, mi, se);
  d.setFullYear(y); // years under 100: the constructor reads 99 as 1999
  if (d.getFullYear() !== y || d.getMonth() !== mo - 1 || d.getDate() !== da) {
    return { state: "invalid" };
  }
  return { state: "complete", date: d };
}

/** H:M:S -> the 1970-01-01 carrier `parseTemporal` also uses for a bare time. */
function clockTime(h: number, mi: number, se: number): Reading {
  return { state: "complete", date: new Date(1970, 0, 1, h, mi, se) };
}

/**
 * `12/07/2026` — the mistake people actually make, and the one worth naming.
 *
 * Day-first and month-first are the same eleven characters and mean different days,
 * so neither can be accepted; what can be done is say so, because "that is not a
 * date" leaves somebody retyping exactly what they typed the first time.
 */
const DAY_FIRST_RE = /^\d{1,2}[-/]\d{1,2}[-/]\d{4}$/;
const DATE_PARTS_RE = /^(\d{4})-(\d{0,2})(?:-(\d{0,2}))?$/;
const TIME_PARTS_RE = /^(\d{1,2}):(\d{0,2})(?::(\d{0,2}))?$/;

function readDate(text: string): Reading {
  if (text === "") return { state: "empty" };
  if (DAY_FIRST_RE.test(text)) return { state: "invalid", dayFirst: true };
  const t = text.replace(/\//g, "-");
  if (/^\d+$/.test(t)) return readCompactDate(t);

  const m = DATE_PARTS_RE.exec(t);
  if (!m) return { state: "invalid" };
  const [, y, mo, da] = m;
  if (da === undefined) return trailing(mo, 1, 12);
  const month = fieldValue(mo, 1, 12);
  if (month === null) return { state: "invalid" }; // it has a day after it: finished
  const day = fieldValue(da, 1, 31);
  if (day === null) return trailing(da, 1, 31);
  return calendarDate(Number(y), month, day);
}

/** `20260712`, and every prefix of it. */
function readCompactDate(t: string): Reading {
  if (t.length > 8) return { state: "invalid" };
  if (t.length <= 4) return { state: "partial" }; // still in the year
  const [y, mo, da] = [t.slice(0, 4), t.slice(4, 6), t.slice(6, 8)];
  if (t.length <= 6) return trailing(mo, 1, 12);
  const month = fieldValue(mo, 1, 12);
  if (month === null) return { state: "invalid" };
  if (t.length < 8) return trailing(da, 1, 31);
  const day = fieldValue(da, 1, 31, 2);
  if (day === null) return { state: "invalid" };
  return calendarDate(Number(y), month, day);
}

function readTime(text: string): Reading {
  if (text === "") return { state: "empty" };
  if (/^\d+$/.test(text)) return readCompactTime(text);

  const m = TIME_PARTS_RE.exec(text);
  if (!m) return { state: "invalid" };
  const [, hh, mi, se] = m;
  const hour = fieldValue(hh, 0, 23);
  if (hour === null) return { state: "invalid" }; // a colon follows it: finished
  const minute = fieldValue(mi, 0, 59, 2);
  if (se === undefined) {
    if (minute === null) return trailing(mi, 0, 59, 2);
    return clockTime(hour, minute, 0);
  }
  if (minute === null) return { state: "invalid" };
  const second = fieldValue(se, 0, 59, 2);
  if (second === null) return trailing(se, 0, 59, 2);
  return clockTime(hour, minute, second);
}

/**
 * `1430`, `930`, `143000`, and every prefix of them.
 *
 * Three digits are read as `H:MM` first and as two-thirds of `HH:MM` second, so `930`
 * is half past nine while `093` is somebody a keystroke away from `0930`.
 */
function readCompactTime(t: string): Reading {
  if (t.length > 6) return { state: "invalid" };
  if (t.length === 1) return { state: "partial" };
  if (t.length === 2) {
    const asHour = fieldValue(t, 0, 23, 2) !== null;
    return asHour || fieldPrefix(t.slice(1), 0, 59)
      ? { state: "partial" }
      : { state: "invalid" };
  }
  if (t.length === 3) {
    const h = fieldValue(t.slice(0, 1), 0, 23);
    const mm = fieldValue(t.slice(1), 0, 59, 2);
    if (h !== null && mm !== null) return clockTime(h, mm, 0);
    return fieldValue(t.slice(0, 2), 0, 23, 2) !== null &&
      fieldPrefix(t.slice(2), 0, 59)
      ? { state: "partial" }
      : { state: "invalid" };
  }
  const hour = fieldValue(t.slice(0, 2), 0, 23, 2);
  const minute = fieldValue(t.slice(2, 4), 0, 59, 2);
  if (hour === null || minute === null) return { state: "invalid" };
  if (t.length === 4) return clockTime(hour, minute, 0);
  if (t.length === 5) return trailing(t.slice(4), 0, 59, 2);
  const second = fieldValue(t.slice(4), 0, 59, 2);
  if (second === null) return { state: "invalid" };
  return clockTime(hour, minute, second);
}

function readDateTime(text: string): Reading {
  if (text === "") return { state: "empty" };
  const sep = /[Tt ]/.exec(text);
  // A date on its own is midnight, exactly as `datetime.fromisoformat` reads it —
  // and it is what somebody has typed for the moment before they reach the time.
  if (!sep) return readDate(text);

  const date = readDate(text.slice(0, sep.index));
  if (date.state !== "complete") {
    return date.state === "empty" ? { state: "partial" } : date;
  }
  const rest = text.slice(sep.index + 1).trim();
  if (rest === "") return { state: "partial" };
  const time = readTime(rest);
  if (time.state !== "complete") return time;
  return calendarDate(
    date.date.getFullYear(),
    date.date.getMonth() + 1,
    date.date.getDate(),
    time.date.getHours(),
    time.date.getMinutes(),
    time.date.getSeconds(),
  );
}

function readTyped(type: ColumnType, text: string): Reading {
  const t = text.trim();
  if (type === "date") return readDate(t);
  if (type === "time") return readTime(t);
  return readDateTime(t);
}

/**
 * Typed text -> Date, forgivingly. Null for anything not complete and real.
 *
 * Accepts what somebody would plausibly type for an ISO value and no more:
 * `2026-07-12`, `2026-7-2`, `2026/07/12`, `20260712`; `14:30`, `9:05`, `14:30:45`,
 * `1430`, `930`, `143000`; and a date-time as either of those pairs joined by a `T`
 * or a space. Not `12/07/2026`, which is two different days depending on where you
 * live, and not month names or AM/PM, which are a second opinion about what a date
 * is rather than a shorthand for one.
 *
 * Being lenient here cannot produce a write the server refuses, because the typed
 * text is never what is sent: `serializeTemporal` re-emits the Date as canonical
 * ISO, which `fromisoformat` accepts on every Python this runs on. What could go
 * wrong is accepting a *calendar-impossible* value, and `calendarDate` closes that.
 */
export function parseTyped(type: ColumnType, text: string): Date | null {
  const r = readTyped(type, text);
  return r.state === "complete" ? r.date : null;
}

/** The mask a box shows when it is empty. The server's own vocabulary for these
 *  types *is* the mask ("a date (YYYY-MM-DD)"), so the placeholder, the error and
 *  the API documentation all say the same characters. A worked example would say
 *  more, and it gets to: it lives in the hint under a half-typed box. */
export function temporalPlaceholder(type: ColumnType): string {
  if (type === "date") return "YYYY-MM-DD";
  if (type === "time") return "HH:MM";
  return "YYYY-MM-DD HH:MM";
}

/** A bound carrying a timezone designator. `parseTemporal` ignores offsets by
 *  design, so a zoned bound is one this module cannot honestly compare against —
 *  and the server does not guess either: it raises "must use the same timezone
 *  convention as its min/max" (columns/validation.py). */
const ZONED_RE = /(?:[Zz]|[+-]\d{2}:?\d{2})$/;

/**
 * This column's `min`/`max` as Dates — one source for both the status line and the
 * picker's greyed-out days. Null where there is no bound, or none we may judge.
 */
export function temporalBounds(column: Column): {
  min: Date | null;
  max: Date | null;
} {
  const bound = (key: "min" | "max") => {
    const raw = column.config[key];
    if (typeof raw !== "string" || ZONED_RE.test(raw)) return null;
    return parseTemporal(column.type, raw);
  };
  return { min: bound("min"), max: bound("max") };
}

export type TemporalStatusState =
  | "empty"
  | "partial"
  | "invalid"
  | "outOfRange"
  | "valid";

export type TemporalStatus = {
  state: TemporalStatusState;
  /** The one line under the box. Empty only while the box is. */
  message: string;
  /** Set on `valid`: the reading the text resolves to. */
  date?: Date;
};

/** How each type describes itself. `noun` is lifted verbatim from `_TEMPORAL` in
 *  backend/src/tables/columns/validation.py, so the line under the box and the 400
 *  behind it say the same words. */
const SHAPE: Record<string, { noun: string; example: string; thing: string }> = {
  date: {
    noun: "a date (YYYY-MM-DD)",
    example: "2026-07-12",
    thing: "a date",
  },
  time: {
    noun: "a time (HH:MM[:SS])",
    example: "14:30",
    thing: "a time",
  },
  datetime: {
    noun: "a date-time (ISO-8601)",
    example: "2026-07-12 14:30",
    thing: "a date & time",
  },
};

/** The reading spelled out for a person — the weekday above all, which is the one
 *  thing nobody can check by eye and the whole reason this line is worth its height. */
function echo(type: ColumnType, d: Date): string {
  const seconds = d.getSeconds() !== 0;
  if (type === "date") return d.toLocaleDateString(undefined, { dateStyle: "full" });
  if (type === "time") {
    return d.toLocaleTimeString(undefined, {
      timeStyle: seconds ? "medium" : "short",
    });
  }
  return d.toLocaleString(undefined, {
    dateStyle: "full",
    timeStyle: seconds ? "medium" : "short",
  });
}

/**
 * What the box thinks of what is in it.
 *
 * Pure, and here rather than in the component, for the reason `phoneStatus` is:
 * this is the half that has to agree with the server, and it is the half worth
 * testing. The component is left holding wiring.
 *
 * `mode: "query"` skips the bounds and nothing else. A filter value is not a cell
 * value — `validate_scalar` never runs on a condition — so refusing to ask "anything
 * before this column's minimum" would refuse a question the server would answer.
 * Everything else applies unchanged, because a filter holding a near-miss date that
 * silently matches nothing is exactly the failure a picker was there to prevent.
 */
export function temporalStatus(
  column: Column,
  typed: string,
  mode: "value" | "query" = "value",
): TemporalStatus {
  const type = column.type;
  const shape = SHAPE[type];
  const reading = readTyped(type, typed);

  if (reading.state === "empty") return { state: "empty", message: "" };
  if (reading.state === "partial") {
    return {
      state: "partial",
      message: `Keep going — ${shape.thing} here looks like ${shape.example}.`,
    };
  }
  if (reading.state === "invalid") {
    return {
      state: "invalid",
      message: reading.dayFirst
        ? `Dates are written year first: ${SHAPE.date.example}, not 12/07/2026.`
        : `Enter ${shape.noun} — e.g. ${shape.example}.`,
    };
  }

  if (mode === "value") {
    const { min, max } = temporalBounds(column);
    // `<` and `>`, not `<=`/`>=`: a value *on* the bound passes, which is what
    // "on or after" says and what `validate_scalar` does.
    if (min && reading.date < min) {
      return {
        state: "outOfRange",
        message: `Must be on or after ${displayTemporal(type, column.config.min)}.`,
      };
    }
    if (max && reading.date > max) {
      return {
        state: "outOfRange",
        message: `Must be on or before ${displayTemporal(type, column.config.max)}.`,
      };
    }
  }
  return { state: "valid", message: echo(type, reading.date), date: reading.date };
}
