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
