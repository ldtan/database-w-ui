import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Dialog } from "primereact/dialog";
import { InputText } from "primereact/inputtext";
import { InputNumber } from "primereact/inputnumber";
import { Dropdown } from "primereact/dropdown";
import { Checkbox } from "primereact/checkbox";
import { Button } from "primereact/button";
import { Message } from "primereact/message";
import { confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { NameFields } from "../../components/NameFields";
import { isValidName } from "../../lib/slug";
import { COLUMN_TYPE_OPTIONS } from "../../lib/columnTypes";
import { useToast } from "../../lib/toast";
import type { ChoiceOption, Column, ColumnType, ListRead } from "../../lib/types";

interface Props {
  listUuid: string;
  workspaceUuid: string | null;
  // null => create a new column; otherwise edit this one.
  column: Column | null;
  visible: boolean;
  onHide: () => void;
}

/** Add or modify a column. The two are one dialog because they take the same
 *  settings — with one exception the API enforces: a column's **type is immutable**
 *  once records hold values shaped by it, so it is shown but locked when editing. */
export function ColumnDialog({
  listUuid,
  workspaceUuid,
  column,
  visible,
  onHide,
}: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const editing = column !== null;

  const [displayName, setDisplayName] = useState("");
  const [name, setName] = useState("");
  const [type, setType] = useState<ColumnType>("text");
  const [required, setRequired] = useState(false);
  const [maxLength, setMaxLength] = useState<number | null>(null);
  const [min, setMin] = useState<number | null>(null);
  const [max, setMax] = useState<number | null>(null);
  const [integerOnly, setIntegerOnly] = useState(false);
  const [choices, setChoices] = useState("");
  const [linkText, setLinkText] = useState("");
  const [targetListUuid, setTargetListUuid] = useState<string | null>(null);
  const [displayColumnUuid, setDisplayColumnUuid] = useState<string | null>(null);
  const [multiple, setMultiple] = useState(false);

  // Seed the form from the column being edited (or clear it for a new one) each
  // time the dialog opens.
  useEffect(() => {
    if (!visible) return;
    if (!column) {
      setDisplayName("");
      setName("");
      setType("text");
      setRequired(false);
      setMaxLength(null);
      setMin(null);
      setMax(null);
      setIntegerOnly(false);
      setChoices("");
      setLinkText("");
      setTargetListUuid(null);
      setDisplayColumnUuid(null);
      setMultiple(false);
      return;
    }
    const config = column.config ?? {};
    setDisplayName(column.display_name);
    setName(column.name);
    setType(column.type);
    setRequired(!!config.required);
    setMaxLength((config.max_length as number) ?? null);
    setMin((config.min as number) ?? null);
    setMax((config.max as number) ?? null);
    setIntegerOnly(!!config.integer_only);
    // Options carry stable ids; resend them as labels so unchanged ones keep theirs.
    setChoices(
      ((config.options as ChoiceOption[]) ?? []).map((o) => o.label).join(";"),
    );
    setLinkText((config.label as string) ?? "");
    setTargetListUuid((config.target_list_uuid as string) ?? null);
    setDisplayColumnUuid((config.display_column_uuid as string) ?? null);
    setMultiple(!!config.multiple);
  }, [visible, column]);

  // Lists in this workspace are the candidate lookup targets (self included).
  const lists = useQuery({
    queryKey: ["lists", workspaceUuid],
    queryFn: () =>
      api.get<ListRead[]>(`/lists/?workspace_uuid=${workspaceUuid}`),
    enabled: type === "lookup" && !!workspaceUuid,
  });
  // Columns of the chosen target list, offered as the label to show per row.
  const targetColumns = useQuery({
    queryKey: ["columns", targetListUuid],
    queryFn: () => api.get<Column[]>(`/lists/${targetListUuid}/columns/`),
    enabled: type === "lookup" && !!targetListUuid,
  });

  const save = useMutation({
    mutationFn: () => {
      const config: Record<string, unknown> = { required };
      if (type === "text" || type === "long_text") {
        config.max_length = maxLength;
      }
      if (type === "number") {
        config.min = min;
        config.max = max;
        config.integer_only = integerOnly;
      }
      if (type === "choice") config.choices = choices;
      if (type === "link") config.label = linkText.trim() || null;
      if (type === "user" || type === "group") config.multiple = multiple;
      if (type === "lookup") {
        config.target_list_uuid = targetListUuid;
        config.display_column_uuid = displayColumnUuid;
        config.multiple = multiple;
      }

      if (editing) {
        return api.patch<Column>(`/lists/${listUuid}/columns/${column.uuid}`, {
          name,
          display_name: displayName,
          config,
        });
      }
      return api.post<Column>(`/lists/${listUuid}/columns/`, {
        name,
        display_name: displayName,
        type,
        config,
      });
    },
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: ["columns", listUuid] });
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      // Turning `multiple` off rewrites records, and the labels a lookup/user cell
      // renders are keyed on the values it holds.
      queryClient.invalidateQueries({ queryKey: ["lookup-labels"] });
      queryClient.invalidateQueries({ queryKey: ["principal-labels"] });
      toast.success(editing ? "Column updated" : "Column added", saved.display_name);
      onHide();
    },
  });

  // Going from several values to one is the only edit here that loses data, so it
  // asks first. The records are not rewritten on the spot (on a large list that
  // would be a multi-minute lock inside this request) — the extra values are hidden
  // immediately and cleared out later, which is exactly what the message promises.
  function onSave() {
    const losingValues = editing && !!column.config.multiple && !multiple;
    if (!losingValues) {
      save.mutate();
      return;
    }
    confirmDialog({
      header: "Keep only the first value?",
      icon: "pi pi-exclamation-triangle",
      message: `“${column.display_name}” currently allows several values. Records holding more than one will show only the first from now on, and the extra values are removed for good the next time the records are edited or cleaned up. Switching back to multiple before then restores them.`,
      acceptLabel: "Keep first value",
      acceptClassName: "p-button-danger",
      rejectLabel: "Cancel",
      accept: () => save.mutate(),
    });
  }

  return (
    <Dialog
      header={editing ? `Edit “${column.display_name}”` : "Add column"}
      visible={visible}
      onHide={onHide}
      style={{ width: 420 }}
    >
      <div className="flex flex-col gap-4">
        {save.error instanceof ApiError && (
          <Message severity="error" text={save.error.message} />
        )}
        <NameFields
          key={`${column?.uuid ?? "new"}:${visible}`}
          idPrefix="col"
          value={{ displayName, name }}
          onChange={(next) => {
            setDisplayName(next.displayName);
            setName(next.name);
          }}
          linked={!editing}
          autoFocus
        />
        <div className="flex flex-col gap-2">
          <label htmlFor="col-type">Type</label>
          <Dropdown
            inputId="col-type"
            value={type}
            options={COLUMN_TYPE_OPTIONS}
            onChange={(e) => setType(e.value as ColumnType)}
            disabled={editing}
          />
          {editing && (
            <small style={{ color: "var(--text-muted)" }}>
              A column's type cannot change: existing records already hold values in
              its shape.
            </small>
          )}
        </div>

        {(type === "text" || type === "long_text") && (
          <div className="flex flex-col gap-2">
            <label htmlFor="col-maxlen">Max length (optional)</label>
            <InputNumber
              inputId="col-maxlen"
              value={maxLength}
              onValueChange={(e) => setMaxLength(e.value ?? null)}
              min={1}
              useGrouping={false}
            />
          </div>
        )}

        {type === "number" && (
          <>
            <div className="flex gap-3">
              <div className="flex flex-col gap-2 flex-1">
                <label htmlFor="col-min">Min</label>
                <InputNumber
                  inputId="col-min"
                  value={min}
                  onValueChange={(e) => setMin(e.value ?? null)}
                  useGrouping={false}
                />
              </div>
              <div className="flex flex-col gap-2 flex-1">
                <label htmlFor="col-max">Max</label>
                <InputNumber
                  inputId="col-max"
                  value={max}
                  onValueChange={(e) => setMax(e.value ?? null)}
                  useGrouping={false}
                />
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                inputId="col-int"
                checked={integerOnly}
                onChange={(e) => setIntegerOnly(!!e.checked)}
              />
              <label htmlFor="col-int">Integers only</label>
            </div>
          </>
        )}

        {type === "choice" && (
          <div className="flex flex-col gap-2">
            <label htmlFor="col-choices">Choices (separated by ";")</label>
            <InputText
              id="col-choices"
              value={choices}
              onChange={(e) => setChoices(e.target.value)}
              placeholder="Todo;Doing;Done"
            />
            {editing && (
              <small style={{ color: "var(--text-muted)" }}>
                Renaming an option keeps records pointing at it; removing one leaves
                records holding a value the column no longer offers.
              </small>
            )}
          </div>
        )}

        {type === "link" && (
          <div className="flex flex-col gap-2">
            <label htmlFor="col-linktext">Link text (optional)</label>
            <InputText
              id="col-linktext"
              value={linkText}
              onChange={(e) => setLinkText(e.target.value)}
              placeholder="Shown instead of the URL"
            />
          </div>
        )}

        {(type === "user" || type === "group") && (
          <div className="flex items-center gap-2">
            <Checkbox
              inputId="col-multi-principal"
              checked={multiple}
              onChange={(e) => setMultiple(!!e.checked)}
            />
            <label htmlFor="col-multi-principal">
              Allow selecting multiple {type === "user" ? "users" : "groups"}
            </label>
          </div>
        )}

        {type === "lookup" && (
          <>
            <div className="flex flex-col gap-2">
              <label htmlFor="col-target">Target list</label>
              <Dropdown
                inputId="col-target"
                value={targetListUuid}
                options={(lists.data ?? []).map((l) => ({
                  label: l.display_name,
                  value: l.uuid,
                }))}
                onChange={(e) => {
                  setTargetListUuid(e.value);
                  setDisplayColumnUuid(null); // columns differ per target list
                }}
                placeholder={lists.isLoading ? "Loading…" : "Select a list…"}
                filter
              />
            </div>
            {targetListUuid && (
              <div className="flex flex-col gap-2">
                <label htmlFor="col-display">Column to show</label>
                <Dropdown
                  inputId="col-display"
                  value={displayColumnUuid}
                  options={(targetColumns.data ?? []).map((c) => ({
                    label: c.display_name,
                    value: c.uuid,
                  }))}
                  onChange={(e) => setDisplayColumnUuid(e.value)}
                  placeholder={
                    targetColumns.isLoading ? "Loading…" : "Primary column"
                  }
                  showClear
                  filter
                />
              </div>
            )}
            <div className="flex items-center gap-2">
              <Checkbox
                inputId="col-multiple"
                checked={multiple}
                onChange={(e) => setMultiple(!!e.checked)}
              />
              <label htmlFor="col-multiple">Allow linking multiple rows</label>
            </div>
          </>
        )}

        <div className="flex items-center gap-2">
          <Checkbox
            inputId="col-required"
            checked={required}
            onChange={(e) => setRequired(!!e.checked)}
          />
          <label htmlFor="col-required">Required</label>
        </div>

        <div className="flex justify-end gap-2">
          <Button label="Cancel" text onClick={onHide} />
          <Button
            label={editing ? "Save" : "Add"}
            onClick={onSave}
            loading={save.isPending}
            disabled={
              !displayName.trim() ||
              !isValidName(name) ||
              (type === "lookup" && !targetListUuid)
            }
          />
        </div>
      </div>
    </Dialog>
  );
}
