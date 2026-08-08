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
import type {
  Column,
  FieldCondition,
  Form,
  FormConfig,
} from "../../lib/types";
import { cleanConditions } from "../views/operators";

/** One row of the form editor: a column, and what the form does with it. */
export interface FormFieldRow {
  column_uuid: string;
  included: boolean;
  enabled: boolean;
  label_override: string;
  /**
   * Null is "no condition". While the row is being edited this may hold a half-built
   * condition — a chosen operator with no column yet — because dropping those as they
   * are typed would take the row away mid-thought. `settledCondition` is what turns
   * that back into something the API will accept.
   */
  condition: FieldCondition | null;
}

/**
 * A row's condition as it should be stored: complete conditions only, and `null` rather
 * than an empty group.
 *
 * `FilterCondition.column_uuid` is a `UUID` on the wire, so a row still missing its
 * column would 422 the whole save — and an empty group means "always", which is what
 * having no condition already means. Collapsing the two keeps one representation of
 * "unconditional" in the config instead of two.
 */
export function settledCondition(
  condition: FieldCondition | null | undefined,
): FieldCondition | null {
  if (!condition) return null;
  const conditions = cleanConditions(condition.when?.conditions ?? []);
  if (conditions.length === 0) return null;
  return {
    when: { conjunction: condition.when?.conjunction ?? "and", conditions },
    effect: condition.effect ?? "hide",
  };
}

/**
 * The form whose conditions the "Copy conditions to…" action writes.
 *
 * Only `create` and `update` pair up, because the grid picks between exactly those two
 * by what you are doing — `f.type === (item ? "update" : "create")`. A `view` form
 * displays a record and collects nothing, and a `custom` one is not reached by that
 * picker at all.
 *
 * Unlike the field layout, which the two forms exist to differ on, a condition is
 * usually meant to hold for both — but only usually, which is why copying is an action
 * somebody takes rather than something saving does on its own.
 */
export function siblingForm(active: Form, forms: Form[]): Form | null {
  const want =
    active.type === "create"
      ? "update"
      : active.type === "update"
        ? "create"
        : null;
  if (!want) return null;
  const candidates = forms.filter(
    (f) => f.type === want && f.uuid !== active.uuid,
  );
  // A list can hold more than one form of a type; the grid reaches for the default.
  return candidates.find((f) => f.is_default) ?? candidates[0] ?? null;
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
      condition: settledCondition(row.condition),
    })),
  };
}

/**
 * A stored condition with any part of it that no longer resolves dropped.
 *
 * `known` is the uuids of the list's live columns. A condition naming one that has
 * since been deleted would fail `FormService._validate_config` on the next save, so
 * the form becomes unsaveable — by whoever opens it next, who did not write the
 * condition and has nothing on screen telling them what is wrong. Pruning as the
 * editor seeds is the same thing it already does for a *field* naming a missing
 * column, and it is why deleting a column needs no cascade over forms.
 *
 * Until then the condition simply sits there: `conditions.ts` cannot answer it either,
 * and drops it, so the field it governs shows.
 */
export function prunedCondition(
  condition: FieldCondition | null | undefined,
  known: Set<string>,
): FieldCondition | null {
  const settled = settledCondition(condition);
  if (!settled) return null;
  return settledCondition({
    ...settled,
    when: {
      ...settled.when,
      conditions: settled.when.conditions.filter((c) =>
        known.has(c.column_uuid),
      ),
    },
  });
}

/**
 * `source`'s conditions laid over `target`, leaving the target's layout as it renders.
 *
 * **It writes the target's layout down in full, and it has to.** A form provisioned
 * with a list names only the audit columns, so every real column reaches the dialog
 * through `resolveFields`' "a column the config has never heard of is newer than the
 * config, not excluded by it" rule — not through the config at all. A condition
 * therefore has nowhere to live on such a form, and copying by mapping over the stored
 * fields reports success and changes nothing, which is how this was first written.
 *
 * Appending only the conditioned columns is the other tempting fix, and it is worse:
 * `resolveFields` renders configured fields before unlisted ones, so writing one
 * column down and not its neighbours moves it up the form. Emitting every column in
 * the order that function already produces is what makes this a copy of the
 * conditions and *only* the conditions.
 *
 * A column the target excludes stays excluded, its label override survives, and a
 * column the source never mentions keeps whatever condition it had.
 */
export function withCopiedConditions(
  target: Form,
  source: Form,
  columns: Column[],
): FormConfig {
  const bySource = new Map(
    (source.config.fields ?? []).map((f) => [
      f.column_uuid,
      f.condition ?? null,
    ]),
  );
  const stored = new Map(
    (target.config.fields ?? []).map((f) => [f.column_uuid, f]),
  );
  // `resolveFields`' order: what the config names, by `order`, then everything else
  // in schema order. A stored field naming a column that is gone is dropped, as
  // `seedRows` drops it — re-sending one is refused by `_validate_config`.
  const live = new Set(columns.map((c) => c.uuid));
  const configured = (target.config.fields ?? [])
    .filter((f) => live.has(f.column_uuid))
    .slice()
    .sort((a, b) => a.order - b.order)
    .map((f) => f.column_uuid);
  const seen = new Set(configured);
  const ordered = [
    ...configured,
    ...columns.filter((c) => !seen.has(c.uuid)).map((c) => c.uuid),
  ];

  return {
    ...target.config,
    fields: ordered.map((column_uuid, order) => {
      const field = stored.get(column_uuid);
      return {
        column_uuid,
        included: field?.included ?? true,
        enabled: field?.enabled ?? true,
        order,
        label_override: field?.label_override ?? null,
        condition: bySource.has(column_uuid)
          ? (bySource.get(column_uuid) ?? null)
          : (field?.condition ?? null),
      };
    }),
  };
}

/** Whether two forms already agree about every condition, so nothing need be sent. */
export function sameConditions(a: Form, b: Form): boolean {
  const of = (form: Form) =>
    JSON.stringify(
      (form.config.fields ?? [])
        .map((f) => [f.column_uuid, f.condition ?? null] as const)
        .filter(([, condition]) => condition !== null)
        .sort(([x], [y]) => (x < y ? -1 : x > y ? 1 : 0)),
    );
  return of(a) === of(b);
}
