import { describe, expect, it } from "vitest";

import type {
  Column,
  FieldCondition,
  Form,
  FormField,
} from "../../lib/types";
import {
  buildFormConfigPatch,
  prunedCondition,
  sameConditions,
  settledCondition,
  siblingForm,
  withCopiedConditions,
  type FormFieldRow,
} from "./formConfig";

function form(over: Partial<Form> = {}): Form {
  return {
    uuid: "f1",
    name: "create",
    display_name: "Create",
    type: "create",
    is_default: true,
    permissions_inherit: true,
    config: { fields: [] },
    ...over,
  } as Form;
}

function field(over: Partial<FormField> = {}): FormField {
  return {
    column_uuid: "c1",
    included: true,
    enabled: true,
    order: 0,
    label_override: null,
    ...over,
  };
}

function row(over: Partial<FormFieldRow> = {}): FormFieldRow {
  return {
    column_uuid: "c1",
    included: true,
    enabled: true,
    label_override: "",
    condition: null,
    ...over,
  };
}

const HIDE_UNLESS_REJECTED: FieldCondition = {
  when: {
    conjunction: "and",
    conditions: [{ column_uuid: "status", operator: "is", value: "Rejected" }],
  },
  effect: "hide",
};

describe("buildFormConfigPatch", () => {
  it("writes array position back as order", () => {
    const patch = buildFormConfigPatch(form(), [
      row({ column_uuid: "b" }),
      row({ column_uuid: "a" }),
    ]);
    expect(patch.fields.map((f) => [f.column_uuid, f.order])).toEqual([
      ["b", 0],
      ["a", 1],
    ]);
  });

  it("sends a blank label override as null, not an empty string", () => {
    expect(
      buildFormConfigPatch(form(), [row({ label_override: "  " })]).fields[0]
        .label_override,
    ).toBeNull();
    expect(
      buildFormConfigPatch(form(), [row({ label_override: " Name " })])
        .fields[0].label_override,
    ).toBe("Name");
  });

  it("carries each row's condition through", () => {
    const patch = buildFormConfigPatch(form(), [
      row({ condition: HIDE_UNLESS_REJECTED }),
    ]);
    expect(patch.fields[0].condition).toEqual(HIDE_UNLESS_REJECTED);
  });

  /**
   * The bug this whole module exists to prevent.
   *
   * `PATCH /forms/{uuid}` replaces `config` wholesale and `FormConfig` is
   * `extra="forbid"`, so a key the editor does not model is destroyed by an ordinary
   * "Save layout" — by somebody who changed nothing but the order, with nothing on
   * screen to say what went. `merge` is used as the unmodelled key on purpose: it is
   * the block merged forms store, and this is the test that keeps it alive.
   */
  it("preserves a config key the editor knows nothing about", () => {
    const stored = form({
      config: {
        fields: [field()],
        merge: { link_column_uuid: "link", fields: [], allow_shared: false },
      } as never,
    });
    const patch = buildFormConfigPatch(stored, [row()]) as unknown as Record<
      string,
      unknown
    >;
    expect(patch.merge).toEqual({
      link_column_uuid: "link",
      fields: [],
      allow_shared: false,
    });
  });
});

describe("settledCondition", () => {
  /**
   * The editor holds half-built conditions on purpose — dropping a row the moment its
   * operator is chosen and its column is not would take it away mid-thought — so this
   * is where they stop. `FilterCondition.column_uuid` is a `UUID` on the wire, and one
   * blank row would 422 the whole save.
   */
  it("drops a condition with no column chosen yet", () => {
    expect(
      settledCondition({
        when: {
          conjunction: "and",
          conditions: [{ column_uuid: "", operator: "is", value: "x" }],
        },
        effect: "hide",
      }),
    ).toBeNull();
  });

  it("drops a condition whose operator needs a value it has not got", () => {
    expect(
      settledCondition({
        when: {
          conjunction: "and",
          conditions: [{ column_uuid: "status", operator: "is", value: "" }],
        },
        effect: "hide",
      }),
    ).toBeNull();
  });

  it("keeps a value-free operator", () => {
    const condition: FieldCondition = {
      when: {
        conjunction: "and",
        conditions: [{ column_uuid: "status", operator: "is_empty" }],
      },
      effect: "hide",
    };
    expect(settledCondition(condition)).toEqual(condition);
  });

  /** One representation of "unconditional" in the stored config, not two. */
  it("collapses an empty group to null", () => {
    expect(
      settledCondition({
        when: { conjunction: "and", conditions: [] },
        effect: "hide",
      }),
    ).toBeNull();
    expect(settledCondition(null)).toBeNull();
  });

  it("keeps the complete conditions out of a partly-built group", () => {
    const settled = settledCondition({
      when: {
        conjunction: "or",
        conditions: [
          { column_uuid: "status", operator: "is", value: "Rejected" },
          { column_uuid: "", operator: "is", value: "" },
        ],
      },
      effect: "disable",
    });
    expect(settled?.when.conditions).toHaveLength(1);
    expect(settled?.when.conjunction).toBe("or");
    expect(settled?.effect).toBe("disable");
  });
});

