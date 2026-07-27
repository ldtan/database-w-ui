/**
 * The inputs a record's fields are edited with, and the block that lays them out.
 *
 * These live apart from `RecordFormDialog` for the same reason `recordValues.ts`
 * does: more than one surface now edits a record's fields — the Add/Edit dialog,
 * and the values a QR scanning session fixes for every scan — and the two agreeing
 * about what a `datetime` column's editor is, or about what a changed parent lookup
 * does to the lookups nested under it, has to come from shared code rather than
 * from two copies kept in step by hand.
 *
 * Nothing here decides *which* fields to show. That is `resolveFields`' job, from
 * the list's form; a caller passes the result in.
 */
import { Calendar } from "primereact/calendar";
import { Checkbox } from "primereact/checkbox";
import { Dropdown } from "primereact/dropdown";
import { InputNumber } from "primereact/inputnumber";
import { InputText } from "primereact/inputtext";
import { InputTextarea } from "primereact/inputtextarea";

import type { Column } from "../../lib/types";
import { LookupField } from "../lookups/LookupControls";
import { PrincipalField } from "../principals/PrincipalControls";
import { nestedDescendants, type ResolvedField } from "./recordValues";

export function RecordField({
  listUuid,
  column,
  value,
  disabled,
  onChange,
  columns,
  values,
}: {
  listUuid: string;
  column: Column;
  value: unknown;
  disabled: boolean;
  onChange: (v: unknown) => void;
  /** The full column set and current draft, for editors whose behavior depends
   *  on a *sibling* cell — today, a nested lookup filtering by its parent. */
  columns?: Column[];
  values?: Record<string, unknown>;
}) {
  const id = `field-${column.uuid}`;
  switch (column.type) {
    case "long_text":
      return (
        <InputTextarea
          id={id}
          value={(value as string) ?? ""}
          onChange={(e) => onChange(e.target.value)}
          rows={3}
          disabled={disabled}
        />
      );
    case "number":
      return (
        <InputNumber
          inputId={id}
          value={(value as number) ?? null}
          onValueChange={(e) => onChange(e.value ?? null)}
          useGrouping={false}
          disabled={disabled}
        />
      );
    case "checkbox":
      return (
        <Checkbox
          inputId={id}
          checked={!!value}
          onChange={(e) => onChange(!!e.checked)}
          disabled={disabled}
        />
      );
    case "choice": {
      // Options are the values: a cell stores the label itself.
      const options = (column.config.options ?? []).map((o) => ({
        label: o,
        value: o,
      }));
      return (
        <Dropdown
          inputId={id}
          value={(value as string) ?? null}
          options={options}
          onChange={(e) => onChange(e.value)}
          showClear
          placeholder="Select…"
          disabled={disabled}
        />
      );
    }
    case "date":
      return (
        <Calendar
          inputId={id}
          value={(value as Date) ?? null}
          onChange={(e) => onChange(e.value ?? null)}
          dateFormat="yy-mm-dd"
          showButtonBar
          disabled={disabled}
        />
      );
    case "time":
      return (
        <Calendar
          inputId={id}
          value={(value as Date) ?? null}
          onChange={(e) => onChange(e.value ?? null)}
          timeOnly
          disabled={disabled}
        />
      );
    case "datetime":
      return (
        <Calendar
          inputId={id}
          value={(value as Date) ?? null}
          onChange={(e) => onChange(e.value ?? null)}
          showTime
          dateFormat="yy-mm-dd"
          disabled={disabled}
        />
      );
    case "link":
      return (
        <InputText
          id={id}
          type="url"
          value={(value as string) ?? ""}
          onChange={(e) => onChange(e.target.value)}
          placeholder="https://example.com"
          disabled={disabled}
        />
      );
    case "lookup": {
      // Nested lookup: the parent's *draft* value (not the stored one) drives
      // what this picker may offer. A pairing whose parent column is gone
      // degrades to a plain picker, matching the backend's tolerance.
      const parentUuid = column.config.parent_column_uuid;
      const parent = parentUuid
        ? columns?.find((c) => c.uuid === parentUuid && c.type === "lookup")
        : undefined;
      let parentValues: string[] | undefined;
      if (parent && values) {
        const raw = values[parent.uuid];
        parentValues = Array.isArray(raw)
          ? raw.map(String)
          : raw
            ? [String(raw)]
            : [];
      }
      return (
        <LookupField
          listUuid={listUuid}
          column={column}
          value={value}
          disabled={disabled}
          onChange={onChange}
          parentValues={parentValues}
          parentLabel={parent?.display_name}
        />
      );
    }
    case "user":
    case "group":
      return (
        <PrincipalField
          column={column}
          value={value}
          disabled={disabled}
          onChange={onChange}
        />
      );
    default:
      return (
        <InputText
          id={id}
          value={(value as string) ?? ""}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
        />
      );
  }
}

/**
 * Every field of a form, labelled, in order.
 *
 * The one piece of behavior here beyond layout: changing a value clears every
 * lookup nested under that column, transitively — those selections were chosen
 * against the old parent and no longer mean anything. Any surface that edits
 * fields owes a record that, which is precisely why it is not left to callers.
 */
export function RecordFields({
  listUuid,
  fields,
  columns,
  values,
  onChange,
}: {
  listUuid: string;
  fields: ResolvedField[];
  columns: Column[];
  values: Record<string, unknown>;
  onChange: (values: Record<string, unknown>) => void;
}) {
  return (
    <>
      {fields.map(({ column, enabled, label }) => (
        <div key={column.uuid} className="flex flex-col gap-2">
          <label htmlFor={`field-${column.uuid}`}>
            {label}
            {column.config.required ? " *" : ""}
          </label>
          <RecordField
            listUuid={listUuid}
            column={column}
            value={values[column.uuid]}
            disabled={!enabled}
            onChange={(v) => {
              const next = { ...values, [column.uuid]: v };
              for (const uuid of nestedDescendants(columns, column.uuid)) {
                next[uuid] = null;
              }
              onChange(next);
            }}
            columns={columns}
            values={values}
          />
        </div>
      ))}
    </>
  );
}
