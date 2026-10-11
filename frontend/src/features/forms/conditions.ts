/**
 * Whether a form field applies to the record being filled in.
 *
 * A field's condition is stored as a view's `FilterGroup` and means the same thing —
 * "does this record match?" — asked of one draft rather than of a table. Which makes
 * this file the client-side twin of `backend/src/tables/records/filtering.py`, and that
 * is the whole risk in it: **one operator vocabulary, two implementations**, one in
 * SQL over stored JSONB and one here over a half-typed draft.
 *
 * `filtering.py::_condition` is the specification, and the rules below are copied from
 * it deliberately rather than reinvented:
 *
 * - Comparisons are **text** comparisons (`_as_text`), because `data ->> key` is text.
 *   The draft holds live values — a `Date`, a number, an array — so `toStored` runs
 *   first and the comparison happens on exactly what the server would have stored.
 * - A cell holding an array yields **its first element**, unless the column declares
 *   `multiple` (`_field`, and the reason that function exists: records outlive the
 *   `multiple` setting that shaped them).
 * - `contains` is case-insensitive, because `ilike` is.
 * - A temporal condition written against **"today" or "now"** resolves against the
 *   clock at the moment it is asked, not against the moment it was saved
 *   (`resolve_filter_token`). Here that is the browser's clock and there it is the
 *   account's recorded zone, which is a cache of this browser's — so the two agree
 *   about what day it is.
 * - `>`/`<` compare **numbers** on a `NUMERIC_TYPES` column — `number`, `rating` and
 *   `duration`, all three of which store a plain JSON number — and text everywhere
 *   else. Text ordering on numbers puts "9" after "10", which is the wrong answer
 *   `_numeric` was written to avoid.
 * - A condition naming a column that no longer exists, or using an operator this build
 *   does not know, is **dropped** — `_condition` returns `None` and `apply_filters`
 *   leaves it out of the WHERE. So an unreadable condition shows the field rather than
 *   hiding it: failing closed would take fields off the screen with nothing to explain
 *   why, and a form field is an affordance, not a boundary.
 *
 * Nothing here is enforcement. The server never reads a form's config when it writes a
 * record, so a hidden field's column is still required if it says it is, and CSV
 * import, a QR fill and a direct API write see no conditions at all.
 */
import { resolveFilterToken } from "../../lib/temporal";
import type {
  Column,
  FieldCondition,
  FilterCondition,
  Form,
} from "../../lib/types";
import { toStored, type ResolvedField } from "../records/recordValues";

function asText(value: unknown): string | null {
  return value === null || value === undefined ? null : String(value);
}

/**
 * The cell as a condition should see it — `_field`'s rule, in TypeScript.
 *
 * A `multiple` column compares as the serialized array, which is what `->>` yields for
 * one. Exact equality against that text is not something any control can produce (nor
 * in the view filter editor it is shared with); `contains`, which is what people
 * actually reach for on a multi-valued column, matches an element either way.
 */
function fieldText(column: Column, raw: unknown): string | null {
  const stored = toStored(column, raw);
  if (stored === null || stored === undefined) return null;
  if (column.config.multiple) {
    return Array.isArray(stored) ? JSON.stringify(stored) : asText(stored);
  }
  if (Array.isArray(stored)) {
    return stored.length > 0 ? asText(stored[0]) : null;
  }
  return asText(stored);
}

/**
 * Should `>`/`<` on this column compare numbers? `_numeric_comparable`'s rule.
 *
 * The set is `NUMERIC_TYPES`, not `"number"` alone: a `rating` is a point on a scale
 * and a `duration` is a count of seconds, but a cell in either holds a plain JSON
 * number and the backend compares all three numerically. Leaving them out compared
 * them as text, which is the "9" > "10" answer `_numeric` exists to avoid — so
 * `Hours > 600` on a duration column read `"3600" > "600"` as false and quietly kept
 * a field hidden on exactly the records that should have shown it. Copied from
 * `columns/enums.py::NUMERIC_TYPES`; the two are meant to stay in step.
 */
const NUMERIC_TYPES = new Set(["number", "rating", "duration"]);

function numericComparable(column: Column, value: unknown): boolean {
  if (NUMERIC_TYPES.has(column.type)) return true;
  // A calculated column has no fixed output type — one formula yields numbers and the
  // next yields text — so the value being compared against is the only signal for
  // which comparison was meant.
  return (
    column.type === "calculated" &&
    typeof value === "number" &&
    Number.isFinite(value)
  );
}

