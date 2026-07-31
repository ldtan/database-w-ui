/**
 * The rules a grid cell's editor follows, with nothing in them that renders.
 *
 * Split from `inlineEdit.tsx` so that a test can import them: that file holds
 * `InlineCellEditor`, which reaches PrimeReact through `RecordField`, and a rule
 * about what counts as a changed value should not need a DOM to check.
 */
import type { Column } from "../../lib/types";
import { clearedNested, needsFullEditor, toStored } from "./recordValues";
import type { ResolvedField } from "./recordValues";

/**
 * What a column's cells do while the grid is in edit mode.
 *
 * "dialog" is the honest answer for a rich-text or multiline column: its editor
 * is a 200px box with a toolbar, or a textarea whose line breaks are the point,
 * and a table row is neither tall enough nor wide enough to be either. Rather
 * than shrink it into a cell or leave the cell inert, clicking one opens the
 * record in the dialog it needs.
 *
 * "locked" is every other reason a cell will not open: the update form marks the
 * field read-only, or the column is calculated (`resolveFields` drops those, and
 * the backend refuses to store one anyway).
 */
export type CellEditKind = "inline" | "dialog" | "locked";

/** Column uuid -> what its cells do. Anything absent is "locked". */
export function cellEditKinds(fields: ResolvedField[]): Map<string, CellEditKind> {
  const kinds = new Map<string, CellEditKind>();
  for (const { column, enabled } of fields) {
    kinds.set(
      column.uuid,
      !enabled ? "locked" : needsFullEditor(column) ? "dialog" : "inline",
    );
  }
  return kinds;
}

/** The cell an editor is currently open on, and what has been typed into it. */
export interface CellDraft {
  rowUuid: string;
  columnUuid: string;
  /**
   * What the editor opened holding, carried alongside the draft so that the
   * commit can ask "did this person change anything?" without consulting the
   * grid — see `cellPatch`, which compares against this rather than against the
   * stored cell.
   */
  initial: unknown;
  value: unknown;
}

/**
 * The `data` patch a committed cell sends, or **null when nothing changed**.
 *
 * The null case is not an optimisation. Opening a cell and clicking away is a
 * gesture people make constantly, and a column with an `onupdate` token is
 * re-stamped on *every* accepted write — so a no-op PATCH would quietly move the
 * record's "last modified" without anybody having modified it.
 *
 * "Changed" means *this person changed it*, so the draft is compared against
 * what the editor opened holding rather than against the record's current cell.
 * Those are the same thing at the moment a cell opens and stop being the same
 * thing the instant a poll lands: comparing against the record would read a
 * cell somebody else has since edited as changed-by-us, and reverting their
 * value on an Enter that typed nothing is the one outcome an untouched cell must
 * never have. Comparing against the seed also keeps a lossy round trip from
 * manufacturing an edit — `deserialize` then `serialize` turns a stored `14:30`
 * into `14:30:00`, which differs from the stored string but not from itself.
 *
 * A changed lookup also clears every lookup nested under it, exactly as
 * `RecordFields` does when the dialog's parent field changes.
 */
export function cellPatch(
  columns: Column[],
  column: Column,
  draft: CellDraft,
): Record<string, unknown> | null {
  const next = toStored(column, draft.value);
  if (JSON.stringify(next) === JSON.stringify(toStored(column, draft.initial))) {
    return null;
  }
  return { [column.uuid]: next, ...clearedNested(columns, column.uuid) };
}
