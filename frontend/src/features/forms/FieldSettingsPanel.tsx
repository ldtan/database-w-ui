/**
 * Everything a form does with one field, apart from whether it shows it at all.
 *
 * These used to sit on the row itself — a checkbox, a second checkbox, a text box —
 * which was already 460px of columns that had to stay in step with a header, and
 * scrolled sideways to fit. A condition builder would not have fitted at any width, and
 * more to the point it should not have to: reordering fields is what the list is for,
 * and the settings are what you open one row to change.
 *
 * So the row keeps Order, Field and Shown, and this opens over it.
 */
import { Checkbox } from "primereact/checkbox";
import { Dropdown } from "primereact/dropdown";
import { InputText } from "primereact/inputtext";
import { Message } from "primereact/message";

import { Field } from "../../components/Field";
import type { Column, ConditionEffect, FieldCondition } from "../../lib/types";
import { FilterConditionsEditor } from "../views/FilterConditionsEditor";
import { settledCondition, type FormFieldRow } from "./formConfig";

const EFFECTS = [
  { label: "Hide the field", value: "hide" },
  { label: "Show it, but not for editing", value: "disable" },
];

/** Does this row hold anything other than the defaults? Drives the dot on the gear,
 *  which is what keeps a collapsed setting from being an invisible one. */
export function rowIsCustomised(
  row: FormFieldRow,
  showsEnabled: boolean,
): boolean {
  return (
    row.label_override.trim() !== "" ||
    (showsEnabled && !row.enabled) ||
    settledCondition(row.condition) !== null
  );
}

interface Props {
  listUuid: string;
  /** Every column of this list, including this row's own — filtered below. */
  columns: Column[];
  column: Column | undefined;
  row: FormFieldRow;
  /** False on a view form, which displays a record and collects nothing. */
  showsEnabled: boolean;
  disabled: boolean;
  onChange: (patch: Partial<FormFieldRow>) => void;
}

export function FieldSettingsPanel({
  listUuid,
  columns,
  column,
  row,
  showsEnabled,
  disabled,
  onChange,
}: Props) {
  // A condition testing the field's own column is refused on save: the field would
  // hide on its own value, keep that value, and never come back. Not offering it
  // beats explaining it.
  const testable = columns.filter((c) => c.uuid !== row.column_uuid);
  const condition = row.condition;
  const conditions = condition?.when?.conditions ?? [];
  const required = !!column?.config.required;
  const label = column?.display_name ?? "This field";

  function setCondition(next: Partial<FieldCondition>) {
    onChange({
      condition: {
        when: condition?.when ?? { conjunction: "and", conditions: [] },
        effect: condition?.effect ?? "hide",
        ...next,
      },
    });
  }

  return (
    <div
      className="flex flex-col gap-4"
      style={{ width: 420, maxWidth: "80vw" }}
    >
      <div className="field-label">{label}</div>

      {showsEnabled && (
        <Field label="Editable">
          <div className="flex items-center gap-2">
            <Checkbox
              inputId={`fs-enabled-${row.column_uuid}`}
              checked={row.enabled}
              disabled={disabled || !row.included}
              onChange={(e) => onChange({ enabled: !!e.checked })}
            />
            <label htmlFor={`fs-enabled-${row.column_uuid}`}>
              People filling this form may change it
            </label>
          </div>
        </Field>
      )}

      <Field label="Label" htmlFor={`fs-label-${row.column_uuid}`} optional>
        <InputText
          id={`fs-label-${row.column_uuid}`}
          value={row.label_override}
          onChange={(e) => onChange({ label_override: e.target.value })}
          placeholder={label}
          disabled={disabled}
          className="w-full"
        />
      </Field>

      {showsEnabled && (
        <div className="flex flex-col gap-2 border-t pt-3">
          <span className="field-label">Only apply this field when…</span>
          <FilterConditionsEditor
            listUuid={listUuid}
            columns={testable}
            conjunction={condition?.when?.conjunction ?? "and"}
            onConjunctionChange={(conjunction) =>
              setCondition({
                when: {
                  conjunction,
                  conditions,
                },
              })
            }
            conditions={conditions}
            onConditionsChange={(next) =>
              setCondition({
                when: {
                  conjunction: condition?.when?.conjunction ?? "and",
                  conditions: next,
                },
              })
            }
          />

          {conditions.length > 0 && (
            <Field label="When it does not apply">
              <Dropdown
                value={condition?.effect ?? "hide"}
                options={EFFECTS}
                disabled={disabled}
                onChange={(e) =>
                  setCondition({ effect: e.value as ConditionEffect })
                }
                className="w-full"
              />
            </Field>
          )}

          {/* The line this feature cannot cross, said where the decision is made.
              `required` is enforced by the write path, which never looks at a form —
              so a hidden required column is still required, and the record simply
              cannot be saved. */}
          {required && conditions.length > 0 && (
            <Message
              severity="warn"
              content={
                <span className="text-sm">
                  This column is required. While the condition is unmet the
                  record cannot be saved at all, unless the column has a default
                  value.
                </span>
              }
            />
          )}

          <small className="field-hint">
            Conditions decide what the add and edit forms show. They are not a
            restriction: the value can still be set by importing a file,
            scanning a code, or editing the cell in the grid.
          </small>
        </div>
      )}
    </div>
  );
}
