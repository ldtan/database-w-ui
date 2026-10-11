/** Which columns go into a table's QR codes, and the saving of that choice.
 *
 *  The shape is lifted from the export dialog's column table (`ImportExportDialog`): a
 *  `Set` of uuids, All/None, and a scrolling table of checkboxes whose labels show the
 *  `display_name` with the `name` in parentheses. That last detail matters more here than
 *  it does there — the `name` **is** the key the payload is written under, so somebody
 *  authoring a code by hand is reading it off this table.
 *
 *  **The selection is saved on the table**, not held per session, because it describes a
 *  printed artifact rather than how somebody is working right now: every badge for a table
 *  should carry the same fields, and re-picking them per print is how two batches end up
 *  disagreeing. It lives in `tables.settings.qr` — the first key of that blob anything
 *  reads — and saving needs `configure`, so a viewer gets the layout and cannot change it.
 */
import { useMemo } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "primereact/button";
import { Checkbox } from "primereact/checkbox";
import { Message } from "primereact/message";

import { api } from "../../lib/api";
import { QR_UUID_KEY } from "../../lib/qr";
import { useToast } from "../../lib/toast";
import { tableSettings, type Column, type TableRead } from "../../lib/types";

export interface QrSelection {
  /** Column **names**, in the order the table declares them. Names rather than uuids
   *  because that is the payload's key, and because a clone keeps every name. */
  columns: string[];
  includeUuid: boolean;
}

/** The saved selection, or the sensible default for a table that has none.
 *
 *  The default is the **primary column plus the uuid**: a badge that says who it is and
 *  carries a machine id, which is the shape the scanner was built around ("a badge's code
 *  says *who* someone is"). Everything else is a choice somebody should make deliberately,
 *  because every field added is bytes, and bytes are modules.
 */
export function resolveQrSelection(
  lst: TableRead | undefined,
  columns: Column[],
): QrSelection {
  const saved = tableSettings(lst).qr;
  if (saved && Array.isArray(saved.columns)) {
    const names = new Set(columns.map((c) => c.name));
    return {
      // Filtered against the table's current columns: a saved selection outlives the
      // columns it names, and one that was renamed away should quietly stop appearing
      // rather than show as a ghost tick. The server ignores it too.
      columns: saved.columns.filter(
        (n): n is string => typeof n === "string" && names.has(n),
      ),
      includeUuid: saved.include_uuid !== false,
    };
  }
  const primary = columns.find((c) => c.uuid === lst?.primary_column?.uuid);
  return { columns: primary ? [primary.name] : [], includeUuid: true };
}

export function QrColumnPicker({
  tableUuid,
  columns,
  selection,
  onChange,
  canSave,
}: {
  tableUuid: string;
  columns: Column[];
  selection: QrSelection;
  onChange: (next: QrSelection) => void;
  /** `configure` on the table. Without it the picker still works for this print — it is
   *  the *saving* that is gated, not the choosing. */
  canSave: boolean;
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const chosen = useMemo(() => new Set(selection.columns), [selection.columns]);

  const save = useMutation({
    mutationFn: () =>
      api.patch(`/tables/${tableUuid}`, {
        settings: {
          qr: { columns: selection.columns, include_uuid: selection.includeUuid },
        },
      }),
    onSuccess: () => {
      // The table object carries `settings`, so anything holding it has to re-read.
      queryClient.invalidateQueries({ queryKey: ["table", tableUuid] });
      queryClient.invalidateQueries({ queryKey: ["tables"] });
      toast.success("Saved", "New codes for this table will use these fields.");
    },
    onError: () => toast.error("Could not save", "The fields were not changed."),
  });

  function toggle(name: string, on: boolean) {
    const next = on
      ? // Kept in the table's own column order rather than click order, so the payload's
        // key order is a property of the table and not of how somebody ticked.
        columns.filter((c) => chosen.has(c.name) || c.name === name).map((c) => c.name)
      : selection.columns.filter((n) => n !== name);
    onChange({ ...selection, columns: next });
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <Checkbox
          inputId="qr-include-uuid"
          checked={selection.includeUuid}
          onChange={(e) => onChange({ ...selection, includeUuid: !!e.checked })}
        />
        <label htmlFor="qr-include-uuid" className="flex-1 cursor-pointer text-sm">
          Record id{" "}
          <span className="text-muted">({QR_UUID_KEY})</span>
        </label>
      </div>
      <p className="field-hint -mt-1">
        What makes a code identify the record it came from. Scanning it back into this app
        ignores it — a fill makes a new record — so leave it on unless something outside
        the app is reading the codes and does not want it.
      </p>

      <div className="flex items-center justify-between">
        <span className="text-sm text-muted">
          {selection.columns.length} of {columns.length} fields
        </span>
        <span className="flex items-center gap-1">
          <Button
            label="All"
            text
            size="small"
            onClick={() => onChange({ ...selection, columns: columns.map((c) => c.name) })}
          />
          <Button
            label="None"
            text
            size="small"
            onClick={() => onChange({ ...selection, columns: [] })}
          />
        </span>
      </div>
      <div className="flex flex-col gap-1 max-h-48 overflow-auto">
        {columns.map((col) => (
          <div key={col.uuid} className="flex items-center gap-2">
            <Checkbox
              inputId={`qr-${col.uuid}`}
              checked={chosen.has(col.name)}
              onChange={(e) => toggle(col.name, !!e.checked)}
            />
            <label
              htmlFor={`qr-${col.uuid}`}
              className="flex-1 cursor-pointer text-sm"
            >
              {col.display_name} <span className="text-muted">({col.name})</span>
            </label>
          </div>
        ))}
      </div>

      {canSave ? (
        // Left-aligned in its own row rather than stretched: a `text` Button fills the
        // column it is in, which put the icon hard against the left edge and the label
        // floating in the middle, reading as a layout accident.
        <div className="flex">
          <Button
            label="Save as this table's default"
            icon="pi pi-check"
            text
            size="small"
            loading={save.isPending}
            onClick={() => save.mutate()}
          />
        </div>
      ) : (
        // Worded to be true whether or not the table has a saved default — a viewer
        // opening this on a table nobody has configured would otherwise be told it has
        // one. What they can be told either way is what their change does and does not do.
        <Message
          severity="info"
          text="Changes here apply to this print only. Saving them as the table's default needs permission to configure the table."
        />
      )}
    </div>
  );
}
