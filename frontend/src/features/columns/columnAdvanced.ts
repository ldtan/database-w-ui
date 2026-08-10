/**
 * Which of a column's settings are folded away, and when the fold has to open itself.
 *
 * The column dialog offers about twenty controls at equal weight, which is why nothing
 * in it can be skimmed. Most of them are refinements — a maximum length, an integer-only
 * number, a default — and most columns use none of them. Those go behind a disclosure.
 *
 * Two rules decide what may be folded, and this file is where the second one lives:
 *
 * 1. **Nothing the Save button validates.** A lookup with no target list cannot be
 *    saved; folding the control that sets it turns an incomplete form into a broken
 *    one. So a lookup's target, a calculated column's formula, a choice column's
 *    options and the text input mode stay on screen, along with `required`, which is
 *    the one piece of validation nearly every column has an opinion about.
 * 2. **A fold must open itself when it holds something.** A setting that is set and
 *    invisible is worse than one that is merely hard to find — you would have to open
 *    every section of every column to know what a list actually does.
 *
 * `hasAdvancedConfig` is the second rule, kept as a pure function because it is exactly
 * the sort of predicate that rots: a config key added to the dialog and not added here
 * fails silently, and only in the one case that matters — editing a column that uses it.
 */
import type { ColumnConfig } from "../../lib/types";

/**
 * Config keys the advanced section owns, and the value that counts as unset for each.
 *
 * Kept as data rather than as a chain of `||` so that adding a control means adding a
 * line here, in a file whose test names every key.
 */
const ADVANCED_KEYS: Array<keyof ColumnConfig | string> = [
  "unique",
  "max_length",
  "min",
  "max",
  "integer_only",
  "allow_other",
  // A link column's display text.
  "label",
  "multiple",
  // A nested lookup, which is set as a pair.
  "parent_column_uuid",
  "target_filter_column_uuid",
  // A lookup's saved option filter. Stored as `undefined` when cleared rather
  // than an empty group, since `isSet` counts any object as a choice made.
  "option_filters",
  "default",
  "onupdate",
];

/** Is this value one somebody chose, as opposed to the absence of a choice? */
function isSet(value: unknown): boolean {
  // `false` and `""` are what these keys hold when they are *off*, and `0` is a real
  // minimum somebody typed — so falsiness alone would both open the section for every
  // column and hide a legitimate `min: 0`.
  if (value === undefined || value === null || value === "" || value === false) {
    return false;
  }
  return !(Array.isArray(value) && value.length === 0);
}

/** Does this column use any setting that lives behind the Advanced disclosure? */
export function hasAdvancedConfig(config: ColumnConfig | null | undefined): boolean {
  if (!config) return false;
  return ADVANCED_KEYS.some((key) => isSet(config[key as string]));
}

export const ADVANCED_CONFIG_KEYS = ADVANCED_KEYS;
