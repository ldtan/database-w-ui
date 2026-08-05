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
import { useState } from "react";

import { Calendar } from "primereact/calendar";
import { Checkbox } from "primereact/checkbox";
import { Dropdown } from "primereact/dropdown";
import { Editor } from "primereact/editor";
import { InputNumber } from "primereact/inputnumber";
import { InputText } from "primereact/inputtext";
import { InputTextarea } from "primereact/inputtextarea";

import type { Column } from "../../lib/types";
import { LookupField } from "../lookups/LookupControls";
import { PrincipalField } from "../principals/PrincipalControls";
import { clearedNested, textMode, type ResolvedField } from "./recordValues";

/**
 * The rich text toolbar, spelled out rather than left to Quill's default.
 *
 * Quill's default offers colours, fonts, sizes, images and embeds. Every one of
 * those is either a tag the sanitiser drops on the way in — so the button would
 * appear to work and then silently lose the formatting — or, for images, a way
 * to paste a megabyte of base64 into a JSONB cell. This is exactly the set that
 * survives ALLOWED_TAGS in columns/richtext.py, and the two must be kept in step.
 */
const RICH_TOOLBAR = (
  <>
    {/* Grouped so that on a narrow dialog the toolbar wraps between groups
        rather than leaving one button stranded on a line of its own. */}
    <span className="ql-formats">
      <select className="ql-header" defaultValue="">
        <option value="1" />
        <option value="2" />
        <option value="3" />
        <option value="" />
      </select>
    </span>
    <span className="ql-formats">
      <button aria-label="Bold" className="ql-bold" />
      <button aria-label="Italic" className="ql-italic" />
      <button aria-label="Underline" className="ql-underline" />
      <button aria-label="Strikethrough" className="ql-strike" />
    </span>
    <span className="ql-formats">
      <button aria-label="Bulleted list" className="ql-list" value="bullet" />
      <button aria-label="Numbered list" className="ql-list" value="ordered" />
      <button aria-label="Quote" className="ql-blockquote" />
      {/* ql-code (inline `<code>`), not ql-code-block: a Quill code block is a
          pair of nested divs carrying their meaning in class names, and the
          sanitiser drops both — that button would lose its formatting on save. */}
      <button aria-label="Code" className="ql-code" />
    </span>
    <span className="ql-formats">
      <button aria-label="Link" className="ql-link" />
      <button aria-label="Remove formatting" className="ql-clean" />
    </span>
  </>
);

/**
 * The dropdown entry standing for "none of these". Not a value anything stores:
 * picking it empties the cell, and what fills it again is whatever gets typed in
 * the box it reveals. A control character so it can never collide with a real
 * option — those are trimmed, non-empty labels.
 */
const OTHER = "\u0000other";

/**
 * A choice cell: the options, plus — when the column allows it — an answer of the
 * record's own.
 *
 * An "other" answer is stored as the typed string itself, exactly as an option is
 * (D3 in the data-model doc), so nothing on disk marks one. That is what makes it
 * sort, filter, export and search like any other value, and it is why this control
 * recognises one by *not finding it among the options* rather than by a flag.
 */
function ChoiceField({
  id,
  column,
  value,
  disabled,
  onChange,
}: {
  id: string;
  column: Column;
  value: unknown;
  disabled: boolean;
  onChange: (v: unknown) => void;
}) {
  const options = column.config.options ?? [];
  const allowOther = !!column.config.allow_other;
  const offList =
    allowOther &&
    typeof value === "string" &&
    value !== "" &&
    !options.includes(value);
  // Sticky rather than derived outright, because the box has to survive its own
  // contents: an answer typed a letter at a time passes through strings that
  // *are* options ("Done" on the way to "Done, late"), and a purely derived mode
  // would snatch the box away mid-word and put the half-typed answer in the
  // dropdown instead.
  const [othering, setOthering] = useState(offList);
  const other = othering || offList;

  return (
    <div className="flex flex-col gap-2">
      <Dropdown
        inputId={id}
        value={other ? OTHER : ((value as string) ?? null)}
        options={[
          ...options.map((o) => ({ label: o, value: o })),
          ...(allowOther ? [{ label: "Other…", value: OTHER }] : []),
        ]}
        onChange={(e) => {
          setOthering(e.value === OTHER);
          onChange(e.value === OTHER ? null : e.value);
        }}
        showClear
        placeholder="Select…"
        disabled={disabled}
      />
      {other && (
        <InputText
          aria-label={`${column.display_name}: your own answer`}
          value={(value as string) ?? ""}
          onChange={(e) => onChange(e.target.value || null)}
          placeholder="Type your answer"
          disabled={disabled}
        />
      )}
    </div>
  );
}

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
    // A text column's editor comes from its config, not its type — "single" and
    // "multiline" used to be the `text` and `long_text` types. Anything
    // unrecognised, including unset, is a single line.
    case "text": {
      const mode = textMode(column);
      if (mode === "rich") {
        return (
          <Editor
            id={id}
            value={(value as string) ?? ""}
            // `htmlValue` is the markup; `textValue` is the same content
            // flattened. The column stores HTML, so it is the former that is
            // kept — see columns/richtext.py, which cleans it on arrival.
            onTextChange={(e) => onChange(e.htmlValue ?? "")}
            headerTemplate={RICH_TOOLBAR}
            style={{ height: 200 }}
            readOnly={disabled}
          />
        );
      }
      if (mode === "multiline") {
        return (
          <InputTextarea
            id={id}
            value={(value as string) ?? ""}
            onChange={(e) => onChange(e.target.value)}
            rows={3}
            disabled={disabled}
          />
        );
      }
      break;
    }
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
    case "choice":
      return (
        <ChoiceField
          id={id}
          column={column}
          value={value}
          disabled={disabled}
          onChange={onChange}
        />
      );
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
  }
  // Everything that is a plain one-line box: a single-line text column, and any
  // type with no editor of its own.
  return (
    <InputText
      id={id}
      value={(value as string) ?? ""}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
    />
  );
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
            onChange={(v) =>
              onChange({
                ...values,
                [column.uuid]: v,
                ...clearedNested(columns, column.uuid),
              })
            }
            columns={columns}
            values={values}
          />
        </div>
      ))}
    </>
  );
}
