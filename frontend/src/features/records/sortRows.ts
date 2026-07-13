import type { ListItem } from "../../lib/types";

/**
 * The one order records are shown in.
 *
 * The grid and the card list are two layouts of the same records, so they must not
 * disagree about what "sorted by Name" means — and they did: PrimeReact's own
 * comparator flips blanks to the top on a descending sort and orders "item10"
 * before "item9", where this one does neither. Whichever screen you are on, the
 * records now come out the same way: the grid drives this through each column's
 * `sortFunction`, the cards by calling `sortRows` directly.
 */
export function compareValues(a: unknown, b: unknown, order: 1 | -1): number {
  const aBlank = a === null || a === undefined || a === "";
  const bBlank = b === null || b === undefined || b === "";
  // Blanks sink to the bottom either way — the direction is applied *after* this,
  // so reversing the sort must not float the empty records to the top.
  if (aBlank || bBlank) return aBlank && bBlank ? 0 : aBlank ? 1 : -1;
  const base =
    typeof a === "number" && typeof b === "number"
      ? a - b
      : String(a).localeCompare(String(b), undefined, { numeric: true });
  return base * order;
}

/**
 * Order records by a column. `field` is the grid's `data.<column uuid>` path — the
 * shape PrimeReact hands to a `sortFunction`, and what the sort dropdown stores.
 *
 * Reference cells hold uuids, so they sort by uuid rather than by the label shown.
 * That is what the grid has always done; the fix for it is to sort server-side by
 * the resolved label, not to have the two layouts differ.
 */
export function sortRows(
  rows: ListItem[],
  field: string | undefined,
  order: number | null | undefined,
): ListItem[] {
  if (!field || !order) return rows;
  const key = field.replace(/^data\./, "");
  const direction = order === -1 ? -1 : 1;
  return [...rows].sort((a, b) =>
    compareValues(a.data?.[key], b.data?.[key], direction),
  );
}
