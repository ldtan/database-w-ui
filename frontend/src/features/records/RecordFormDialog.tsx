import { useEffect, useState, useRef } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AppDialog } from "../../components/AppDialog";
import { Button } from "primereact/button";
import { confirmDialog } from "primereact/confirmdialog";
import { Message } from "primereact/message";

import { api } from "../../lib/api";
import { useToast } from "../../lib/toast";
import type { Column, Form, ListItem } from "../../lib/types";
import { applyFieldConditions } from "../forms/conditions";
import { RecordFields } from "./RecordFields";
import { buildRecordData, deserialize, resolveFields } from "./recordValues";
import { ErrorMessage } from "../../components/ErrorMessage";

interface Props {
  listUuid: string;
  columns: Column[];
  form: Form | null; // the create/update form whose config drives the layout
  item: ListItem | null; // null => create, otherwise edit
  /** Values to open a *new* record with, keyed by column uuid (the QR scanner's). */
  prefill?: Record<string, unknown> | null;
  /** What the QR scanner has to say: a key the form has no field for, or, when a
   *  scan was submitted and refused, the server's reason. Shown above the fields. */
  notes?: string[];
  visible: boolean;
  onHide: () => void;
  /** Overrides "Add record" / "Edit record" when the dialog is opened from
   *  somewhere that has to say *which* list is being added to — a lookup
   *  creating a row in its target. */
  header?: string;
  /** The record that was just created, for a caller that needs its uuid rather
   *  than just the side effect. Runs before `onHide`; the toast and the `items`
   *  invalidation happen either way, since the row is real whatever the caller
   *  goes on to do with it. */
  onCreated?: (item: ListItem) => void;
}

/**
 * Add or edit one record, laid out by the list's form.
 *
 * The inputs are not chosen here so much as *derived*: `resolveFields` applies the
 * form's config (which columns, in what order, with what labels, and which are
 * read-only), and each field's editor comes from its column's `type` and `config`.
 * A new column therefore appears in this dialog without touching it — that is the
 * point of the config-driven design, and the reason there is no per-list form code.
 *
 * Which form? The list's `create` form when adding and its `update` form when
 * editing, picked by the page. Values are sent under column **uuids**, never names,
 * because that is how records are stored.
 *
 * A new record can arrive part-written: `prefill` is what a scanned QR code could
 * make sense of, and `notes` is what it could not (see `qrFill.ts`). The dialog
 * treats those values exactly as if they had been typed — a QR fills this form, it
 * does not bypass it.
 */
export function RecordFormDialog({
  listUuid,
  columns,
  form,
  item,
  prefill,
  notes,
  visible,
  onHide,
  header,
  onCreated,
}: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [values, setValues] = useState<Record<string, unknown>>({});
  // Recomputed every render, which is what makes a conditional field appear and
  // disappear as the field driving it is typed into — no effect, no second copy of the
  // draft. `resolveFields` stays pure and values-free so the surfaces that must not
  // apply conditions (the grid's cell editing, the QR fill) go on calling it directly.
  //
  // Both answers are kept because "no fields" has two causes now, and telling somebody
  // to add a column when the list has plenty is worse than saying nothing.
  const laidOut = resolveFields(columns, form);
  const fields = applyFieldConditions(laidOut, form, values, columns);

  /**
   * What the dialog opened with, so closing it can tell a filled-in form from an
   * untouched one.
   *
   * Escape, the X and Cancel all discarded everything typed, with no prompt — on a
   * fifteen-field form, with focus in a text box, one Escape. The app already
   * treats this as worth confirming for the *one-row* draft in the grid
   * (`confirmDiscardDraft`), which is strictly less work to lose. Comparing against
   * the seed rather than raising a flag on the first keystroke means a value typed
   * and typed back is not a change, and reopening an unedited record still closes
   * in one press.
   */
  const seed = useRef<string>("{}");

  useEffect(() => {
    if (!visible) return;
    if (!item) {
      // Read, not depended on: the prefill is handed over as the dialog opens.
      setValues(prefill ?? {});
      seed.current = JSON.stringify(prefill ?? {});
      return;
    }
    const initial: Record<string, unknown> = {};
    for (const column of columns) {
      const raw = item.data?.[column.uuid];
      if (raw !== undefined && raw !== null) {
        initial[column.uuid] = deserialize(column, raw);
      }
    }
    setValues(initial);
    seed.current = JSON.stringify(initial);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, item?.uuid]);

  function requestHide() {
    if (JSON.stringify(values) === seed.current) {
      onHide();
      return;
    }
    confirmDialog({
      message: item
        ? "Discard your changes to this record?"
        : "Discard this record? Everything filled in will be lost.",
      header: item ? "Discard changes" : "Discard record",
      icon: "pi pi-exclamation-triangle",
      acceptLabel: "Discard",
      acceptClassName: "p-button-danger",
      rejectLabel: "Keep editing",
      accept: onHide,
    });
  }

  const persist = useMutation({
    mutationFn: () => {
      const data = buildRecordData(fields, values, !!item);
      return item
        ? api.patch<ListItem>(`/lists/${listUuid}/items/${item.uuid}`, { data })
        : api.post<ListItem>(`/lists/${listUuid}/items/`, { data });
    },
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      toast.success(item ? "Record updated" : "Record added");
      if (!item) onCreated?.(saved);
      onHide();
    },
  });

  return (
    <AppDialog
      header={header ?? (item ? "Edit record" : "Add record")}
      visible={visible}
      onHide={requestHide}
      style={{ width: 480 }}
      footer={
        <>
          <Button label="Cancel" text onClick={requestHide} />
          <Button
            label={item ? "Save" : "Add record"}
            icon={item ? "pi pi-check" : "pi pi-plus"}
            onClick={() => persist.mutate()}
            loading={persist.isPending}
            // Either cause leaves nothing to write, and the hint in the body says
            // which one it is.
            disabled={fields.length === 0}
          />
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <ErrorMessage error={persist.error} />
        {notes && notes.length > 0 && (
          <Message
            severity="warn"
            content={
              <div className="flex flex-col gap-1">
                <span className="font-semibold">From the QR code:</span>
                <ul className="m-0 pl-4">
                  {notes.map((note, i) => (
                    <li key={i}>{note}</li>
                  ))}
                </ul>
              </div>
            }
          />
        )}
        {laidOut.length === 0 && (
          <p className="field-hint">Add a column first.</p>
        )}
        {/* Every field this form has is conditional and none of them apply. Saying so
            beats an empty dialog, and beats "add a column first" — the list has
            columns, they just do not apply to the record as it stands. */}
        {laidOut.length > 0 && fields.length === 0 && (
          <p className="field-hint">
            None of this form's fields apply yet. They appear as the record is filled
            in elsewhere.
          </p>
        )}
        <RecordFields
          listUuid={listUuid}
          fields={fields}
          columns={columns}
          values={values}
          onChange={setValues}
        />
      </div>
    </AppDialog>
  );
}
