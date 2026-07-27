import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AppDialog } from "../../components/AppDialog";
import { Button } from "primereact/button";
import { Message } from "primereact/message";

import { api, ApiError } from "../../lib/api";
import { useToast } from "../../lib/toast";
import type { Column, Form, ListItem } from "../../lib/types";
import { RecordFields } from "./RecordFields";
import { buildRecordData, deserialize, resolveFields } from "./recordValues";

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
}: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [values, setValues] = useState<Record<string, unknown>>({});
  const fields = resolveFields(columns, form);

  useEffect(() => {
    if (!visible) return;
    if (!item) {
      // Read, not depended on: the prefill is handed over as the dialog opens.
      setValues(prefill ?? {});
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, item?.uuid]);

  const persist = useMutation({
    mutationFn: () => {
      const data = buildRecordData(fields, values, !!item);
      return item
        ? api.patch<ListItem>(`/lists/${listUuid}/items/${item.uuid}`, { data })
        : api.post<ListItem>(`/lists/${listUuid}/items/`, { data });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      toast.success(item ? "Record updated" : "Record added");
      onHide();
    },
  });

  return (
    <AppDialog
      header={item ? "Edit record" : "Add record"}
      visible={visible}
      onHide={onHide}
      style={{ width: 480 }}
      footer={
        <>
          <Button label="Cancel" text onClick={onHide} />
          <Button
            label={item ? "Save" : "Add record"}
            icon={item ? "pi pi-check" : "pi pi-plus"}
            onClick={() => persist.mutate()}
            loading={persist.isPending}
            disabled={fields.length === 0}
          />
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {persist.error instanceof ApiError && (
          <Message severity="error" text={persist.error.message} />
        )}
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
        {fields.length === 0 && (
          <p className="field-hint">Add a column first.</p>
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
