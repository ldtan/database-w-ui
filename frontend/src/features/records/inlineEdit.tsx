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
 *
 * The rules themselves — which columns open an editor, what a committed cell
 * sends — are in `cellEdit.ts`, which imports no React so that they can be
 * tested without one.
 */
import { useEffect, useRef, useState } from "react";

import type { Column, ListItem } from "../../lib/types";
import type { CellDraft } from "./cellEdit";
import { RecordField } from "./RecordFields";
import { deserialize } from "./recordValues";

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
 * of the cell edited before it, and `initial` travels with it so the commit can
 * tell an edited cell from one merely opened.
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
  /**
   * The record as the cache holds it *now*. Not PrimeReact's `rowData`, which is
   * a snapshot taken when the cell mounted and re-synced only on an editing-meta
   * change: seeded from that, an editor opened after a poll shows a value the
   * grid stopped displaying, and filters a nested lookup's options by a parent
   * cell that has moved on.
   */
  row: ListItem;
  onDraft: (draft: CellDraft) => void;
}) {
  const [value, setValue] = useState<unknown>(() =>
    deserialize(column, row.data?.[column.uuid] ?? null),
  );
  // What we opened holding. A ref because nothing renders it and nothing may
  // change it — it is the fixed point the commit measures the draft against.
  const initial = useRef(value);

  // Claim the draft slot as the editor opens, so that a commit can always tell
  // which cell the value in hand belongs to — including a cell opened and
  // committed without a single keystroke.
  useEffect(() => {
    onDraft({
      rowUuid: row.uuid,
      columnUuid: column.uuid,
      initial: initial.current,
      value,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Escape means two things in the same place. PrimeReact's overlay components
  // close their panel on it and call `preventDefault()` but never
  // `stopPropagation()` (dropdown.cjs.js:1187-1190; Calendar is the same), so the
  // keypress carries on to the `<td>`, which reads it as "cancel this cell" and
  // throws away the option that was just picked.
  //
  // Sampled at capture because by the time it bubbles back the panel has been
  // hidden and the evidence is gone. `data-pr-is-overlay` is the library's own
  // marker rather than a guessed class: datatable.cjs.js:1232-1238 uses exactly it
  // to stop an outside click *inside* a dropdown from closing the cell, which is
  // the same distinction being drawn here.
  //
  // Note this cannot key off `event.defaultPrevented`: `onEscapeKey` calls
  // `preventDefault()` whether or not a panel is open, so that flag is set on
  // every Escape in a dropdown and swallowing it would break cancel outright.
  const dismissedOverlay = useRef(false);

  return (
    <div
      className="cell-editor"
      onKeyDownCapture={(e) => {
        if (e.key === "Escape") {
          dismissedOverlay.current =
            document.querySelector("[data-pr-is-overlay]") !== null;
        }
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape" && dismissedOverlay.current) {
          dismissedOverlay.current = false;
          e.stopPropagation();
        }
      }}
    >
      <RecordField
        listUuid={listUuid}
        column={column}
        value={value}
        disabled={false}
        onChange={(v) => {
          setValue(v);
          onDraft({
            rowUuid: row.uuid,
            columnUuid: column.uuid,
            initial: initial.current,
            value: v,
          });
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