describe("prunedCondition", () => {
  /**
   * A column deleted after the condition was written would fail the backend's
   * reference check on the next save — so the form becomes unsaveable for whoever
   * opens it next, who did not write the condition. Pruning as the editor seeds is
   * what makes a column delete need no cascade over forms.
   */
  it("drops a condition naming a column that is gone", () => {
    expect(
      prunedCondition(HIDE_UNLESS_REJECTED, new Set(["other"])),
    ).toBeNull();
  });

  it("keeps one naming a live column", () => {
    expect(prunedCondition(HIDE_UNLESS_REJECTED, new Set(["status"]))).toEqual(
      HIDE_UNLESS_REJECTED,
    );
  });

  it("keeps the half that still resolves", () => {
    const pruned = prunedCondition(
      {
        when: {
          conjunction: "and",
          conditions: [
            { column_uuid: "status", operator: "is", value: "Rejected" },
            { column_uuid: "gone", operator: "is", value: "x" },
          ],
        },
        effect: "hide",
      },
      new Set(["status"]),
    );
    expect(pruned?.when.conditions.map((c) => c.column_uuid)).toEqual([
      "status",
    ]);
  });
});

describe("siblingForm", () => {
  const create = form({ uuid: "c", type: "create" });
  const update = form({ uuid: "u", type: "update" });
  const view = form({ uuid: "v", type: "view", name: "view" });

  it("pairs create with update, in both directions", () => {
    expect(siblingForm(create, [create, update, view])?.uuid).toBe("u");
    expect(siblingForm(update, [create, update, view])?.uuid).toBe("c");
  });

  it("has nothing to pair a view or custom form with", () => {
    expect(siblingForm(view, [create, update, view])).toBeNull();
    expect(siblingForm(form({ type: "custom" }), [create, update])).toBeNull();
  });

  it("prefers the default when a list has two forms of a type", () => {
    const other = form({ uuid: "u2", type: "update", is_default: false });
    expect(siblingForm(create, [create, other, update])?.uuid).toBe("u");
  });

  it("finds nothing when the pair is missing", () => {
    expect(siblingForm(create, [create])).toBeNull();
  });
});

