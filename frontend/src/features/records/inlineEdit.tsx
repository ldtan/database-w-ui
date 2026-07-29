/**
 * Editing values from the grid itself — the toolbar's "Edit cells" mode.
 *
 * The dialog remains the way to edit a *record*: it lays the fields out the way
 * the list's update form says, and it saves them together. This is the way to
 * edit a *value* — one cell, committed on its own, for the correction you make
 * while reading rows rather than the record you sit down to fill in.
 *
 * Three things make that a small feature rather than a second editor:
 *
 * - `PATCH /items/{uuid}` is a **partial merge** of `data` (see
 *   `ListItemService._validate_data`, which starts from the stored dict), so one
 *   cell is one request carrying one key. There is no read-modify-write here and
 *   no endpoint of its own.
 * - The inputs are `RecordField`, the same ones the dialog uses. A column type
 *   that gains an editor gains it in both places at once, which is the whole
 *   reason that component lives apart from the dialog.
 * - Which columns may be written is `resolveFields(columns, updateForm)` — the
 *   *update form's* answer, not a new one. A field the form marks read-only, and
 *   a calculated column that stores nothing, are not editable here either.
 *
 * What this file does **not** do is decide whether the caller may edit the
 * record: that is per-record (`canItem(uuid, "edit")`) and belongs to the page,
 * which already resolves it for every row on screen.
 */
import { useEffect, useState } from "react";

import type { Column, ListItem } from "../../lib/types";
import { RecordField } from "./RecordFields";
import {
  deserialize,
  isEmptyValue,
  nestedDescendants,
  serialize,
  type ResolvedField,
} from "./recordValues";

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
    const mode = column.config.text_mode;
    kinds.set(
      column.uuid,
      !enabled
        ? "locked"
        : column.type === "text" && (mode === "rich" || mode === "multiline")
          ? "dialog"
          : "inline",
    );
  }
  return kinds;
}

export function cellEditKind(
  kinds: Map<string, CellEditKind>,
  columnUuid: string,
): CellEditKind {
  return kinds.get(columnUuid) ?? "locked";
}

/** The cell an editor is currently open on, and what has been typed into it. */
export interface CellDraft {
  rowUuid: string;
  columnUuid: string;
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
 * Both sides are normalised before they are compared, because the same absence
 * is spelled several ways: a cleared box is `""`, an emptied multi-select is
 * `[]`, and the stored cell is `null` (see `isEmptyValue`, which is the rule the
 * dialog and the backend already share).
 *
 * A changed lookup also clears every lookup nested under it, exactly as
 * `RecordFields` does when the dialog's parent field changes — a city chosen for
 * the old country means nothing once the country moves. The merge makes that the
 * same one request rather than a second round trip.
 */
export function cellPatch(
  columns: Column[],
  column: Column,
  row: ListItem,
  draft: unknown,
): Record<string, unknown> | null {
  const next = isEmptyValue(draft) ? null : serialize(column, draft);
  const stored = row.data?.[column.uuid];
  const prev = isEmptyValue(stored) ? null : stored;
  if (JSON.stringify(next) === JSON.stringify(prev)) return null;

  const patch: Record<string, unknown> = { [column.uuid]: next };
  for (const uuid of nestedDescendants(columns, column.uuid)) patch[uuid] = null;
  return patch;
}

/**
 * One cell's editor, opened in place.
 *
 * It holds its **own** draft and never calls PrimeReact's `editorCallback`. That
 * is deliberate: `editorCallback` writes through `ObjectUtils.mutateFieldData`
 * into a shallow copy of the row, whose nested `data` object is the very one
 * react-query is caching — so every keystroke would edit the cached record, and
 * a cancelled edit (Escape) would leave the typed value sitting in the cache
 * with nothing to restore it from. Keeping the draft here means the row objects
 * stay exactly as the server sent them until a save actually succeeds.
 *
 * The draft is reported upwards rather than returned, because the thing that
 * decides *when* to commit is PrimeReact's cell lifecycle (Enter, Tab, a click
 * outside), which surfaces on the column as `onCellEditComplete`. `rowUuid` and
 * `columnUuid` travel with it so the page can tell a live draft from the remains
 * of the cell edited before it.
 */
export function InlineCellEditor({
  listUuid,
  column,
  columns,
  row,
  onDraft,
}: {
  listUuid: string;
  column: Column;
  /** Every column, for an editor that reads a sibling cell — a nested lookup. */
  columns: Column[];
  row: ListItem;
  onDraft: (draft: CellDraft) => void;
}) {
  const [value, setValue] = useState<unknown>(() =>
    deserialize(column, row.data?.[column.uuid] ?? null),
  );

  // Claim the draft slot as the editor opens, so that a commit can always tell
  // which cell the value in hand belongs to — including a cell opened and
  // committed without a single keystroke.
  useEffect(() => {
    onDraft({ rowUuid: row.uuid, columnUuid: column.uuid, value });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="cell-editor">
      <RecordField
        listUuid={listUuid}
        column={column}
        value={value}
        disabled={false}
        onChange={(v) => {
          setValue(v);
          onDraft({ rowUuid: row.uuid, columnUuid: column.uuid, value: v });
        }}
        columns={columns}
        // The *stored* row: a nested lookup filters its options by the parent
        // cell's value, and in the grid that parent is another cell of this row
        // rather than another field of a draft.
        values={row.data ?? {}}
      />
    </div>
  );
}
