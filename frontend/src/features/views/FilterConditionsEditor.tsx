import { Calendar } from "primereact/calendar";
import { Dropdown } from "primereact/dropdown";
import { InputText } from "primereact/inputtext";
import { InputNumber } from "primereact/inputnumber";
import { SelectButton } from "primereact/selectbutton";
import { Button } from "primereact/button";

import { isTemporal, parseTemporal, serializeTemporal } from "../../lib/temporal";
import type { Column, FilterCondition } from "../../lib/types";
import { LookupFilterValue } from "../lookups/LookupControls";
import { PrincipalFilterValue } from "../principals/PrincipalControls";

export const OPERATORS = [
  { label: "is", value: "is" },
  { label: "is not", value: "is_not" },
  { label: "contains", value: "contains" },
  { label: "does not contain", value: "does_not_contain" },
  { label: "is empty", value: "is_empty" },
  { label: "is not empty", value: "is_not_empty" },
  { label: ">", value: ">" },
  { label: "≥", value: ">=" },
  { label: "<", value: "<" },
  { label: "≤", value: "<=" },
];

export const NO_VALUE_OPS = new Set(["is_empty", "is_not_empty"]);

// Drop conditions that are incomplete so they don't get sent to the server.
export function cleanConditions(conditions: FilterCondition[]): FilterCondition[] {
  return conditions.filter(
    (c) =>
      c.column_uuid &&
      c.operator &&
      (NO_VALUE_OPS.has(c.operator) ||
        (c.value !== undefined && c.value !== null && c.value !== "")),
  );
}

interface Props {
  listUuid: string;
  columns: Column[];
  conjunction: "and" | "or";
  onConjunctionChange: (value: "and" | "or") => void;
  conditions: FilterCondition[];
  onConditionsChange: (conditions: FilterCondition[]) => void;
}

/**
 * The rows of a filter: column, operator, value — joined by one and/or.
 *
 * Shared by the view's Filter dialog and the export dialog, which filters what it
 * exports with exactly the same shape (the backend takes the same `filters` object
 * either way), so this is the one place that knows what a condition looks like.
 *
 * The value input follows the column's type, and for reference columns that is not
 * a nicety: a lookup, user or group cell stores a *uuid*, so a text box could only
 * ever be filled by pasting one. Those get a picker, and a near-miss in a text box
 * silently matching nothing is why temporal columns get a calendar.
 */
export function FilterConditionsEditor({
  listUuid,
  columns,
  conjunction,
  onConjunctionChange,
  conditions,
  onConditionsChange,
}: Props) {
  function update(index: number, patch: Partial<FilterCondition>) {
    onConditionsChange(
      conditions.map((c, i) => (i === index ? { ...c, ...patch } : c)),
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {conditions.length > 1 && (
        <SelectButton
          value={conjunction}
          options={[
            { label: "Match all", value: "and" },
            { label: "Match any", value: "or" },
          ]}
          onChange={(e) => e.value && onConjunctionChange(e.value)}
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
          // Wraps rather than overflows: column + operator + value + remove is more
          // than a phone's width, so on one it becomes two lines.
          <div key={i} className="flex flex-wrap items-center gap-2">
            <Dropdown
              value={cond.column_uuid}
              options={columns.map((c) => ({
                label: c.display_name,
                value: c.uuid,
              }))}
              onChange={(e) => update(i, { column_uuid: e.value })}
              placeholder="Column"
              className="flex-1 min-w-40"
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
              ) : col?.type === "user" || col?.type === "group" ? (
                // Cells store a uuid, so a text box here could only ever be
                // filled by pasting one — pick the principal instead.
                <PrincipalFilterValue
                  column={col}
                  value={cond.value}
                  onChange={(v) => update(i, { value: v })}
                />
              ) : col?.type === "lookup" ? (
                // Same: a lookup cell stores a target-row uuid, not its label.
                <LookupFilterValue
                  listUuid={listUuid}
                  column={col}
                  value={cond.value}
                  onChange={(v) => update(i, { value: v })}
                />
              ) : col && isTemporal(col.type) ? (
                // Otherwise this is a bare text box: you would have to hand-type the
                // exact stored format, and a near-miss silently matches nothing.
                <Calendar
                  value={parseTemporal(col.type, cond.value)}
                  onChange={(e) =>
                    update(i, {
                      value: e.value
                        ? serializeTemporal(col.type, e.value as Date)
                        : null,
                    })
                  }
                  dateFormat="yy-mm-dd"
                  timeOnly={col.type === "time"}
                  showTime={col.type === "datetime"}
                  showButtonBar
                  style={{ width: 170 }}
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
                onConditionsChange(conditions.filter((_, j) => j !== i))
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
            onConditionsChange([
              ...conditions,
              { column_uuid: "", operator: "is", value: "" },
            ])
          }
          disabled={columns.length === 0}
        />
      </div>
    </div>
  );
}
