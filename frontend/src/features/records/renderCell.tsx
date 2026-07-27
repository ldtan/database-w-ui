import { linkLabel, safeHref } from "../../lib/links";
import { displayTemporal, isTemporal } from "../../lib/temporal";
import type { Column, ListItem } from "../../lib/types";
import { LookupCell } from "../lookups/LookupControls";
import { PrincipalCell, type PrincipalType } from "../principals/PrincipalControls";

/**
 * One record's value for one column, as the UI shows it.
 *
 * Shared by the grid and the card list — the two are different layouts of the same
 * data, and a value that renders one way in a table and another way in a card is a
 * bug waiting to happen (it already would have been: link and temporal
 * columns each have rules that live only here).
 */
export function renderCell(
  column: Column,
  row: ListItem,
  listUuid: string,
  principalUuids: Record<PrincipalType, string[]>,
  lookupUuids: Map<string, string[]>,
) {
  const value = row.data?.[column.uuid];
  // Reference cells are handed the uuids the *whole page* holds, and resolve them
  // themselves. They share one query key per column, so it is still one request —
  // but each cell subscribes to it, which a map passed down as a prop cannot do:
  // DataTable re-renders only when its `value` changes, so the labels would land
  // after an edit and never reach the DOM.
  if (column.type === "lookup") {
    return (
      <LookupCell
        listUuid={listUuid}
        column={column}
        value={value}
        pageUuids={lookupUuids.get(column.uuid) ?? []}
      />
    );
  }
  if (column.type === "user" || column.type === "group") {
    return (
      <PrincipalCell
        column={column}
        value={value}
        pageUuids={principalUuids[column.type]}
      />
    );
  }
  if (value === null || value === undefined || value === "") {
    return <span className="text-faint">—</span>;
  }
  if (column.type === "checkbox") {
    return value ? "✓" : "✗";
  }
  if (column.type === "link") {
    const href = safeHref(column, value);
    // A value the scheme check rejects is shown as plain text, never an anchor.
    if (!href) return String(value);
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="hover:underline"
        style={{ color: "var(--brand)" }} // themed per palette; no Tailwind token for it
        onClick={(e) => e.stopPropagation()} // don't also toggle row selection
      >
        {linkLabel(column, href)}
      </a>
    );
  }
  // Otherwise a datetime renders as its raw stored string, seconds and all.
  if (isTemporal(column.type)) {
    return displayTemporal(column.type, value);
  }
  return String(value);
}
