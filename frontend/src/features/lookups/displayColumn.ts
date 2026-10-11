/**
 * Which column of a lookup's target table carries a row's label.
 *
 * A hand-kept mirror of the server's `display_column_key`
 * (`backend/src/tables/records/services.py`), which is the authority: the write
 * path, the export formatter and the options endpoint all call it, so that the
 * three cannot disagree about what a label *is*. Nothing here feeds any of them.
 * This copy exists for one job — deciding which field of the "add a row" dialog
 * receives the name somebody just typed — and the cost of it being wrong is a
 * label landing in the wrong box, which the person can see and fix, not a
 * mislabelled row.
 *
 * The fallback chain is load-bearing all the same, and is the reason this is a
 * function rather than a property read: `primary_column` is only ever set by an
 * explicit PATCH and is nulled again when that column is deleted, so most tables
 * have none at all. Stopping at it would leave the dialog unable to prefill for
 * the ordinary case.
 *
 * @see ../references/ReferencePicker for where the typed text comes from.
 */
import type { Column } from "../../lib/types";

/** A table's primary column, as `TableRead` publishes it — a link, not an id. */
export interface PrimaryColumnLink {
  uuid: string;
}

/**
 * The uuid of the column to show (and to prefill), or null when the target has
 * nothing that could serve as a label.
 *
 * `columns` must be the target table's columns **as the API returned them**:
 * `ColumnService.table` orders by `(position, id)` and filters to the table's
 * shape, so the first element is already the server's "first column" and
 * re-sorting here could only introduce a disagreement. A target whose schema is
 * refused — a table shared as a lookup target only — yields an empty array and
 * therefore null, which is correct: that caller cannot create rows there either.
 */
export function resolveDisplayColumn(
  displayColumnUuid: string | null | undefined,
  primaryColumn: PrimaryColumnLink | null | undefined,
  columns: Column[],
): string | null {
  // Taken at face value, exactly as the server takes it. It is not checked
  // against `columns`: the server does not check it either, and a lookup
  // pointing at a since-deleted display column is a broken column to be fixed
  // in its own dialog, not something to paper over by silently labelling rows
  // off a different field.
  if (displayColumnUuid) return displayColumnUuid;

  // Only when it is still part of the shape. A deleted primary column nulls the
  // table's pointer server-side, but a cached table payload can outlive that.
  if (primaryColumn && columns.some((c) => c.uuid === primaryColumn.uuid)) {
    return primaryColumn.uuid;
  }

  return columns[0]?.uuid ?? null;
}
