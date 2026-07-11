import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Dialog } from "primereact/dialog";
import { InputText } from "primereact/inputtext";
import { InputTextarea } from "primereact/inputtextarea";
import { InputNumber } from "primereact/inputnumber";
import { Dropdown } from "primereact/dropdown";
import { Checkbox } from "primereact/checkbox";
import { Calendar } from "primereact/calendar";
import { Button } from "primereact/button";
import { Message } from "primereact/message";

import { api, ApiError } from "../../lib/api";
import type { Column, Form, ListItem } from "../../lib/types";

interface Props {
  listUuid: string;
  columns: Column[];
  form: Form | null; // the create/update form whose config drives the layout
  item: ListItem | null; // null => create, otherwise edit
  visible: boolean;
  onHide: () => void;
}

interface ResolvedField {
  column: Column;
  enabled: boolean;
  label: string;
}

// Apply the form's config: only included fields, in order, with enabled and
// label overrides. An empty/absent config means "all columns in schema order".
function resolveFields(columns: Column[], form: Form | null): ResolvedField[] {
  if (form && form.config.fields && form.config.fields.length > 0) {
    const byUuid = new Map(columns.map((c) => [c.uuid, c]));
    return form.config.fields
      .filter((f) => f.included)
      .slice()
      .sort((a, b) => a.order - b.order)
      .map((f) => {
        const column = byUuid.get(f.column_uuid);
        if (!column) return null;
        return {
          column,
          enabled: f.enabled !== false,
          label:
            (f.label_override && f.label_override.trim()) || column.display_name,
        };
      })
      .filter((x): x is ResolvedField => x !== null);
  }
  return columns.map((c) => ({ column: c, enabled: true, label: c.display_name }));
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}
function fmtDate(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
function fmtTime(d: Date): string {
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function serialize(column: Column, value: unknown): unknown {
  if (value instanceof Date) {
    if (column.type === "date") return fmtDate(value);
    if (column.type === "time") return fmtTime(value);
    if (column.type === "datetime") return `${fmtDate(value)}T${fmtTime(value)}`;
  }
  return value;
}

function deserialize(column: Column, raw: unknown): unknown {
  const s = String(raw);
  if (column.type === "date" || column.type === "datetime") {
    const [datePart, timePart] = s.split("T");
    const [y, mo, da] = datePart.split("-").map(Number);
    if (column.type === "date") return new Date(y, mo - 1, da);
    const [h, mi, se] = (timePart ?? "00:00:00").split(":").map(Number);
    return new Date(y, mo - 1, da, h, mi, se || 0);
  }
  if (column.type === "time") {
    const [h, mi, se] = s.split(":").map(Number);
    return new Date(1970, 0, 1, h, mi, se || 0);
  }
  return raw;
}

function RecordField({
  column,
  value,
  disabled,
  onChange,
}: {
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

export function RecordFormDialog({
  listUuid,
  columns,
  form,
  item,
  visible,
  onHide,
}: Props) {
  const queryClient = useQueryClient();
  const [values, setValues] = useState<Record<string, unknown>>({});
  const fields = resolveFields(columns, form);

  useEffect(() => {
    if (!visible) return;
    if (!item) {
      setValues({});
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
      const data: Record<string, unknown> = {};
      for (const { column, enabled } of fields) {
        if (!enabled) continue; // read-only fields keep default/existing value
        const value = values[column.uuid];
        const empty = value === undefined || value === null || value === "";
        if (item) {
          data[column.uuid] = empty ? null : serialize(column, value);
        } else if (!empty) {
          data[column.uuid] = serialize(column, value);
        }
      }
      return item
        ? api.patch<ListItem>(`/lists/${listUuid}/items/${item.uuid}`, { data })
        : api.post<ListItem>(`/lists/${listUuid}/items/`, { data });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      onHide();
    },
  });

  return (
    <Dialog
      header={item ? "Edit record" : "Add record"}
      visible={visible}
      onHide={onHide}
      style={{ width: 460 }}
    >
      <div className="flex flex-col gap-4">
        {persist.error instanceof ApiError && (
          <Message severity="error" text={persist.error.message} />
        )}
        {fields.length === 0 && <p>Add a column first.</p>}
        {fields.map(({ column, enabled, label }) => (
          <div key={column.uuid} className="flex flex-col gap-2">
            <label htmlFor={`field-${column.uuid}`}>
              {label}
              {column.config.required ? " *" : ""}
            </label>
            <RecordField
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
    </Dialog>
  );
}
