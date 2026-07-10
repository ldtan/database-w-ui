import { useState } from "react";
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
import type { Column, ListItem } from "../../lib/types";

interface Props {
  listUuid: string;
  columns: Column[];
  visible: boolean;
  onHide: () => void;
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

function RecordField({
  column,
  value,
  onChange,
}: {
  column: Column;
  value: unknown;
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
        />
      );
    case "number":
      return (
        <InputNumber
          inputId={id}
          value={(value as number) ?? null}
          onValueChange={(e) => onChange(e.value ?? null)}
          useGrouping={false}
        />
      );
    case "checkbox":
      return (
        <Checkbox
          inputId={id}
          checked={!!value}
          onChange={(e) => onChange(!!e.checked)}
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
        />
      );
    case "time":
      return (
        <Calendar
          inputId={id}
          value={(value as Date) ?? null}
          onChange={(e) => onChange(e.value ?? null)}
          timeOnly
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
        />
      );
    default:
      return (
        <InputText
          id={id}
          value={(value as string) ?? ""}
          onChange={(e) => onChange(e.target.value)}
        />
      );
  }
}

export function RecordFormDialog({ listUuid, columns, visible, onHide }: Props) {
  const queryClient = useQueryClient();
  const [values, setValues] = useState<Record<string, unknown>>({});

  const create = useMutation({
    mutationFn: () => {
      const data: Record<string, unknown> = {};
      for (const column of columns) {
        const value = values[column.uuid];
        if (value === undefined || value === null || value === "") continue;
        data[column.uuid] = serialize(column, value);
      }
      return api.post<ListItem>(`/lists/${listUuid}/items/`, { data });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      setValues({});
      onHide();
    },
  });

  return (
    <Dialog
      header="Add record"
      visible={visible}
      onHide={onHide}
      style={{ width: 460 }}
    >
      <div className="flex flex-col gap-4">
        {create.error instanceof ApiError && (
          <Message severity="error" text={create.error.message} />
        )}
        {columns.length === 0 && <p>Add a column first.</p>}
        {columns.map((column) => (
          <div key={column.uuid} className="flex flex-col gap-2">
            <label htmlFor={`field-${column.uuid}`}>
              {column.display_name}
              {column.config.required ? " *" : ""}
            </label>
            <RecordField
              column={column}
              value={values[column.uuid]}
              onChange={(v) =>
                setValues((prev) => ({ ...prev, [column.uuid]: v }))
              }
            />
          </div>
        ))}
        <div className="flex justify-end gap-2">
          <Button label="Cancel" text onClick={onHide} />
          <Button
            label="Add"
            onClick={() => create.mutate()}
            loading={create.isPending}
            disabled={columns.length === 0}
          />
        </div>
      </div>
    </Dialog>
  );
}