function asNumber(text: string | null): number | null {
  if (text === null || text.trim() === "") return null;
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * One condition against one draft — or **null when it does not apply at all**.
 *
 * Null is not "false". It is `_condition` returning `None`: the clause never reaches
 * the WHERE, so it neither includes nor excludes anything, and the caller drops it.
 */
export function evaluateCondition(
  condition: FilterCondition,
  values: Record<string, unknown>,
  columns: Column[],
): boolean | null {
  const column = columns.find((c) => c.uuid === condition.column_uuid);
  if (!column) return null;

  const field = fieldText(column, values[column.uuid]);
  const { operator } = condition;
  // "today"/"now" on a temporal column stand for a reading of the clock, not for
  // themselves — `filtering._condition` resolves them where it builds the WHERE and
  // this is the same substitution, in the same place, against the browser's clock.
  // Left alone for every other column and value, so a `text` cell really holding the
  // word "today" still compares as that word.
  const value = resolveFilterToken(column.type, condition.value);
  const wanted = asText(value);

  switch (operator) {
    // Every operator below that takes a value answers **false** when either side is
    // absent. In SQL both `NULL = 'x'` and `field = NULL` evaluate to NULL, and a NULL
    // clause keeps the row out of the result — so an empty cell satisfies neither
    // "is" nor "is not", which is worth stating because "is not" reads as though it
    // should. `is_empty` is how you ask about an empty cell, there and here.
    case "is":
    case "eq":
    case "=":
      return field !== null && wanted !== null && field === wanted;
    case "is_not":
    case "neq":
    case "!=":
      return field !== null && wanted !== null && field !== wanted;
    case "contains":
      return field !== null && wanted !== null
        ? field.toLowerCase().includes(wanted.toLowerCase())
        : false;
    case "does_not_contain":
      return field !== null && wanted !== null
        ? !field.toLowerCase().includes(wanted.toLowerCase())
        : false;
    case "is_empty":
      return field === null || field === "";
    case "is_not_empty":
      return field !== null && field !== "";
    case ">":
    case ">=":
    case "<":
    case "<=":
    case "gt":
    case "gte":
    case "lt":
    case "lte": {
      if (numericComparable(column, value)) {
        const left = asNumber(field);
        const right =
          typeof value === "number" ? value : asNumber(asText(value));
        if (left === null || right === null) return false; // NULL excludes the row
        return compare(operator, left, right);
      }
      if (field === null || wanted === null) return false;
      return compare(operator, field, wanted);
    }
    default:
      // An operator this build does not know. `_condition` falls off its own end and
      // returns None for exactly this case; matching it means a config written by a
      // newer client degrades to "no opinion" rather than to "hide everything".
      return null;
  }
}

function compare<T extends number | string>(
  operator: string,
  left: T,
  right: T,
): boolean {
  switch (operator) {
    case ">":
    case "gt":
      return left > right;
    case ">=":
    case "gte":
      return left >= right;
    case "<":
    case "lt":
      return left < right;
    default:
      return left <= right;
  }
}

/**
 * Does the field this condition governs apply?
 *
 * An empty or absent condition is **true** — the field applies — matching
 * `apply_filters`, which filters nothing when handed no conditions. A block left
 * half-built in the editor is therefore inert rather than hiding the field it was
 * meant to reveal.
 */
export function conditionMet(
  condition: FieldCondition | null | undefined,
  values: Record<string, unknown>,
  columns: Column[],
): boolean {
  const conditions = condition?.when?.conditions ?? [];
  const answers = conditions
    .map((cond) => evaluateCondition(cond, values, columns))
    .filter((answer): answer is boolean => answer !== null);
  if (answers.length === 0) return true;
  return condition?.when?.conjunction === "or"
    ? answers.some(Boolean)
    : answers.every(Boolean);
}

/**
 * `resolveFields`' answer, narrowed by each field's condition.
 *
 * A **sibling of `resolveFields`, not an option on it**. That function is pure, has no
 * values to evaluate against, and is called by four other surfaces — the grid's
 * `cellEditKinds`, the new-row footer, the QR fill and the read-only record dialog —
 * none of which conditions apply to. Adding a `values` argument there would leak this
 * into all of them; a separate call makes each surface opt in.
 *
 * The return type is unchanged, which is what keeps the rest of the form ignorant of
 * conditions: `hide` drops the entry and `disable` clears `enabled`, and
 * `buildRecordData` already skips both. So a field that does not apply is never sent,
 * and because `PATCH /records/{uuid}` merges rather than replaces, whatever the record
 * already held in that cell survives untouched.
 */
export function applyFieldConditions(
  fields: ResolvedField[],
  form: Form | null,
  values: Record<string, unknown>,
  columns: Column[],
): ResolvedField[] {
  const byUuid = new Map(
    (form?.config.fields ?? []).map((f) => [
      f.column_uuid,
      f.condition ?? null,
    ]),
  );
  const applied: ResolvedField[] = [];
  for (const field of fields) {
    const condition = byUuid.get(field.column.uuid) ?? null;
    if (!condition || conditionMet(condition, values, columns)) {
      applied.push(field);
      continue;
    }
    if (condition.effect === "disable") {
      applied.push({ ...field, enabled: false });
    }
  }
  return applied;
}