describe("withCopiedConditions", () => {
  const COLUMNS = ["status", "reason", "notes"].map(
    (uuid) => ({ uuid, name: uuid, display_name: uuid, type: "text", config: {} }) as Column,
  );
  const source = form({
    uuid: "c",
    config: {
      fields: [
        field({ column_uuid: "status" }),
        field({ column_uuid: "reason", condition: HIDE_UNLESS_REJECTED }),
      ],
    },
  });
  const of = (config: { fields: FormField[] }, uuid: string) =>
    config.fields.find((f) => f.column_uuid === uuid)!;

  it("copies a condition onto the matching column", () => {
    const target = form({
      uuid: "u",
      type: "update",
      config: { fields: [field({ column_uuid: "reason" })] },
    });
    expect(
      of(withCopiedConditions(target, source, COLUMNS), "reason").condition,
    ).toEqual(HIDE_UNLESS_REJECTED);
  });

  /**
   * The bug the browser found, and the reason this takes `columns` at all.
   *
   * A form provisioned with a list names only the audit columns, so every real column
   * reaches the dialog through `resolveFields`' "newer than the config" rule and has
   * no entry to hang a condition on. Copying by mapping over the stored fields
   * reported success and wrote nothing.
   */
  it("gives a column the target has never listed an entry to hold the condition", () => {
    const target = form({
      uuid: "u",
      type: "update",
      config: { fields: [] },
    });
    const patched = withCopiedConditions(target, source, COLUMNS);
    expect(of(patched, "reason").condition).toEqual(HIDE_UNLESS_REJECTED);
    expect(of(patched, "reason").included).toBe(true);
  });

  /**
   * Writing one column down and not its neighbours would move it up the form, because
   * `resolveFields` renders configured fields before unlisted ones. So the whole
   * layout is written, in the order it already renders in.
   */
  it("keeps the order the form already had", () => {
    const target = form({
      uuid: "u",
      type: "update",
      // Configured: notes first. Unlisted: status and reason, in schema order.
      config: { fields: [field({ column_uuid: "notes", order: 0 })] },
    });
    const patched = withCopiedConditions(target, source, COLUMNS);
    expect(patched.fields.map((f) => f.column_uuid)).toEqual([
      "notes",
      "status",
      "reason",
    ]);
    expect(patched.fields.map((f) => f.order)).toEqual([0, 1, 2]);
  });

  it("clears a condition the source has dropped", () => {
    const target = form({
      uuid: "u",
      type: "update",
      config: {
        fields: [
          field({ column_uuid: "status", condition: HIDE_UNLESS_REJECTED }),
        ],
      },
    });
    expect(
      of(withCopiedConditions(target, source, COLUMNS), "status").condition,
    ).toBeNull();
  });

  /** The layout is what two forms exist to differ on, so only conditions move. */
  it("leaves a column the source never mentions alone", () => {
    const own: FieldCondition = { ...HIDE_UNLESS_REJECTED, effect: "disable" };
    const target = form({
      uuid: "u",
      type: "update",
      config: {
        fields: [
          field({
            column_uuid: "notes",
            included: false,
            label_override: "Remarks",
            condition: own,
          }),
        ],
      },
    });
    const patched = withCopiedConditions(target, source, COLUMNS);
    expect(of(patched, "notes").condition).toEqual(own);
    expect(of(patched, "notes").included).toBe(false);
    expect(of(patched, "notes").label_override).toBe("Remarks");
  });

  /** A stored field naming a deleted column is refused by `_validate_config`, so it
   *  is dropped here as `seedRows` drops it. */
  it("drops a field naming a column that is gone", () => {
    const target = form({
      uuid: "u",
      type: "update",
      config: { fields: [field({ column_uuid: "deleted" })] },
    });
    const patched = withCopiedConditions(target, source, COLUMNS);
    expect(patched.fields.map((f) => f.column_uuid)).not.toContain("deleted");
  });

  it("keeps a config key it does not model", () => {
    const target = form({
      uuid: "u",
      type: "update",
      config: { fields: [], merge: { link_column_uuid: "link" } } as never,
    });
    const patched = withCopiedConditions(
      target,
      source,
      COLUMNS,
    ) as unknown as Record<string, unknown>;
    expect(patched.merge).toEqual({ link_column_uuid: "link" });
  });
});

describe("sameConditions", () => {
  const a = form({
    config: {
      fields: [
        field({ column_uuid: "reason", condition: HIDE_UNLESS_REJECTED }),
      ],
    },
  });

  it("ignores field order and unconditional fields", () => {
    const b = form({
      uuid: "u",
      config: {
        fields: [
          field({ column_uuid: "zzz" }),
          field({ column_uuid: "reason", condition: HIDE_UNLESS_REJECTED }),
        ],
      },
    });
    expect(sameConditions(a, b)).toBe(true);
  });

  it("notices a different effect", () => {
    const b = form({
      uuid: "u",
      config: {
        fields: [
          field({
            column_uuid: "reason",
            condition: { ...HIDE_UNLESS_REJECTED, effect: "disable" },
          }),
        ],
      },
    });
    expect(sameConditions(a, b)).toBe(false);
  });

  it("notices a condition that is missing entirely", () => {
    expect(sameConditions(a, form({ uuid: "u" }))).toBe(false);
  });
});
