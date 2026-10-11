/** Duration cells: seconds in the database, something readable on screen.
 *
 *  A cell holds **seconds**, and nothing on the server ever formats one — the same
 *  arrangement `number`'s `precision` and `number_format` already have. So the
 *  column's `duration_format` is read here and nowhere else, and an exported CSV
 *  carries the raw seconds so it round-trips exactly.
 *
 *  `parseDuration` is the mirror of `parse_duration` in
 *  backend/src/tables/columns/validation.py, which the CSV import runs. The two are
 *  meant to be edited together: a form this one accepts and that one refuses is a
 *  value somebody can type into the grid but not into a file.
 */
import type { Column } from "./types";

export type DurationFormat = "h:mm" | "h:mm:ss" | "m:ss" | "decimal_hours";

const FORMATS: DurationFormat[] = ["h:mm", "h:mm:ss", "m:ss", "decimal_hours"];
const SECONDS_RE = /^\d+(\.\d+)?$/;
const UNIT_FORM_RE = /^(\d+(\.\d+)?\s*[hms]\s*)+$/;
const UNIT_RE = /(\d+(?:\.\d+)?)\s*([hms])/g;
const UNIT_SECONDS: Record<string, number> = { h: 3600, m: 60, s: 1 };

/** How this column's durations are written. Unset means `h:mm`. */
export function durationFormat(column: Column): DurationFormat {
  const format = column.config.duration_format;
  return FORMATS.includes(format as DurationFormat)
    ? (format as DurationFormat)
    : "h:mm";
}

/** Read a written duration as seconds, or null if it is not one.
 *
 *  A bare number is **always** seconds, whatever the column is formatted as —
 *  that is what an exported file holds, so any other reading would break the round
 *  trip. `1:30` is h:mm unless the column is formatted `m:ss`; three colon-parts
 *  are always h:mm:ss; and the unit form (`90m`, `1.5h`, `1h30m`) is unambiguous
 *  in any column.
 *
 *  **`decimal_hours` does not round-trip through this function, and must not try
 *  to.** Its displayed form *is* a bare number ("1.50"), which this reads as one
 *  and a half seconds. Rather than carve an exception into the one rule that keeps
 *  imports honest, the editor for that format is a number box in hours that
 *  multiplies out itself (`RecordFields`) — so nothing types a decimal-hours value
 *  into a parser that would mean something else by it. */
export function parseDuration(
  format: DurationFormat,
  text: string,
): number | null {
  const raw = text.trim().toLowerCase();
  if (!raw) return null;
  if (SECONDS_RE.test(raw)) return Number(raw);

  if (raw.includes(":")) {
    const parts = raw.split(":");
    if (parts.length > 3 || !parts.every((p) => SECONDS_RE.test(p))) return null;
    const values = parts.map(Number);
    // "1:75" is a typo, not a silent 2:15.
    if (values.slice(1).some((v) => v >= 60)) return null;
    let hours = 0;
    let minutes = 0;
    let seconds = 0;
    if (values.length === 3) [hours, minutes, seconds] = values;
    else if (format === "m:ss") [minutes, seconds] = values;
    else [hours, minutes] = values;
    return hours * 3600 + minutes * 60 + seconds;
  }

  if (UNIT_FORM_RE.test(raw)) {
    let total = 0;
    for (const [, amount, unit] of raw.matchAll(UNIT_RE)) {
      total += Number(amount) * UNIT_SECONDS[unit];
    }
    return total;
  }
  return null;
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

/** Seconds as this column writes them. Empty string for an empty cell.
 *
 *  Rounded to whole seconds first, so a stored 89.7 does not render as `1:29.7`
 *  in one format and `1:30` in another. `decimal_hours` keeps two places, which
 *  is what a timesheet wants and what `h:mm` cannot express. */
export function formatDuration(
  format: DurationFormat,
  value: unknown,
): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    return typeof value === "string" ? value : "";
  }
  if (format === "decimal_hours") return (value / 3600).toFixed(2);

  const total = Math.round(value);
  const seconds = total % 60;
  const minutes = Math.floor(total / 60) % 60;
  const hours = Math.floor(total / 3600);
  if (format === "m:ss") return `${Math.floor(total / 60)}:${pad(seconds)}`;
  if (format === "h:mm:ss") return `${hours}:${pad(minutes)}:${pad(seconds)}`;
  // h:mm — the default, and the one that can lose the seconds it is not showing.
  return `${hours}:${pad(minutes)}`;
}

/** The placeholder for a box that takes this format, so the hint matches the
 *  thing being asked for rather than being one example for four formats. */
export function durationPlaceholder(format: DurationFormat): string {
  if (format === "decimal_hours") return "1.5";
  if (format === "m:ss") return "1:30";
  if (format === "h:mm:ss") return "1:30:00";
  return "1:30";
}
