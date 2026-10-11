/** Rating cells: an integer, and the scale it is a point on.
 *
 *  Mirrors `rating_scale` / `RATING_MAX` in
 *  backend/src/tables/columns/validation.py. The scale lives in the column's shared
 *  `max` key — the top of a rating is a bound like any other, so it needs no key of
 *  its own — and the bottom is always 1, because a rating of zero is what an *empty*
 *  cell means and a configurable floor would give the column two ways to say
 *  "unrated".
 */
import type { Column } from "./types";

/** The largest scale the server will accept. Ten glyphs is already past the point
 *  where somebody counts rather than sees. */
export const RATING_MAX = 10;
const DEFAULT_SCALE = 5;

/** The top of this column's scale, defaulting to five. */
export function ratingScale(column: Column): number {
  const top = column.config.max;
  if (typeof top !== "number" || !Number.isInteger(top)) return DEFAULT_SCALE;
  if (top < 1 || top > RATING_MAX) return DEFAULT_SCALE;
  return top;
}

/** A cell as a whole rating on this column's scale, or null for unrated.
 *
 *  Clamped rather than refused, because this reads what is *already stored*: a cell
 *  written when the scale went to ten, on a column since narrowed to five, is a 7
 *  that has to render as something. The server refuses the next write of it. */
export function ratingValue(column: Column, value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  const rounded = Math.round(value);
  if (rounded < 1) return null;
  return Math.min(rounded, ratingScale(column));
}
