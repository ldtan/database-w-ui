import type { ColumnType } from "./types";

// Types offered in the "add column" UI.
export const COLUMN_TYPE_OPTIONS: { label: string; value: ColumnType }[] = [
  // One entry, not two: whether text is one line or several is `config.text_mode`,
  // set in the column dialog, so it is not a separate type to choose between.
  { label: "Text", value: "text" },
  { label: "Number", value: "number" },
  { label: "Checkbox", value: "checkbox" },
  { label: "Choice", value: "choice" },
  { label: "Date", value: "date" },
  { label: "Time", value: "time" },
  { label: "Date & time", value: "datetime" },
  { label: "Link (URL)", value: "link" },
  { label: "User", value: "user" },
  { label: "Group", value: "group" },
  { label: "Lookup (link to another list)", value: "lookup" },
  { label: "Calculated (formula)", value: "calculated" },
];

/** Whether a column of `from` can be converted straight to `to`.
 *
 *  Mirrors `is_supported` in `columns/conversion.py`, and has to stay in step with
 *  it — the server refuses anything this lets through, so a mismatch shows up as a
 *  400 rather than a disabled option. Kept here rather than fetched because it is a
 *  property of the type system, not of any particular column.
 *
 *  Text is the hub: everything converts to text and text converts to everything, so
 *  a pair with text on neither side is two conversions rather than one. `date` and
 *  `datetime` convert directly so widening and truncating skip the round trip.
 *  `calculated` is out in both directions — not for want of a value to convert
 *  (it has one now), but because gaining or losing a formula is a decision about
 *  the column rather than a cell conversion. */
export function canConvert(from: ColumnType, to: ColumnType): boolean {
  if (from === "calculated" || to === "calculated") return false;
  if (from === to) return false; // nothing to do, rather than unsupported
  if (from === "text" || to === "text") return true;
  return (
    (from === "date" && to === "datetime") ||
    (from === "datetime" && to === "date")
  );
}

/** The types this column could become, and why the others are unavailable. */
export function conversionTargets(from: ColumnType) {
  return COLUMN_TYPE_OPTIONS.filter((o) => o.value !== from).map((o) => ({
    ...o,
    disabled: !canConvert(from, o.value),
  }));
}

/** The types a *copy* of this column could have.
 *
 *  Same list as a conversion's, plus the source's own type — which `canConvert`
 *  excludes as a no-op and which is the commonest thing to want from a duplicate:
 *  the same column again. The backend's `is_supported` already treats a same-type
 *  pair as a straight copy. */
export function duplicateTargets(from: ColumnType) {
  return COLUMN_TYPE_OPTIONS.map((o) => ({
    ...o,
    disabled: o.value !== from && !canConvert(from, o.value),
  }));
}
