import type { ColumnType } from "./types";

// Date/time columns store a wall-clock string: "2026-07-12", "14:30:00", or
// "2026-07-12T14:30:00". They are calendar values, not instants — a birth date or
// a meeting time does not shift because you flew to another timezone. So parse and
// format the components in local time. Going through Date#toISOString() would
// convert via UTC and land on the previous day for anyone west of Greenwich.

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

/** Stored string -> what a grid cell shows. Seconds are dropped unless meaningful. */
export function displayTemporal(type: ColumnType, raw: unknown): string {
  const d = parseTemporal(type, raw);
  if (!d) return String(raw); // unrecognized: show it rather than hide it
  const withSeconds = d.getSeconds() !== 0;
  if (type === "date") return fmtDate(d);
  if (type === "time") return fmtTime(d, withSeconds);
  return `${fmtDate(d)} ${fmtTime(d, withSeconds)}`;
}
