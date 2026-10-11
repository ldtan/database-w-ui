/**
 * The row you are typing a *new* record into, before it is a record.
 *
 * "Edit cells" turns the grid's values into editable cells; this is the row at the
 * foot of it that has no record behind it yet. It is a `TableRecord` like any other
 * so that `renderCell` and `InlineCellEditor` need no branch for it — the draft's
 * values are held in **stored** form, exactly the shape a record's `data` has, and
 * the only thing that marks the row is its uuid.
 *
 * Nothing is sent per cell, because there is nothing to PATCH: a committed cell
 * merges into the draft, and the whole draft goes as one `POST /records/` when the
 * row's ✓ is pressed. Which columns it may write is the *create* form's answer
 * (`resolveFields(cols, createForm)`), not the update form's — creating and
 * editing a record are allowed to differ, and the table already says so.
 *
 * These live apart from `TableDetailPage` for the reason `cellEdit.ts` does: they
 * are rules, they import no React, and a test should not need a DOM to check them.
 */
import type { TableRecord } from "../../lib/types";

/**
 * The uuid a draft row carries.
 *
 * Deliberately not uuid-shaped, so it can never collide with a record's — the grid
 * keys rows on `uuid` (`dataKey="uuid"`), and a sentinel that could also be a real
 * record is a sentinel that will one day address the wrong row. It is also why
 * nothing derived from the draft may reach the server: `useEffectiveActionsMap`
 * resolves per-record permissions from a table of uuids, and this one names nothing.
 */
export const NEW_ROW_UUID = "new-record-draft";

export function isNewRow(uuid: string): boolean {
  return uuid === NEW_ROW_UUID;
}

/**
 * A draft's values, wrapped as the row the grid renders.
 *
 * `permissions_inherit` is true because that is what the server defaults a new
 * record to, and the Share dialog is unreachable from a row that does not exist —
 * so the value is never read, only required by the type.
 */
export function newRowItem(data: Record<string, unknown>): TableRecord {
  return { uuid: NEW_ROW_UUID, data, permissions_inherit: true };
}

/**
 * Did the record just added land on the page in front of you?
 *
 * Filtering, sorting and paging are all server-side, so this cannot be worked out
 * from the record — only observed, by looking at the page the refetch brought
 * back. A `false` here is the normal outcome rather than a failure: a record added
 * under an ascending sort belongs at the end of the table, and its *not* being on
 * page 1 is the view being obeyed. It is worth saying out loud precisely because a
 * row that vanishes without explanation reads as a bug.
 *
 * Takes the page rather than the query client so it stays a function of its
 * arguments; the caller reads the cache.
 */
export function landedOnPage(
  rows: TableRecord[] | undefined,
  uuid: string,
): boolean {
  return (rows ?? []).some((row) => row.uuid === uuid);
}
