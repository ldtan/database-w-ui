import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Dropdown } from "primereact/dropdown";
import { Checkbox } from "primereact/checkbox";
import { AppDialog } from "../../components/AppDialog";
import { Button } from "primereact/button";
import { Message } from "primereact/message";
import { OverlayPanel } from "primereact/overlaypanel";

import { api, ApiError } from "../../lib/api";
import { NameFields } from "../../components/NameFields";
import { DRAG_GRIP_WIDTH, SortableList } from "../../components/SortableList";
import { isValidName } from "../../lib/slug";
import { useToast } from "../../lib/toast";
import type { Column, Form } from "../../lib/types";
import { DeletedConfigDialog } from "../lists/DeletedConfigDialog";
import { ShareDialog } from "../permissions/ShareDialog";
import { useEffectiveActionsMap } from "../permissions/useEffectiveActions";
import { moveTo } from "../views/reorder";
import { FieldSettingsPanel, rowIsCustomised } from "./FieldSettingsPanel";
import {
  buildFormConfigPatch,
  prunedCondition,
  sameConditions,
  siblingForm,
  withCopiedConditions,
  type FormFieldRow,
} from "./formConfig";

/** A `FormFieldRow` plus the column's label, which the editor shows but never saves. */
interface Row extends FormFieldRow {
  display_name: string;
}

// Seed editor rows from a form's stored fields, appending any columns not yet
// in the config (so newly-added columns show up). An empty stored config means
// "all columns", which this naturally produces.
function seedRows(form: Form, columns: Column[]): Row[] {
  const byUuid = new Map(columns.map((c) => [c.uuid, c]));
  const live = new Set(byUuid.keys());
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
      condition: prunedCondition(field.condition, live),
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
      condition: null,
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
  // One panel for every row, and the index of the row it is currently showing. A
  // panel per row would mount a condition editor per column of the list.
  const settingsPanel = useRef<OverlayPanel>(null);
  const [settingsFor, setSettingsFor] = useState<number | null>(null);
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
  // worse than no control. Shown, order and label still apply. Conditions are the
  // same: they are read when a record is filled in, so a view form has none.
  const showsEnabled = activeForm?.type !== "view";
  // The other half of the add/edit pair, or null — a view or custom form has none.
  const sibling = activeForm ? siblingForm(activeForm, forms) : null;

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
    // Through `buildFormConfigPatch` rather than an object literal: the PATCH replaces
    // `config` wholesale, so anything the editor does not model has to be carried
    // across explicitly or saving the layout destroys it.
    mutationFn: () =>
      api.patch<Form>(`/lists/${listUuid}/forms/${activeForm!.uuid}`, {
        config: buildFormConfigPatch(activeForm!, rows),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["forms", listUuid] });
      toast.success("Form saved", activeForm?.display_name);
    },
  });

  /**
   * Put this form's conditions on the other half of the pair.
   *
   * Conditions are stored per form, so the add and edit forms may differ — which is
   * often what you want, and is why this is a button rather than something saving does
   * on its own. What it copies is only the conditions: which columns each form shows,
   * and in what order, is exactly what the two exist to differ on.
   *
   * It reads the *stored* sibling and the *stored* active form, so it applies what was
   * last saved. Pressing "Save layout" first is the point of the disabled state below.
   */
  const copyConditions = useMutation({
    mutationFn: async () => {
      const target = siblingForm(activeForm!, forms)!;
      await api.patch<Form>(`/lists/${listUuid}/forms/${target.uuid}`, {
        config: withCopiedConditions(target, activeForm!, columns),
      });
      return target.display_name;
    },
    onSuccess: (name) => {
      queryClient.invalidateQueries({ queryKey: ["forms", listUuid] });
      toast.success("Conditions copied", `Applied to “${name}”`);
    },
    onError: (err) =>
      toast.error(
        "Could not copy the conditions",
        err instanceof ApiError ? err.message : undefined,
      ),
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

        {/* Order + field + Shown + a gear now fits a phone, so the sideways scroll
            this block used to need has gone with the controls that caused it. */}
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2 text-sm text-muted px-1">
            {/* Indented past the drag grip so the headers sit over their columns. */}
            <span style={{ width: DRAG_GRIP_WIDTH, flexShrink: 0 }} />
            <span style={{ width: 64 }}>Order</span>
            <span className="flex-1">Field</span>
            <span style={{ width: 70 }}>Shown</span>
            <span style={{ width: 44 }} />
          </div>
          {/* Drag by the grip or step with the chevrons — and the grip is why the
              row is not itself draggable: these rows hold a checkbox and buttons,
              all of which a draggable row would swallow the pointer for.
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
                {/* The dot is what keeps a collapsed setting from being an invisible
                    one: a label override or a condition has to be readable from the
                    list, or the only way to find one is to open every row. */}
                <Button
                  icon="pi pi-cog"
                  text
                  size="small"
                  aria-label={`Settings for ${row.display_name}`}
                  className={
                    rowIsCustomised(row, showsEnabled) ? "is-on" : undefined
                  }
                  badge={rowIsCustomised(row, showsEnabled) ? " " : undefined}
                  badgeClassName="p-badge-dot"
                  onClick={(e) => {
                    setSettingsFor(i);
                    settingsPanel.current?.show(e, e.currentTarget);
                  }}
                />
              </>
            )}
          </SortableList>
          {rows.length === 0 && <p>Add a column to configure this form.</p>}
        </div>

        {/* "Save layout", not "Save" — the dialog's own footer is a few pixels
            below this one, and two buttons that both say Save do not obviously
            mean "keep this form's fields" and "close the settings". */}
        <div className="flex flex-wrap justify-end gap-2">
          {canEdit && sibling && (
            <Button
              label={`Copy conditions to “${sibling.display_name}”`}
              icon="pi pi-copy"
              text
              onClick={() => copyConditions.mutate()}
              loading={copyConditions.isPending}
              // Nothing to copy, or the two already agree. It reads the *stored*
              // forms, so unsaved edits are not what it would send — which is why
              // "Save layout" comes first and this settles afterwards.
              disabled={!activeForm || sameConditions(activeForm, sibling)}
              tooltip={
                "Conditions are stored per form. This applies the ones you last " +
                "saved here to the other form, leaving its own field layout alone."
              }
              tooltipOptions={{ position: "top" }}
            />
          )}
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

      {/* One panel, re-anchored to whichever gear was pressed. Rendered outside the
          list so a re-order cannot unmount it while it is open. */}
      <OverlayPanel ref={settingsPanel} onHide={() => setSettingsFor(null)}>
        {settingsFor !== null && rows[settingsFor] && (
          <FieldSettingsPanel
            listUuid={listUuid}
            columns={columns}
            column={columns.find(
              (c) => c.uuid === rows[settingsFor].column_uuid,
            )}
            row={rows[settingsFor]}
            showsEnabled={showsEnabled}
            disabled={!canEdit}
            onChange={(patch) => update(settingsFor, patch)}
          />
        )}
      </OverlayPanel>

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
