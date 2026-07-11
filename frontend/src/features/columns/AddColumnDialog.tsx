import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Dialog } from "primereact/dialog";
import { InputText } from "primereact/inputtext";
import { InputNumber } from "primereact/inputnumber";
import { Dropdown } from "primereact/dropdown";
import { Checkbox } from "primereact/checkbox";
import { Button } from "primereact/button";
import { Message } from "primereact/message";

import { api, ApiError } from "../../lib/api";
import { slugify } from "../../lib/slug";
import { COLUMN_TYPE_OPTIONS } from "../../lib/columnTypes";
import type { Column, ColumnType, ListRead } from "../../lib/types";

interface Props {
  listUuid: string;
  workspaceUuid: string | null;
  visible: boolean;
  onHide: () => void;
}

export function AddColumnDialog({
  listUuid,
  workspaceUuid,
  visible,
  onHide,
}: Props) {
  const queryClient = useQueryClient();
  const [displayName, setDisplayName] = useState("");
  const [type, setType] = useState<ColumnType>("text");
  const [required, setRequired] = useState(false);
  const [maxLength, setMaxLength] = useState<number | null>(null);
  const [min, setMin] = useState<number | null>(null);
  const [max, setMax] = useState<number | null>(null);
  const [integerOnly, setIntegerOnly] = useState(false);
  const [choices, setChoices] = useState("");
  const [targetListUuid, setTargetListUuid] = useState<string | null>(null);
  const [displayColumnUuid, setDisplayColumnUuid] = useState<string | null>(null);
  const [multiple, setMultiple] = useState(false);

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

  function reset() {
    setDisplayName("");
    setType("text");
    setRequired(false);
    setMaxLength(null);
    setMin(null);
    setMax(null);
    setIntegerOnly(false);
    setChoices("");
    setTargetListUuid(null);
    setDisplayColumnUuid(null);
    setMultiple(false);
  }

  const create = useMutation({
    mutationFn: () => {
      const config: Record<string, unknown> = {};
      if (required) config.required = true;
      if ((type === "text" || type === "long_text") && maxLength != null) {
        config.max_length = maxLength;
      }
      if (type === "number") {
        if (min != null) config.min = min;
        if (max != null) config.max = max;
        if (integerOnly) config.integer_only = true;
      }
      if (type === "choice") config.choices = choices;
      if (type === "lookup") {
        config.target_list_uuid = targetListUuid;
        if (displayColumnUuid) config.display_column_uuid = displayColumnUuid;
        if (multiple) config.multiple = true;
      }
      return api.post<Column>(`/lists/${listUuid}/columns/`, {
        name: slugify(displayName),
        display_name: displayName,
        type,
        config,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["columns", listUuid] });
      reset();
      onHide();
    },
  });

  return (
    <Dialog
      header="Add column"
      visible={visible}
      onHide={onHide}
      style={{ width: 420 }}
    >
      <div className="flex flex-col gap-4">
        {create.error instanceof ApiError && (
          <Message severity="error" text={create.error.message} />
        )}
        <div className="flex flex-col gap-2">
          <label htmlFor="col-name">Name</label>
          <InputText
            id="col-name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            autoFocus
          />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor="col-type">Type</label>
          <Dropdown
            inputId="col-type"
            value={type}
            options={COLUMN_TYPE_OPTIONS}
            onChange={(e) => setType(e.value as ColumnType)}
          />
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
        )}
        {type === "number" && (
          <div className="flex items-center gap-2">
            <Checkbox
              inputId="col-int"
              checked={integerOnly}
              onChange={(e) => setIntegerOnly(!!e.checked)}
            />
            <label htmlFor="col-int">Integers only</label>
          </div>
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
            label="Add"
            onClick={() => create.mutate()}
            loading={create.isPending}
            disabled={
              !displayName.trim() || (type === "lookup" && !targetListUuid)
            }
          />
        </div>
      </div>
    </Dialog>
  );
}
