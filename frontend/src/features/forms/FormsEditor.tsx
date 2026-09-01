import { useEffect, useMemo, useRef, useState } from "react";
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
import { ErrorMessage } from "../../components/ErrorMessage";
import { FieldSettingsPanel } from "./FieldSettingsPanel";
import {
  buildFormConfigPatch,
  prunedCondition,
  rowIsCustomised,
  withAppliedSettings,
  type FormFieldRow,
  type FormSettingParts,
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
  const [applying, setApplying] = useState(false);
  const [parts, setParts] = useState<FormSettingParts>({
    layout: true,
    enabled: true,
    labels: true,
    conditions: true,
  });

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
  // Every other form this person may write to. "Apply to all forms" is only offered
  // when there is something to apply *to*, and it silently skipping a form somebody
  // cannot configure would be worse than not counting it.
  const others = activeForm
    ? forms.filter(
        (f) => f.uuid !== activeForm.uuid && canForm(f.uuid, "configure"),
      )
    : [];

  /**
   * Whether the editor holds edits that are not in the form yet.
   *
   * Both sides go through `buildFormConfigPatch`, so what is compared is what would be
   * *sent* rather than what is on screen: a blank label override and a stored null are
   * the same edit, and re-seeding produces the stored config exactly. It gates "Save
   * layout" the way `dirty` gates "Save view" on the toolbar, and warns before an
   * "Apply to all forms" that would send the last save rather than what is showing.
   */
  const dirty = useMemo(() => {
    if (!activeForm) return false;
    const of = (r: FormFieldRow[]) =>
      JSON.stringify(buildFormConfigPatch(activeForm, r).fields);
    return of(rows) !== of(seedRows(activeForm, columns));
  }, [activeForm, columns, rows]);

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

  // Seed the editor from the stored form — on a change of *form*, or when the set
  // of columns genuinely changes, and not merely because `columns` is a new array.
  //
  // It is a prop, so every `invalidateQueries(["columns", listUuid])` gave it a new
  // identity and re-ran this: reorder six fields and relabel two, switch to the
  // Columns tab, edit any column's display name, come back — and the layout was
  // back to the last save. Worse than losing the work quietly, `dirty` went false
  // with it, so "Save layout" greyed out and the editor looked like it had nothing
  // to save. Keying on the column *uuids* keeps the one case that has to re-seed (a
  // column added or removed changes which fields exist) and drops the one that
  // never should have.
  const columnKey = columns.map((c) => c.uuid).join(",");
  useEffect(() => {
    if (activeForm) setRows(seedRows(activeForm, columns));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeForm?.uuid, columnKey]);

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
   * Put the ticked parts of this form's settings on every other form of the list.
   *
   * It needs a dialog rather than being one button, because applying the *layout* to
   * every form is exactly the thing the add and edit forms exist to differ on — a
   * single button that did the lot would be one nobody could safely press.
   *
   * One PATCH per form, in sequence rather than at once — there is no endpoint that
   * takes them together, and a partial failure has to be reportable as one. So a form
   * that refuses is collected rather than thrown, and the toast says which: stopping at
   * the first would leave the list half-applied with nothing on screen saying where it
   * got to.
   */
  const applyToAll = useMutation({
    mutationFn: async () => {
      const done: string[] = [];
      const failed: string[] = [];
      for (const target of others) {
        try {
          await api.patch<Form>(`/lists/${listUuid}/forms/${target.uuid}`, {
            config: withAppliedSettings(target, activeForm!, parts, columns),
          });
          done.push(target.display_name);
        } catch {
          failed.push(target.display_name);
        }
      }
      return { done, failed };
    },
    onSuccess: ({ done, failed }) => {
      setApplying(false);
      queryClient.invalidateQueries({ queryKey: ["forms", listUuid] });
      if (failed.length === 0) {
        toast.success(
          `Applied to ${done.length} form${done.length === 1 ? "" : "s"}`,
          done.join(", "),
        );
      } else {
        toast.error(
          `Could not apply to ${failed.length} of ${others.length} forms`,
          `Failed: ${failed.join(", ")}.${
            done.length ? ` Applied to: ${done.join(", ")}.` : ""
          }`,
        );
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
      size="small"
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
        <ErrorMessage error={save.error} />
        <div className="flex flex-wrap items-center gap-2">
          <Dropdown
            value={activeForm?.uuid ?? null}
            options={forms.map((f) => ({
              label: `${f.display_name}${f.is_default ? " (default)" : ""}`,
              value: f.uuid,
            }))}
            onChange={(e) => setSelectedUuid(e.value)}
            // Wider than ViewBar's picker and staying that way: a form name is
            // suffixed with " (default)" and truncates at ViewBar's 12rem. The row
            // wraps "Deleted" onto a second line at the dialog's 720px, which is
            // what `flex-wrap` is here for — a truncated name is the worse trade.
            className="w-full sm:w-64"
          />
          {/* Beside the picker, where Views keeps "Save view" — the two tabs put the
              same button in the same place, and this is the only control here that
              writes anything, so it heads the row rather than trailing a list that
              can run to a screenful.

              Styled as "Save view" is, down to the size, and for the same reasons:
              outlined once there is something to save and plain text while there is
              not, because it changes what *other people* will see and should only
              draw the eye when pressing it would do that; disabled rather than
              hidden, so the "nothing to save" signal costs no discoverability; and
              inert rather than `disabled`, because a disabled button takes no
              pointer events and its tooltip is the only thing that says why — see
              "Save view" in ViewBar for what PrimeReact's `showOnDisabled` does to
              the DOM to work around that, and why nothing here may use it. A solid
              button here also wrapped the row onto two lines at the dialog's 720px. */}
          {canEdit && (
            <Button
              label="Save layout"
              icon="pi pi-save"
              outlined={dirty}
              text={!dirty}
              size="small"
              onClick={() => {
                if (rows.length === 0 || !dirty) return; // inert, not disabled
                save.mutate();
              }}
              loading={save.isPending}
              className={rows.length === 0 || !dirty ? "is-inert" : undefined}
              aria-disabled={rows.length === 0 || !dirty}
              tooltip={
                dirty
                  ? "Save this form's field order, labels and conditions"
                  : "Nothing to save — this form already looks like this"
              }
              tooltipOptions={{ position: "bottom" }}
            />
          )}
          {/* Next to the button that saves, because the two are read together: this
              sends what that one last wrote, and the dialog says so when the editor
              is holding anything it has not. */}
          {canEdit && others.length > 0 && (
            <Button
              label="Apply to all forms"
              icon="pi pi-clone"
              text
              size="small"
              onClick={() => setApplying(true)}
              tooltip={
                "Put the parts you choose of this form's saved settings on every " +
                "other form of this list."
              }
              tooltipOptions={{ position: "bottom" }}
            />
          )}
          {canEdit && activeForm && (
            <Button
              label="Rename"
              icon="pi pi-pencil"
              text
              size="small"
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
              size="small"
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
                    list, or the only way to find one is to open every row.

                    `settings-gear` and not `is-on`: that class is scoped to
                    `.toolbar .p-button`, and this row is not a toolbar, so the tint
                    that was meant to bind the dot to the gear never applied. Without
                    it — and with PrimeReact rendering a button's badge *inline*, since
                    only a `.p-overlay-badge` wrapper positions one on top — the dot sat
                    beside the gear reading as a second, unexplained control. The class
                    below does both jobs. */}
                <Button
                  icon="pi pi-cog"
                  text
                  size="small"
                  aria-label={`Settings for ${row.display_name}`}
                  className={
                    rowIsCustomised(row, showsEnabled)
                      ? "settings-gear is-set"
                      : "settings-gear"
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
          <ErrorMessage error={rename.error} />
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

      {/* Ticked parts, not one button: the field layout is what the add and edit
          forms exist to differ on, so applying it everywhere is occasionally what
          somebody means and usually not — while a relabelled field or a condition
          almost always is. One switch would have to guess which. */}
      <AppDialog
        header="Apply to all forms"
        visible={applying}
        onHide={() => setApplying(false)}
        style={{ width: 460 }}
        footer={
          <>
            <Button label="Cancel" text onClick={() => setApplying(false)} />
            <Button
              label={`Apply to ${others.length} form${
                others.length === 1 ? "" : "s"
              }`}
              icon="pi pi-clone"
              onClick={() => applyToAll.mutate()}
              loading={applyToAll.isPending}
              disabled={!Object.values(parts).some(Boolean)}
            />
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <p className="m-0">
            Apply the settings saved on “{activeForm?.display_name}” to this
            list’s other {others.length}{" "}
            {others.length === 1 ? "form" : "forms"}:{" "}
            {others.map((f) => f.display_name).join(", ")}.
          </p>

          {(
            [
              ["layout", "Field order and which fields show"],
              ["enabled", "Editable"],
              ["labels", "Label overrides"],
              ["conditions", "Conditions"],
            ] as const
          )
            // A view form displays a record and collects nothing, so it has no
            // "editable" and no conditions to give — the same two the editor hides
            // for one. `withAppliedSettings` refuses them either way; not offering
            // them is what stops somebody ticking a box that does nothing.
            .filter(([key]) => showsEnabled || (key !== "enabled" && key !== "conditions"))
            .map(([key, label]) => (
              <div key={key} className="flex items-center gap-2">
                <Checkbox
                  inputId={`apply-${key}`}
                  checked={parts[key]}
                  onChange={(e) =>
                    setParts((p) => ({ ...p, [key]: !!e.checked }))
                  }
                />
                <label htmlFor={`apply-${key}`}>{label}</label>
              </div>
            ))}

          <Message
            severity="warn"
            content={
              <span className="text-sm">
                This overwrites the ticked settings on every other form. A form
                that only displays records takes the labels and the layout; its
                “editable” and conditions are left alone.
              </span>
            }
          />

          {/* It sends the stored form, so unsaved edits are not what it would
              apply — and with "Save layout" now up beside the picker rather than
              next to this button, that is worth saying rather than implying. */}
          {dirty && (
            <Message
              severity="info"
              content={
                <span className="text-sm">
                  This form has unsaved changes. Press “Save layout” first, or
                  they will not be included.
                </span>
              }
            />
          )}
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
