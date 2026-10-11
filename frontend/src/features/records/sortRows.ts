/**
 * The comparator, and no longer the sort.
 *
 * This file existed because the grid and the card table are two layouts of the same
 * records and disagreed about what "sorted by Name" meant: PrimeReact's own comparator
 * flips blanks to the top on a descending sort and orders "item10" before "item9", where
 * this one does neither. Records are not ordered here any more — the grid is `lazy` and
 * both layouts render the page the server already sorted, which is also the only way a
 * reference column can order by its label instead of the uuid in the cell
 * (`items/references.py` on the server).
 *
 * What is left is the comparator itself, still worth having for the small in-memory
 * tables the UI does order — `ColumnsPanel` sorting columns by display name. The file
 * keeps its name so the import path stays put.
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
