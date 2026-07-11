import type { Column } from "./types";

// Plain-text rendering of a cell value, used where JSX is not wanted (e.g. the
// label of a lookup option). Lookup columns are resolved elsewhere.
export function displayText(column: Column, value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  if (column.type === "checkbox") return value ? "✓" : "✗";
  if (column.type === "choice") {
    const option = column.config.options?.find((o) => o.id === value);
    return option?.label ?? String(value);
  }
  return String(value);
}
