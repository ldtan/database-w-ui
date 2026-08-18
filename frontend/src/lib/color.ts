/** Colour cells: `#rrggbb`, and what to draw the swatch on.
 *
 *  Mirrors `normalize_color` / `_COLOR_RE` in
 *  backend/src/lists/columns/validation.py. The server decides what is stored;
 *  this renders what is already in a cell, including values that predate the
 *  column being a `color` column (a conversion from `text` normalises them, but a
 *  cell can still be read while an older value sits in it).
 */
import type { Column } from "./types";

const COLOR_RE = /^#[0-9a-f]{6}$/;
const SHORTHAND_RE = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/;

/** The stored form: trimmed, lowercased, `#abc` expanded to `#aabbcc`.
 *
 *  Expanding rather than accepting both is the point: two spellings of one colour
 *  would be two values to `unique`, to a filter and to grouping. */
export function normalizeColor(value: string): string {
  const text = value.trim().toLowerCase();
  const short = SHORTHAND_RE.exec(text);
  if (!short) return text;
  const [, r, g, b] = short;
  return `#${r}${r}${g}${g}${b}${b}`;
}

export function isColor(value: unknown): value is string {
  return typeof value === "string" && COLOR_RE.test(normalizeColor(value));
}

/** The palette a colour column offers first, or an empty list. Offered, never
 *  enforced — the server accepts any valid colour whatever the swatches say,
 *  because the point of a colour field is picking one. */
export function colorSwatches(column: Column): string[] {
  const swatches = column.config.swatches;
  if (!Array.isArray(swatches)) return [];
  return swatches.filter(isColor).map(normalizeColor);
}

/** Black or white, whichever stays readable on `color`.
 *
 *  Relative luminance per WCAG, so a swatch can carry its own hex as a label
 *  without going invisible on a light or a dark fill. */
export function readableTextOn(color: string): string {
  const hex = normalizeColor(color);
  if (!COLOR_RE.test(hex)) return "#000000";
  const channel = (i: number) => {
    const c = parseInt(hex.slice(1 + i * 2, 3 + i * 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  const luminance =
    0.2126 * channel(0) + 0.7152 * channel(1) + 0.0722 * channel(2);
  return luminance > 0.179 ? "#000000" : "#ffffff";
}
