/**
 * The filter language's operators, and what counts as a usable condition.
 *
 * Apart from `FilterConditionsEditor` because this is data and a rule, not a control,
 * and because two places now need it without needing the editor: the export dialog
 * builds the same `filters` object, and a form field's condition is the same language
 * asked of one draft (`features/forms/conditions.ts`). A test can import a `.ts`; the
 * suite runs in node and deliberately keeps PrimeReact out of it (see vite.config.ts).
 *
 * The table is the contract `backend/src/tables/records/filtering.py::_condition` answers.
 * Adding one here without adding it there gets a filter that silently matches
 * everything, so the two are meant to be edited together.
 */
import type { FilterCondition } from "../../lib/types";

export const OPERATORS = [
  { label: "is", value: "is" },
  { label: "is not", value: "is_not" },
  { label: "contains", value: "contains" },
  { label: "does not contain", value: "does_not_contain" },
  { label: "is empty", value: "is_empty" },
  { label: "is not empty", value: "is_not_empty" },
  { label: ">", value: ">" },
  { label: "≥", value: ">=" },
  { label: "<", value: "<" },
  { label: "≤", value: "<=" },
];

export const NO_VALUE_OPS = new Set(["is_empty", "is_not_empty"]);

// Drop conditions that are incomplete so they don't get sent to the server.
export function cleanConditions(conditions: FilterCondition[]): FilterCondition[] {
  return conditions.filter(
    (c) =>
      c.column_uuid &&
      c.operator &&
      (NO_VALUE_OPS.has(c.operator) ||
        (c.value !== undefined && c.value !== null && c.value !== "")),
  );
}
