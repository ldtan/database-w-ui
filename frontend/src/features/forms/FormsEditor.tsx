import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Dropdown } from "primereact/dropdown";
import { Checkbox } from "primereact/checkbox";
import { AppDialog } from "../../components/AppDialog";
import { InputText } from "primereact/inputtext";
import { Button } from "primereact/button";
import { Message } from "primereact/message";

import { api, ApiError } from "../../lib/api";
import { NameFields } from "../../components/NameFields";
import { isValidName } from "../../lib/slug";
import { useToast } from "../../lib/toast";
import type { Column, Form } from "../../lib/types";
import { ShareDialog } from "../permissions/ShareDialog";
import { useEffectiveActionsMap } from "../permissions/useEffectiveActions";

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
  // False while the tab is not showing, so it does not seed its editor from a form
  // nobody is looking at.
  active: boolean;
}

/** Per-list form layouts. The body of what used to be its own dialog; it now lives
 *  as a tab in List settings. */
export function FormsEditor({ listUuid, columns, forms, active }: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [selectedUuid, setSelectedUuid] = useState<string | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [sharing, setSharing] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [names, setNames] = useState({ displayName: "", name: "" });

  const activeForm =
    forms.find((f) => f.uuid === selectedUuid) ?? forms[0] ?? null;

  // A form can be targeted at a specific audience, so its own grants — not the
  // list's — decide who may edit or re-share it.
  const canForm = useEffectiveActionsMap(
    "form",
    forms.map((form) => form.uuid),
  );
  const canEdit = !!activeForm && canForm(activeForm.uuid, "configure");
  const canShare = !!activeForm && canForm(activeForm.uuid, "share");
  // A view form displays a record, it does not collect one, so per-field
  // "editable" has nothing to say — and a control that is saved but ignored is
  // worse than no control. Shown, order and label still apply.
  const showsEnabled = activeForm?.type !== "view";

  const setInherit = useMutation({
    mutationFn: (value: boolean) =>
      api.patch(`/lists/${listUuid}/forms/${activeForm!.uuid}`, {
        permissions_inherit: value,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["forms", listUuid] });
      queryClient.invalidateQueries({ queryKey: ["effective"] });
    },
    onError: (err) =>
      toast.error(
        "Could not update inheritance",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  useEffect(() => {
    if (!active) return;
    if (!selectedUuid && forms.length > 0) setSelectedUuid(forms[0].uuid);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, forms]);

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
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["forms", listUuid] });
      toast.success("Form saved", activeForm?.display_name);
    },
  });

  const rename = useMutation({
    mutationFn: () =>
      api.patch<Form>(`/lists/${listUuid}/forms/${activeForm!.uuid}`, {
        name: names.name,
        display_name: names.displayName,
      }),
    onSuccess: (form) => {
      setRenaming(false);
      queryClient.invalidateQueries({ queryKey: ["forms", listUuid] });
      toast.success("Form renamed", form.display_name);
    },
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

  if (forms.length === 0) {
    return <Message severity="info" text="This list has no forms." />;
  }

  return (
    <>
      <div className="flex flex-col gap-4">
        {save.error instanceof ApiError && (
          <Message severity="error" text={save.error.message} />
        )}
        <div className="flex flex-wrap items-center gap-2">
          <Dropdown
            value={activeForm?.uuid ?? null}
            options={forms.map((f) => ({
              label: `${f.display_name}${f.is_default ? " (default)" : ""}`,
              value: f.uuid,
            }))}
            onChange={(e) => setSelectedUuid(e.value)}
            className="w-full sm:w-64"
          />
          {canEdit && activeForm && (
            <Button
              label="Rename"
              icon="pi pi-pencil"
              text
              onClick={() => {
                setNames({
                  displayName: activeForm.display_name,
                  name: activeForm.name,
                });
                setRenaming(true);
              }}
            />
          )}
          {canShare && (
            <Button
              label="Share"
              icon="pi pi-share-alt"
              text
              onClick={() => setSharing(true)}
            />
          )}
        </div>

        {/* Order + field + two checkboxes + a label box is about 460px of columns
            that have to stay in step with the header above them. Rather than reflow
            them on a phone and lose that alignment, the block keeps its width and
            scrolls sideways within the dialog. */}
        <div className="overflow-x-auto">
        <div className="flex flex-col gap-1" style={{ minWidth: 460 }}>
          <div className="flex items-center gap-2 text-sm text-gray-500 px-1">
            <span style={{ width: 64 }}>Order</span>
            <span className="flex-1">Field</span>
            <span style={{ width: 70 }}>Shown</span>
            {showsEnabled && <span style={{ width: 76 }}>Editable</span>}
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
                  disabled={!canEdit || i === 0}
                  onClick={() => move(i, -1)}
                />
                <Button
                  icon="pi pi-chevron-down"
                  text
                  size="small"
                  aria-label="Move down"
                  disabled={!canEdit || i === rows.length - 1}
                  onClick={() => move(i, 1)}
                />
              </div>
              <span className="flex-1">{row.display_name}</span>
              <span style={{ width: 70 }}>
                <Checkbox
                  checked={row.included}
                  disabled={!canEdit}
                  onChange={(e) => update(i, { included: !!e.checked })}
                />
              </span>
              {showsEnabled && (
                <span style={{ width: 76 }}>
                  <Checkbox
                    checked={row.enabled}
                    disabled={!canEdit || !row.included}
                    onChange={(e) => update(i, { enabled: !!e.checked })}
                  />
                </span>
              )}
              <InputText
                value={row.label_override}
                onChange={(e) => update(i, { label_override: e.target.value })}
                placeholder={row.display_name}
                disabled={!canEdit}
                style={{ width: 150 }}
              />
            </div>
          ))}
          {rows.length === 0 && <p>Add a column to configure this form.</p>}
        </div>
        </div>

        <div className="flex justify-end gap-2">
          {canEdit && (
            <Button
              label="Save"
              onClick={() => save.mutate()}
              loading={save.isPending}
              disabled={rows.length === 0}
            />
          )}
        </div>
      </div>

      <AppDialog
        header={activeForm ? `Rename “${activeForm.display_name}”` : ""}
        visible={renaming}
        onHide={() => setRenaming(false)}
        style={{ width: 380 }}
      >
        <div className="flex flex-col gap-4">
          {rename.error instanceof ApiError && (
            <Message severity="error" text={rename.error.message} />
          )}
          <NameFields
            key={activeForm?.uuid ?? "none"}
            idPrefix="form"
            value={names}
            onChange={setNames}
            linked={false}
            autoFocus
          />
          <div className="flex justify-end gap-2">
            <Button label="Cancel" text onClick={() => setRenaming(false)} />
            <Button
              label="Save"
              onClick={() => rename.mutate()}
              loading={rename.isPending}
              disabled={!names.displayName.trim() || !isValidName(names.name)}
            />
          </div>
        </div>
      </AppDialog>

      {sharing && activeForm && (
        <ShareDialog
          visible={sharing}
          onHide={() => setSharing(false)}
          resourceType="form"
          resourceUuid={activeForm.uuid}
          resourceLabel={activeForm.display_name}
          inherit={{
            value: activeForm.permissions_inherit,
            onChange: (value) => setInherit.mutate(value),
            busy: setInherit.isPending,
          }}
        />
      )}
    </>
  );
}
