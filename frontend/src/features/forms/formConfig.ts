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
 * Does this row hold anything other than the defaults?
 *
 * Drives the dot on the row's gear, which is what keeps a collapsed setting from being
 * an invisible one — without it the only way to find a label override or a condition is
 * to open every row.
 *
 * A row the form does not include is answered `false` whatever else it holds, and that
 * is the point rather than a shortcut. `FieldSettingsPanel` disables "editable" for such
 * a row, because a field the form never renders cannot be filled in either way — so its
 * stored `enabled` is not a setting anybody chose. The audit columns arrive excluded and
 * not-editable, which had every list showing four dots against rows whose panel offers
 * nothing to change, and a dot on every row is a dot that says nothing about any of them.
 */
export function rowIsCustomised(
  row: FormFieldRow,
  showsEnabled: boolean,
): boolean {
  if (!row.included) return false;
  return (
    row.label_override.trim() !== "" ||
    (showsEnabled && !row.enabled) ||
    settledCondition(row.condition) !== null
  );
}

/**
 * Which of a form's per-field settings an "apply" carries onto another form.
 *
 * Four parts rather than one switch because they are wanted separately. The add and
 * edit forms exist to differ on `layout` — that is the whole reason a list has two —
 * while a `conditions` rule or a renamed `label` is usually meant to hold for every
 * form that shows the column. Copying the lot is what somebody means occasionally;
 * copying one part is what they mean most of the time.
 */
export interface FormSettingParts {
  /** Field order, and which fields the form includes at all. */
  layout: boolean;
  /** Per-field "editable". */
  enabled: boolean;
  /** Per-field label overrides. */
  labels: boolean;
  /** Per-field display conditions. */
  conditions: boolean;
}

/** Nothing selected — spread over to name only the parts you mean. */
export const NO_PARTS: FormSettingParts = {
  layout: false,
  enabled: false,
  labels: false,
  conditions: false,
};

/**
 * The order `resolveFields` renders a form in: what its config names, by `order`, then
 * every other live column in schema order.
 *
 * A stored field naming a column that is gone is dropped, as `seedRows` drops it —
 * re-sending one is refused by `_validate_config`.
 */
function resolvedOrder(form: Form, columns: Column[]): string[] {
  const live = new Set(columns.map((c) => c.uuid));
  const configured = (form.config.fields ?? [])
    .filter((f) => live.has(f.column_uuid))
    .slice()
    .sort((a, b) => a.order - b.order)
    .map((f) => f.column_uuid);
  const seen = new Set(configured);
  return [
    ...configured,
    ...columns.filter((c) => !seen.has(c.uuid)).map((c) => c.uuid),
  ];
}

/**
 * `target`'s config with the named `parts` of `source` laid over it.
 *
 * **It writes the target's fields down in full, and it has to.** A form provisioned
 * with a list names only the audit columns, so every real column reaches the dialog
 * through `resolveFields`' "a column the config has never heard of is newer than the
 * config, not excluded by it" rule — not through the config at all. A setting
 * therefore has nowhere to live on such a form, and applying by mapping over the
 * stored fields reports success and changes nothing, which is how this was first
 * written.
 *
 * Writing down only the columns being changed is the other tempting fix, and it is
 * worse: `resolveFields` renders configured fields before unlisted ones, so naming one
 * column and not its neighbours moves it up the form. Emitting every column in the
 * order that function already produces is what confines this to the parts asked for.
 *
 * The rule for each part is the same one: **applying it makes the target's rendered
 * value match the source's**. So a column the source's config never names is applied
 * as the defaults it renders with there — shown, editable, no override, no condition —
 * rather than left alone. Leaving it alone is what this did when it only copied
 * conditions, and it meant "Copy conditions" could report success and leave the two
 * forms still disagreeing, with `sameConditions` re-enabling the button to say so.
 * A part not named is not touched at all.
 */
export function withAppliedSettings(
  target: Form,
  source: Form,
  parts: FormSettingParts,
  columns: Column[],
): FormConfig {
  // A view form displays a record rather than collecting one, so per-field "editable"
  // and the conditions read while a record is filled in have nothing to say on either
  // end of the copy — see `showsEnabled` in the editor. Dropping them here rather than
  // at the call site means no caller has to remember.
  const collects = target.type !== "view" && source.type !== "view";
  const takes: FormSettingParts = {
    layout: parts.layout,
    labels: parts.labels,
    enabled: parts.enabled && collects,
    conditions: parts.conditions && collects,
  };
  const from = new Map(
    (source.config.fields ?? []).map((f) => [f.column_uuid, f]),
  );
  const own = new Map(
    (target.config.fields ?? []).map((f) => [f.column_uuid, f]),
  );
  // Applying the layout means taking the source's order; otherwise the target keeps
  // the one it already renders in.
  const ordered = resolvedOrder(takes.layout ? source : target, columns);
  const live = new Set(columns.map((c) => c.uuid));

  return {
    ...target.config,
    fields: ordered.map((column_uuid, order) => {
      const src = from.get(column_uuid);
      const mine = own.get(column_uuid);
      return {
        column_uuid,
        included: (takes.layout ? src?.included : mine?.included) ?? true,
        enabled: (takes.enabled ? src?.enabled : mine?.enabled) ?? true,
        order,
        label_override:
          (takes.labels ? src?.label_override : mine?.label_override) ?? null,
        // Pruned on the way out, on both sides. This rewrites the target's config
        // wholesale, so a condition naming a column deleted since it was written would
        // 422 the write — the target's own stale condition just as surely as one
        // arriving from the source. `seedRows` prunes for the same reason.
        condition: prunedCondition(
          takes.conditions ? src?.condition : mine?.condition,
          live,
        ),
      };
    }),
  };
}

/**
 * `source`'s conditions laid over `target`, leaving the target's layout as it renders.
 *
 * The "Copy conditions to…" button, which predates applying settings in general and is
 * the one case of it worth a button of its own: conditions are the part two paired
 * forms most often want to share.
 */
export function withCopiedConditions(
  target: Form,
  source: Form,
  columns: Column[],
): FormConfig {
  return withAppliedSettings(
    target,
    source,
    { ...NO_PARTS, conditions: true },
    columns,
  );
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
