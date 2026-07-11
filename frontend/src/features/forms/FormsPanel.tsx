import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Dialog } from "primereact/dialog";
import { Dropdown } from "primereact/dropdown";
import { Checkbox } from "primereact/checkbox";
import { InputText } from "primereact/inputtext";
import { Button } from "primereact/button";
import { Message } from "primereact/message";

import { api, ApiError } from "../../lib/api";
import type { Column, Form } from "../../lib/types";

interface Row {
  column_uuid: string;
  display_name: string;
  included: boolean;
  enabled: boolean;
  label_override: string;
}

// Seed editor rows from a form's stored fields, appending any columns not yet
// in the config (so newly-added columns show up). An empty stored config means
// "all columns", which this naturally produces.
function seedRows(form: Form, columns: Column[]): Row[] {
  const byUuid = new Map(columns.map((c) => [c.uuid, c]));
  const rows: Row[] = [];
  const seen = new Set<string>();
  const stored = [...(form.config.fields ?? [])].sort((a, b) => a.order - b.order);
  for (const field of stored) {
    const col = byUuid.get(field.column_uuid);
    if (!col) continue;
    rows.push({
      column_uuid: col.uuid,
      display_name: col.display_name,
      included: field.included,
      enabled: field.enabled,
      label_override: field.label_override ?? "",
    });
    seen.add(col.uuid);
  }
  for (const col of columns) {
    if (seen.has(col.uuid)) continue;
    rows.push({
      column_uuid: col.uuid,
      display_name: col.display_name,
      included: true,
      enabled: true,
      label_override: "",
    });
  }
  return rows;
}

interface Props {
  listUuid: string;
  columns: Column[];
  forms: Form[];
  visible: boolean;
  onHide: () => void;
}

export function FormsPanel({ listUuid, columns, forms, visible, onHide }: Props) {
  const queryClient = useQueryClient();
  const [selectedUuid, setSelectedUuid] = useState<string | null>(null);
  const [rows, setRows] = useState<Row[]>([]);

  const activeForm =
    forms.find((f) => f.uuid === selectedUuid) ?? forms[0] ?? null;

  useEffect(() => {
    if (!visible) return;
    if (!selectedUuid && forms.length > 0) setSelectedUuid(forms[0].uuid);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, forms]);

  useEffect(() => {
    if (activeForm) setRows(seedRows(activeForm, columns));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeForm?.uuid, columns]);

  const save = useMutation({
    mutationFn: () =>
      api.patch<Form>(`/lists/${listUuid}/forms/${activeForm!.uuid}`, {
        config: {
          fields: rows.map((r, i) => ({
            column_uuid: r.column_uuid,
            included: r.included,
            enabled: r.enabled,
            order: i,
            label_override: r.label_override.trim() || null,
          })),
        },
      }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["forms", listUuid] }),
  });

  function move(index: number, delta: number) {
    setRows((prev) => {
      const next = [...prev];
      const target = index + delta;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }
  function update(index: number, patch: Partial<Row>) {
    setRows((prev) => prev.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  }

  return (
    <Dialog header="Forms" visible={visible} onHide={onHide} style={{ width: 640 }}>
      <div className="flex flex-col gap-4">
        {save.error instanceof ApiError && (
          <Message severity="error" text={save.error.message} />
        )}
        <Dropdown
          value={activeForm?.uuid ?? null}
          options={forms.map((f) => ({
            label: `${f.display_name}${f.is_default ? " (default)" : ""}`,
            value: f.uuid,
          }))}
          onChange={(e) => setSelectedUuid(e.value)}
          className="w-64"
        />

        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2 text-sm text-gray-500 px-1">
            <span style={{ width: 64 }}>Order</span>
            <span className="flex-1">Field</span>
            <span style={{ width: 70 }}>Shown</span>
            <span style={{ width: 76 }}>Editable</span>
            <span style={{ width: 150 }}>Label</span>
          </div>
          {rows.map((row, i) => (
            <div key={row.column_uuid} className="flex items-center gap-2">
              <div className="flex" style={{ width: 64 }}>
                <Button
                  icon="pi pi-chevron-up"
                  text
                  size="small"
                  aria-label="Move up"
                  disabled={i === 0}
                  onClick={() => move(i, -1)}
                />
                <Button
                  icon="pi pi-chevron-down"
                  text
                  size="small"
                  aria-label="Move down"
                  disabled={i === rows.length - 1}
                  onClick={() => move(i, 1)}
                />
              </div>
              <span className="flex-1">{row.display_name}</span>
              <span style={{ width: 70 }}>
                <Checkbox
                  checked={row.included}
                  onChange={(e) => update(i, { included: !!e.checked })}
                />
              </span>
              <span style={{ width: 76 }}>
                <Checkbox
                  checked={row.enabled}
                  disabled={!row.included}
                  onChange={(e) => update(i, { enabled: !!e.checked })}
                />
              </span>
              <InputText
                value={row.label_override}
                onChange={(e) => update(i, { label_override: e.target.value })}
                placeholder={row.display_name}
                style={{ width: 150 }}
              />
            </div>
          ))}
          {rows.length === 0 && <p>Add a column to configure this form.</p>}
        </div>

        <div className="flex justify-end gap-2">
          <Button label="Close" text onClick={onHide} />
          <Button
            label="Save"
            onClick={() => save.mutate()}
            loading={save.isPending}
            disabled={!activeForm || rows.length === 0}
          />
        </div>
      </div>
    </Dialog>
  );
}
