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
import type { Column, Form, MergeConfig, TextMode } from "../../lib/types";

export interface ResolvedField {
  column: Column;
  enabled: boolean;
  label: string;
}

/**
 * A column the server writes for you cannot be an input, whatever a form config
 * says: `config.onupdate` is stamped over anything sent on every accepted write
 * (`ListItemService._validate_data`). Read-only rather than absent, because a
 * "last modified" is exactly the sort of thing you want to *see* on a record —
 * which is what `enabled: false` already means everywhere it is read.
 *
 * The same reasoning `resolveFields` applies to calculated columns, one step
 * further: the formula is their only writer, so they are dropped from an input
 * form rather than shown disabled.
 */
function writable(column: Column): boolean {
  return !column.config.onupdate;
}

/**
 * Apply the form's config: only included fields, in order, with enabled and
 * label overrides. An empty/absent config means "all columns in schema order".
 *
 * `includeCalculated` is what separates a display surface from an input one. A
 * calculated column is written by its formula and rejected from a client write, so
 * it can never be an input — but it is exactly what a view form exists to show, and
 * the value is already sitting in the record the backend handed over.
 *
 * **A column the config has never heard of is newer than the config, not excluded
 * by it**, so it is appended in schema order rather than dropped. The distinction
 * is safe to draw because `FormsEditor` always writes back *every* column: one you
 * deliberately hid is in the config as `included: false`, and only a column added
 * since the last save is absent altogether. Reading absence as exclusion is what
 * made the Add/Edit dialog on a freshly provisioned list say "Add a column first"
 * no matter how many columns you added — its seeded config names the audit columns
 * and nothing else, so every real column fell through the gap until someone opened
 * the form editor and saved. This is the same rule a view's `column_order` follows
 * (data-model §7), for the same reason.
 */
export function resolveFields(
  columns: Column[],
  form: Form | null,
  { includeCalculated = false }: { includeCalculated?: boolean } = {},
): ResolvedField[] {
  const eligible = includeCalculated
    ? columns
    : columns.filter((c) => c.type !== "calculated");
  if (form && form.config.fields && form.config.fields.length > 0) {
    const byUuid = new Map(eligible.map((c) => [c.uuid, c]));
    const configured = form.config.fields
      .filter((f) => f.included)
      .slice()
      .sort((a, b) => a.order - b.order)
      .map((f) => {
        const column = byUuid.get(f.column_uuid);
        if (!column) return null;
        return {
          column,
          enabled: f.enabled !== false && writable(column),
          label:
            (f.label_override && f.label_override.trim()) || column.display_name,
        };
      })
      .filter((x): x is ResolvedField => x !== null);
    // Every uuid the config mentions, included or not — an excluded column is
    // spoken for and must not come back through the door below.
    const known = new Set(form.config.fields.map((f) => f.column_uuid));
    const appended = eligible
      .filter((c) => !known.has(c.uuid))
      .map((c) => ({ column: c, enabled: writable(c), label: c.display_name }));
    return [...configured, ...appended];
  }
  return eligible.map((c) => ({
    column: c,
    enabled: writable(c),
    label: c.display_name,
  }));
}

/**
 * The fields a merged form borrows from the list its link column points at.
 *
 * A sibling of `resolveFields` rather than an option on it, because the two disagree
 * about the one thing that function's rules are built around. There, an empty config
 * means *every* column and a column the config has never heard of is appended — both
 * right for a form over its own list, where a new column should show up on its own.
 *
 * Here empty means **none**, and an unlisted column stays unlisted. The other list is
 * not this form's to enumerate: "all of them" would silently widen as the target
 * gained columns, putting fields on screen that nobody chose and that the form's author
 * may not even be able to see. The backend refuses to imply them for the same reason,
 * so the two ends agree.
 *
 * `columns` is the *linked* list's schema, which the caller may not have — a 403 on
 * that list's columns is how a merged form degrades — so an empty array yields no
 * fields rather than throwing.
 */
export function resolveMergedFields(
  columns: Column[],
  merge: MergeConfig | null | undefined,
): ResolvedField[] {
  if (!merge || merge.fields.length === 0) return [];
  const byUuid = new Map(columns.map((c) => [c.uuid, c]));
  return merge.fields
    .filter((f) => f.included)
    .slice()
    .sort((a, b) => a.order - b.order)
    .map((f) => {
      const column = byUuid.get(f.column_uuid);
      // A borrowed column that is gone, or on a list this caller cannot read, simply
      // does not render. There is nothing to show and nothing to write.
      if (!column) return null;
      return {
        column,
        enabled: f.enabled !== false && writable(column),
        label: (f.label_override && f.label_override.trim()) || column.display_name,
      };
    })
    .filter((x): x is ResolvedField => x !== null);
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
    if (editing) {
      data[column.uuid] = toStored(column, value);
    } else if (!isEmptyValue(value)) {
      // Creating omits an empty field rather than nulling it, so the column's
      // default still applies — which is why this branch is not `toStored`.
      data[column.uuid] = serialize(column, value);
    }
  }
  return data;
}

/**
 * A form value as the API stores it: absent becomes `null`, anything else is
 * serialized for its column.
 *
 * Absence is spelled several ways — a cleared box is `""`, an emptied
 * multi-select is `[]`, a stored empty cell is `null` — and collapsing them in one
 * place is what keeps the dialog and the grid agreeing about the same edit.
 */
export function toStored(column: Column, value: unknown): unknown {
  return isEmptyValue(value) ? null : serialize(column, value);
}

/**
 * The cells a changed parent invalidates, ready to spread into whatever is being
 * built — a form's values, or a single-cell patch. A city picked for the old
 * country means nothing once the country moves, and the two surfaces that clear
 * it must not drift apart on what "under" means.
 */
export function clearedNested(
  columns: Column[],
  changedUuid: string,
): Record<string, null> {
  return Object.fromEntries(
    nestedDescendants(columns, changedUuid).map((uuid) => [uuid, null]),
  );
}

/**
 * How a text column's value is written and shown.
 *
 * A text column's editor comes from its config rather than its type — "single"
 * and "multiline" used to be the `text` and `long_text` types — and anything
 * unrecognised, including unset, is a single line. Stated once here because three
 * surfaces branch on it and must not drift: which editor the dialog renders, how
 * the value displays in a cell or a dialog, and whether a grid cell can hold an
 * editor at all.
 */
export function textMode(column: Column): TextMode {
  if (column.type !== "text") return "single";
  const mode = column.config.text_mode;
  return mode === "rich" || mode === "multiline" ? mode : "single";
}

/**
 * A column whose editor needs more room than a table row has. A rich text editor
 * is a toolbar and a 200px box; a multiline value's line breaks are its content.
 * Shrinking either into a row would be a worse editor than the one that exists.
 */
export function needsFullEditor(column: Column): boolean {
  return textMode(column) !== "single";
}
