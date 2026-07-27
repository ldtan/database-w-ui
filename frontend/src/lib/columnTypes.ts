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
