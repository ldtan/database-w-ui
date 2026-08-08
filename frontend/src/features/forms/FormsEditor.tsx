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
import { DRAG_GRIP_WIDTH, SortableList } from "../../components/SortableList";
import { isValidName } from "../../lib/slug";
import { useToast } from "../../lib/toast";
import type { Column, Form, MergeConfig } from "../../lib/types";
import { DeletedConfigDialog } from "../lists/DeletedConfigDialog";
import { ShareDialog } from "../permissions/ShareDialog";
import { useEffectiveActionsMap } from "../permissions/useEffectiveActions";
import { moveTo } from "../views/reorder";
import {
  buildFormConfigPatch,
  mergeSibling,
  sameMerge,
  type FormFieldRow,
} from "./formConfig";
import { MergeEditor } from "./MergeEditor";

/** A `FormFieldRow` plus the column's label, which the editor shows but never saves. */
interface Row extends FormFieldRow {
  display_name: string;
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
  /** The list's own `configure`, which is what the bin is gated on — a deleted form
   *  resolves to no permission context of its own, so the per-form answers below
   *  cannot speak for one. */
  canConfigure: boolean;
}

/** Per-list form layouts. The body of what used to be its own dialog; it now lives
 *  as a tab in List settings. */
export function FormsEditor({
  listUuid,
  columns,
  forms,
  active,
  canConfigure,
}: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [selectedUuid, setSelectedUuid] = useState<string | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  // null is a real value here — "this form does not merge" — so it is sent on save
  // rather than being left to the stored config (see `buildFormConfigPatch`).
  const [merge, setMerge] = useState<MergeConfig | null>(null);
  const [sharing, setSharing] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [names, setNames] = useState({ displayName: "", name: "" });
  const [showDeleted, setShowDeleted] = useState(false);

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

  useEffect(() => {
    setMerge(activeForm?.config.merge ?? null);
  }, [activeForm?.uuid, activeForm?.config.merge]);

  const save = useMutation({
    // Through `buildFormConfigPatch` rather than an object literal: the PATCH replaces
    // `config` wholesale, so anything the editor does not model has to be carried
    // across explicitly or saving the layout destroys it.
    mutationFn: async () => {
      await api.patch<Form>(`/lists/${listUuid}/forms/${activeForm!.uuid}`, {
        config: buildFormConfigPatch(activeForm!, rows, merge),
      });

      // Merging is a property of the create/update *pair* — see `mergeSibling`. The
      // layout stays this form's own; only the merge block is carried across.
      const sibling = mergeSibling(activeForm!, forms);
      if (!sibling || sameMerge(sibling.config.merge, merge)) return null;
      try {
        await api.patch<Form>(`/lists/${listUuid}/forms/${sibling.uuid}`, {
          config: { ...sibling.config, merge },
        });
        return null;
      } catch {
        // A form carries its own grants, so `configure` here does not imply it there.
        // The save that was asked for already landed; report the half that did not
        // rather than rolling back a change the user can see took effect.
        return sibling.display_name;
      }
    },
    onSuccess: (unmirrored) => {
      queryClient.invalidateQueries({ queryKey: ["forms", listUuid] });
      if (unmirrored) {
        toast.warn(
          "Saved, but not applied to “" + unmirrored + "”",
          "You cannot configure that form, so adding and editing records will not " +
            "match until someone who can applies the same linked list to it.",
        );
      } else {
        toast.success("Form saved", activeForm?.display_name);
      }
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

  // The chevrons and the grip are the same move: `moveTo` splices, so dragging a
  // field past several others lands it where it was dropped rather than trading
  // places with whatever was there. `save` writes the array index back as `order`,
  // so nothing downstream needs to know how the order changed.
  function reorder(from: number, to: number) {
    setRows((prev) => moveTo(prev, from, to));
  }
  function update(index: number, patch: Partial<Row>) {
    setRows((prev) => prev.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  }

  // The bin has to be reachable from here too, and this is the state where it
  // matters most: deleting the last form is exactly what empties this panel, and
  // without the button below there would be no way back to the form that did it.
  const binButton = canConfigure && (
    <Button
      label="Deleted"
      icon="pi pi-trash"
      text
      onClick={() => setShowDeleted(true)}
    />
  );
  const binDialog = (
    <DeletedConfigDialog
      listUuid={listUuid}
      kind="forms"
      visible={showDeleted}
      onHide={() => setShowDeleted(false)}
    />
  );

  if (forms.length === 0) {
    return (
      <div className="flex flex-col gap-3">
        <Message severity="info" text="This list has no forms." />
        {binButton && <div>{binButton}</div>}
        {binDialog}
      </div>
    );
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
          {binButton}
        </div>

        {/* Order + field + two checkboxes + a label box is about 460px of columns
            that have to stay in step with the header above them. Rather than reflow
            them on a phone and lose that alignment, the block keeps its width and
            scrolls sideways within the dialog. */}
        <div className="overflow-x-auto">
        <div className="flex flex-col gap-1" style={{ minWidth: 460 }}>
          <div className="flex items-center gap-2 text-sm text-muted px-1">
            {/* Indented past the drag grip so the headers sit over their columns. */}
            <span style={{ width: DRAG_GRIP_WIDTH, flexShrink: 0 }} />
            <span style={{ width: 64 }}>Order</span>
            <span className="flex-1">Field</span>
            <span style={{ width: 70 }}>Shown</span>
            {showsEnabled && <span style={{ width: 76 }}>Editable</span>}
            <span style={{ width: 150 }}>Label</span>
          </div>
          {/* Drag by the grip or step with the chevrons — and the grip is why the
              row is not itself draggable: these rows hold checkboxes and a text
              input, all of which a draggable row would swallow the pointer for.
              `disabled` for a reader, who has nothing to reorder. */}
          <SortableList
            items={rows}
            idOf={(row) => row.column_uuid}
            onReorder={reorder}
            disabled={!canEdit}
            className="flex flex-col gap-1"
            rowClassName="flex items-center gap-2"
          >
            {(row, i) => (
              <>
                <div className="flex" style={{ width: 64 }}>
                  <Button
                    icon="pi pi-chevron-up"
                    text
                    size="small"
                    aria-label="Move up"
                    disabled={!canEdit || i === 0}
                    onClick={() => reorder(i, i - 1)}
                  />
                  <Button
                    icon="pi pi-chevron-down"
                    text
                    size="small"
                    aria-label="Move down"
                    disabled={!canEdit || i === rows.length - 1}
                    onClick={() => reorder(i, i + 1)}
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
              </>
            )}
          </SortableList>
          {rows.length === 0 && <p>Add a column to configure this form.</p>}
        </div>
        </div>

        {/* A view form displays one record and writes nothing, so there is no second
            record for it to write either — merging is a property of a form that
            collects, like `showsEnabled` above. */}
        {showsEnabled && (
          <div className="flex flex-col gap-2 border-t pt-4">
            <span className="field-label">Linked list</span>
            <MergeEditor
              columns={columns}
              value={merge}
              disabled={!canEdit}
              onChange={setMerge}
            />
          </div>
        )}

        {/* "Save layout", not "Save" — the dialog's own footer is a few pixels
            below this one, and two buttons that both say Save do not obviously
            mean "keep this form's fields" and "close the settings". */}
        <div className="flex justify-end gap-2">
          {canEdit && (
            <Button
              label="Save layout"
              icon="pi pi-check"
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
        style={{ width: 420 }}
        footer={
          <>
            <Button label="Cancel" text onClick={() => setRenaming(false)} />
            <Button
              label="Save"
              icon="pi pi-check"
              onClick={() => rename.mutate()}
              loading={rename.isPending}
              disabled={!names.displayName.trim() || !isValidName(names.name)}
            />
          </>
        }
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

      {binDialog}
    </>
  );
}
