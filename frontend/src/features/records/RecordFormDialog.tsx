import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AppDialog } from "../../components/AppDialog";
import { InputText } from "primereact/inputtext";
import { InputTextarea } from "primereact/inputtextarea";
import { InputNumber } from "primereact/inputnumber";
import { Dropdown } from "primereact/dropdown";
import { Checkbox } from "primereact/checkbox";
import { Calendar } from "primereact/calendar";
import { Button } from "primereact/button";
import { Message } from "primereact/message";

import { api, ApiError } from "../../lib/api";
import { useToast } from "../../lib/toast";
import type { Column, Form, ListItem } from "../../lib/types";
import { LookupField } from "../lookups/LookupControls";
import { PrincipalField } from "../principals/PrincipalControls";
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

function RecordField({
  listUuid,
  column,
  value,
  disabled,
  onChange,
}: {
  listUuid: string;
  column: Column;
  value: unknown;
  disabled: boolean;
  onChange: (v: unknown) => void;
}) {
  const id = `field-${column.uuid}`;
  switch (column.type) {
    case "long_text":
      return (
        <InputTextarea
          id={id}
          value={(value as string) ?? ""}
          onChange={(e) => onChange(e.target.value)}
          rows={3}
          disabled={disabled}
        />
      );
    case "number":
      return (
        <InputNumber
          inputId={id}
          value={(value as number) ?? null}
          onValueChange={(e) => onChange(e.value ?? null)}
          useGrouping={false}
          disabled={disabled}
        />
      );
    case "checkbox":
      return (
        <Checkbox
          inputId={id}
          checked={!!value}
          onChange={(e) => onChange(!!e.checked)}
          disabled={disabled}
        />
      );
    case "choice": {
      const options = (column.config.options ?? []).map((o) => ({
        label: o.label,
        value: o.id,
      }));
      return (
        <Dropdown
          inputId={id}
          value={(value as string) ?? null}
          options={options}
          onChange={(e) => onChange(e.value)}
          showClear
          placeholder="Select…"
          disabled={disabled}
        />
      );
    }
    case "date":
      return (
        <Calendar
          inputId={id}
          value={(value as Date) ?? null}
          onChange={(e) => onChange(e.value ?? null)}
          dateFormat="yy-mm-dd"
          showButtonBar
          disabled={disabled}
        />
      );
    case "time":
      return (
        <Calendar
          inputId={id}
          value={(value as Date) ?? null}
          onChange={(e) => onChange(e.value ?? null)}
          timeOnly
          disabled={disabled}
        />
      );
    case "datetime":
      return (
        <Calendar
          inputId={id}
          value={(value as Date) ?? null}
          onChange={(e) => onChange(e.value ?? null)}
          showTime
          dateFormat="yy-mm-dd"
          disabled={disabled}
        />
      );
    case "link":
      return (
        <InputText
          id={id}
          type="url"
          value={(value as string) ?? ""}
          onChange={(e) => onChange(e.target.value)}
          placeholder="https://example.com"
          disabled={disabled}
        />
      );
    case "lookup":
      return (
        <LookupField
          listUuid={listUuid}
          column={column}
          value={value}
          disabled={disabled}
          onChange={onChange}
        />
      );
    case "user":
    case "group":
      return (
        <PrincipalField
          column={column}
          value={value}
          disabled={disabled}
          onChange={onChange}
        />
      );
    default:
      return (
        <InputText
          id={id}
          value={(value as string) ?? ""}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
        />
      );
  }
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
      style={{ width: 460 }}
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
        {fields.length === 0 && <p>Add a column first.</p>}
        {fields.map(({ column, enabled, label }) => (
          <div key={column.uuid} className="flex flex-col gap-2">
            <label htmlFor={`field-${column.uuid}`}>
              {label}
              {column.config.required ? " *" : ""}
            </label>
            <RecordField
              listUuid={listUuid}
              column={column}
              value={values[column.uuid]}
              disabled={!enabled}
              onChange={(v) =>
                setValues((prev) => ({ ...prev, [column.uuid]: v }))
              }
            />
          </div>
        ))}
        <div className="flex justify-end gap-2">
          <Button label="Cancel" text onClick={onHide} />
          <Button
            label={item ? "Save" : "Add"}
            onClick={() => persist.mutate()}
            loading={persist.isPending}
            disabled={fields.length === 0}
          />
        </div>
      </div>
    </AppDialog>
  );
}
