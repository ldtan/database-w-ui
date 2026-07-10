import type { ColumnType } from "./types";

// Types offered in the "add column" UI. `lookup` is omitted for now — it needs
// a target-list/row picker, which is a later addition.
export const COLUMN_TYPE_OPTIONS: { label: string; value: ColumnType }[] = [
  { label: "Single line text", value: "text" },
  { label: "Long text", value: "long_text" },
  { label: "Number", value: "number" },
  { label: "Checkbox", value: "checkbox" },
  { label: "Choice", value: "choice" },
  { label: "Date", value: "date" },
  { label: "Time", value: "time" },
  { label: "Date & time", value: "datetime" },
];
