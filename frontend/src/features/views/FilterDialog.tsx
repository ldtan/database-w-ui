import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Dialog } from "primereact/dialog";
import { Dropdown } from "primereact/dropdown";
import { InputText } from "primereact/inputtext";
import { InputNumber } from "primereact/inputnumber";
import { SelectButton } from "primereact/selectbutton";
import { Button } from "primereact/button";
import { Message } from "primereact/message";

import { api, ApiError } from "../../lib/api";
import type { Column, FilterCondition, View, ViewConfig } from "../../lib/types";

const OPERATORS = [
  { label: "is", value: "is" },
  { label: "is not", value: "is_not" },
  { label: "contains", value: "contains" },
  { label: "is empty", value: "is_empty" },
  { label: "is not empty", value: "is_not_empty" },
  { label: ">", value: ">" },
  { label: "≥", value: ">=" },
  { label: "<", value: "<" },
  { label: "≤", value: "<=" },
];

const NO_VALUE_OPS = new Set(["is_empty", "is_not_empty"]);

interface Props {
  listUuid: string;
  viewUuid: string;
  columns: Column[];
  baseConfig: ViewConfig;
  visible: boolean;
  onHide: () => void;
}

export function FilterDialog({
  listUuid,
  viewUuid,
  columns,
  baseConfig,
  visible,
  onHide,
}: Props) {
  const queryClient = useQueryClient();
  const [conjunction, setConjunction] = useState<"and" | "or">("and");
  const [conditions, setConditions] = useState<FilterCondition[]>([]);

  useEffect(() => {
    if (!visible) return;
    setConjunction(baseConfig.filters?.conjunction ?? "and");
    setConditions(baseConfig.filters?.conditions ?? []);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible]);

  const apply = useMutation({
    mutationFn: () => {
      const cleaned = conditions.filter(
        (c) =>
          c.column_uuid &&
          c.operator &&
          (NO_VALUE_OPS.has(c.operator) ||
            (c.value !== undefined && c.value !== null && c.value !== "")),
      );
      const config: ViewConfig = {
        ...baseConfig,
        filters: { conjunction, conditions: cleaned },
      };
      return api.patch<View>(`/lists/${listUuid}/views/${viewUuid}`, { config });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["views", listUuid] });
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      onHide();
    },
  });

  function update(index: number, patch: Partial<FilterCondition>) {
    setConditions((prev) =>
      prev.map((c, i) => (i === index ? { ...c, ...patch } : c)),
    );
  }

  return (
    <Dialog
      header="Filter"
      visible={visible}
      onHide={onHide}
      style={{ width: 560 }}
    >
      <div className="flex flex-col gap-3">
        {apply.error instanceof ApiError && (
          <Message severity="error" text={apply.error.message} />
        )}
        {conditions.length > 1 && (
          <SelectButton
            value={conjunction}
            options={[
              { label: "Match all", value: "and" },
              { label: "Match any", value: "or" },
            ]}
            onChange={(e) => e.value && setConjunction(e.value)}
          />
        )}

        {conditions.map((cond, i) => {
          const col = columns.find((c) => c.uuid === cond.column_uuid);
          const needsValue = !NO_VALUE_OPS.has(cond.operator);
          const options = (col?.config.options ?? []).map((o) => ({
            label: o.label,
            value: o.id,
          }));
          return (
            <div key={i} className="flex items-center gap-2">
              <Dropdown
                value={cond.column_uuid}
                options={columns.map((c) => ({
                  label: c.display_name,
                  value: c.uuid,
                }))}
                onChange={(e) => update(i, { column_uuid: e.value })}
                placeholder="Column"
                className="flex-1"
              />
              <Dropdown
                value={cond.operator}
                options={OPERATORS}
                onChange={(e) => update(i, { operator: e.value })}
                placeholder="Op"
                style={{ width: 120 }}
              />
              {needsValue &&
                (col?.type === "number" ? (
                  <InputNumber
                    value={(cond.value as number) ?? null}
                    onValueChange={(e) => update(i, { value: e.value ?? null })}
                    useGrouping={false}
                    style={{ width: 120 }}
                  />
                ) : col?.type === "choice" ? (
                  <Dropdown
                    value={(cond.value as string) ?? null}
                    options={options}
                    onChange={(e) => update(i, { value: e.value })}
                    placeholder="Value"
                    style={{ width: 120 }}
                  />
                ) : (
                  <InputText
                    value={(cond.value as string) ?? ""}
                    onChange={(e) => update(i, { value: e.target.value })}
                    style={{ width: 120 }}
                  />
                ))}
              <Button
                icon="pi pi-times"
                text
                severity="danger"
                aria-label="Remove condition"
                onClick={() =>
                  setConditions((prev) => prev.filter((_, j) => j !== i))
                }
              />
            </div>
          );
        })}

        <div>
          <Button
            label="Add condition"
            icon="pi pi-plus"
            text
            onClick={() =>
              setConditions((prev) => [
                ...prev,
                { column_uuid: "", operator: "is", value: "" },
              ])
            }
            disabled={columns.length === 0}
          />
        </div>

        <div className="flex justify-end gap-2">
          <Button label="Cancel" text onClick={onHide} />
          <Button
            label="Apply"
            onClick={() => apply.mutate()}
            loading={apply.isPending}
          />
        </div>
      </div>
    </Dialog>
  );
}
