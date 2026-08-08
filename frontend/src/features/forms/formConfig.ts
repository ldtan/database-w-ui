/**
 * Building a form's `config` for a PATCH, in one testable place.
 *
 * The editor only knows about `fields`, but `config` is a whole object and the API
 * **replaces** it rather than merging (`FormService.update`) — and `FormConfig` is
 * `extra="forbid"`, so a key the editor drops cannot be sent back later either. So a
 * patch that names only `fields` silently destroys every other key the form was
 * carrying, and the destruction happens on an ordinary "Save layout" by someone who
 * changed nothing but the order.
 *
 * Spreading the stored config first is the whole fix. It is one line and it is easy to
 * lose in a refactor, which is why it lives in a pure function with a test rather than
 * inline in a mutation body.
 */
import type { Form, FormConfig, MergeConfig } from "../../lib/types";

/**
 * The form whose `merge` block has to move in step with this one.
 *
 * Merging is a property of the *pair*, not of one form, because the grid picks which
 * form to render by what you are doing: `f.type === (item ? "update" : "create")`. So
 * a merge block on the create form alone gets you a merged Add dialog and a plain Edit
 * one — the borrowed fields simply vanish when you reopen the record, with nothing on
 * screen to say why. That is the shape of bug this codebase spends most of its comments
 * avoiding, so the editor writes both rather than leaving it to be discovered.
 *
 * Only `create` and `update` pair up. A `view` form displays a record and writes
 * nothing, so it has no second record to write either; a `custom` one is not reached by
 * the grid's picker at all.
 *
 * Field *layout* is deliberately not mirrored — which columns a form shows, in what
 * order, is exactly what the two forms exist to differ on.
 */
export function mergeSibling(active: Form, forms: Form[]): Form | null {
  const want =
    active.type === "create" ? "update" : active.type === "update" ? "create" : null;
  if (!want) return null;
  const candidates = forms.filter((f) => f.type === want && f.uuid !== active.uuid);
  // A list can hold more than one form of a type; the grid reaches for the default.
  return candidates.find((f) => f.is_default) ?? candidates[0] ?? null;
}

/** Whether two merge blocks say the same thing, so an unchanged one is not re-sent. */
export function sameMerge(
  a: MergeConfig | null | undefined,
  b: MergeConfig | null | undefined,
): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

/** One row of the form editor: a column, and what the form does with it. */
export interface FormFieldRow {
  column_uuid: string;
  included: boolean;
  enabled: boolean;
  label_override: string;
}

/**
 * The `config` to PATCH after editing `rows`.
 *
 * Array position becomes `order`, so nothing downstream needs to know *how* the order
 * changed — a drag and a chevron produce the same output. An empty `label_override`
 * is sent as `null` rather than `""`, matching what the backend stores for "no
 * override" so a round trip does not churn the row.
 */
export function buildFormConfigPatch(
  form: Form,
  rows: FormFieldRow[],
  merge?: MergeConfig | null,
): FormConfig {
  return {
    // Everything the editor does not model survives the save. Do not replace this
    // with a bare object literal — that is the bug this function exists to prevent.
    ...form.config,
    fields: rows.map((row, i) => ({
      column_uuid: row.column_uuid,
      included: row.included,
      enabled: row.enabled,
      order: i,
      label_override: row.label_override.trim() || null,
    })),
    // `undefined` means "the editor is not managing this", which is different from
    // `null`, meaning "the editor turned merging off". Only the second may overwrite
    // what is stored, so the spread above stands when the argument is omitted.
    ...(merge === undefined ? {} : { merge }),
  };
}
