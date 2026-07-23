/**
 * The record form's shape, in one place: which fields it has, and how a value
 * crosses between the form and the API.
 *
 * `RecordFormDialog` is not the only thing that builds a record any more — the QR
 * scanner fills, and can submit, one without the dialog ever being touched. Both
 * paths therefore have to agree on the same three questions (which columns does
 * this form include, what is an empty value, and what does a value look like once
 * serialized), so the answers live here rather than inside the dialog.
 */
import { isTemporal, parseTemporal, serializeTemporal } from "../../lib/temporal";
import type { Column, Form } from "../../lib/types";

export interface ResolvedField {
  column: Column;
  enabled: boolean;
  label: string;
}

/**
 * Apply the form's config: only included fields, in order, with enabled and
 * label overrides. An empty/absent config means "all columns in schema order".
 */
export function resolveFields(
  columns: Column[],
  form: Form | null,
): ResolvedField[] {
  // A calculated column is computed on read and rejected on write, so it is never
  // an input on a record form.
  const editable = columns.filter((c) => c.type !== "calculated");
  if (form && form.config.fields && form.config.fields.length > 0) {
    const byUuid = new Map(editable.map((c) => [c.uuid, c]));
    return form.config.fields
      .filter((f) => f.included)
      .slice()
      .sort((a, b) => a.order - b.order)
      .map((f) => {
        const column = byUuid.get(f.column_uuid);
        if (!column) return null;
        return {
          column,
          enabled: f.enabled !== false,
          label:
            (f.label_override && f.label_override.trim()) || column.display_name,
        };
      })
      .filter((x): x is ResolvedField => x !== null);
  }
  return editable.map((c) => ({ column: c, enabled: true, label: c.display_name }));
}

export function serialize(column: Column, value: unknown): unknown {
  if (value instanceof Date) {
    return serializeTemporal(column.type, value);
  }
  return value;
}

export function deserialize(column: Column, raw: unknown): unknown {
  if (!isTemporal(column.type)) return raw;
  // A value we cannot parse stays as-is rather than becoming an Invalid Date.
  return parseTemporal(column.type, raw) ?? raw;
}

/**
 * Every lookup column nested (directly or transitively) under `changedUuid`,
 * via `config.parent_column_uuid` edges. When a parent's value changes, these
 * are the cells whose selections no longer mean anything and must be cleared —
 * a city picked for the old country must not survive the country changing.
 */
export function nestedDescendants(
  columns: Column[],
  changedUuid: string,
): string[] {
  const children = new Map<string, string[]>();
  for (const column of columns) {
    const parent = column.config.parent_column_uuid;
    if (!parent) continue;
    children.set(parent, [...(children.get(parent) ?? []), column.uuid]);
  }
  const found: string[] = [];
  const queue = [...(children.get(changedUuid) ?? [])];
  while (queue.length > 0) {
    const uuid = queue.shift()!;
    if (found.includes(uuid)) continue; // config cycles cannot loop us
    found.push(uuid);
    queue.push(...(children.get(uuid) ?? []));
  }
  return found;
}

/** What the backend treats as "no value": null, "", and the empty list. */
export function isEmptyValue(value: unknown): boolean {
  return (
    value === undefined ||
    value === null ||
    value === "" ||
    (Array.isArray(value) && value.length === 0)
  );
}

/**
 * The `data` blob to send, keyed by column **uuid** — never by name, because that
 * is how records are stored.
 *
 * Editing sends an empty field as `null` (the user cleared it); creating simply
 * omits it, so the column's default still applies.
 */
export function buildRecordData(
  fields: ResolvedField[],
  values: Record<string, unknown>,
  editing: boolean,
): Record<string, unknown> {
  const data: Record<string, unknown> = {};
  for (const { column, enabled } of fields) {
    if (!enabled) continue; // read-only fields keep their default/existing value
    const value = values[column.uuid];
    const empty = isEmptyValue(value);
    if (editing) {
      data[column.uuid] = empty ? null : serialize(column, value);
    } else if (!empty) {
      data[column.uuid] = serialize(column, value);
    }
  }
  return data;
}
