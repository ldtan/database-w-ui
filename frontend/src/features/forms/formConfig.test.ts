import { describe, expect, it } from "vitest";

import type { Form } from "../../lib/types";
import {
  buildFormConfigPatch,
  mergeSibling,
  sameMerge,
  type FormFieldRow,
} from "./formConfig";

function form(config: Partial<Form["config"]> = {}): Form {
  return {
    uuid: "form-1",
    name: "create",
    display_name: "Create",
    type: "create",
    is_default: true,
    permissions_inherit: true,
    config: { fields: [], ...config },
  } as Form;
}

const rows: FormFieldRow[] = [
  { column_uuid: "c1", included: true, enabled: true, label_override: "" },
  { column_uuid: "c2", included: false, enabled: true, label_override: "  Notes  " },
];

describe("buildFormConfigPatch", () => {
  it("writes array position back as order", () => {
    const { fields } = buildFormConfigPatch(form(), rows);
    expect(fields.map((f) => [f.column_uuid, f.order])).toEqual([
      ["c1", 0],
      ["c2", 1],
    ]);
  });

  it("trims a label override, and sends an empty one as null", () => {
    const { fields } = buildFormConfigPatch(form(), rows);
    expect(fields[0].label_override).toBeNull();
    expect(fields[1].label_override).toBe("Notes");
  });

  it("carries included and enabled through unchanged", () => {
    const { fields } = buildFormConfigPatch(form(), rows);
    expect(fields[1].included).toBe(false);
    expect(fields[1].enabled).toBe(true);
  });

  /**
   * The reason this module exists. The PATCH replaces `config` wholesale and
   * `FormConfig` is `extra="forbid"`, so a key the editor drops here is gone from the
   * stored form the moment anyone clicks "Save layout" — and cannot be sent back.
   */
  it("preserves config keys the editor does not model", () => {
    const merge = {
      link_column_uuid: "link-1",
      fields: [{ column_uuid: "a1", included: true, enabled: true, order: 0 }],
      allow_shared: false,
    };
    const patch = buildFormConfigPatch(form({ merge } as never), rows);
    expect((patch as never as { merge: unknown }).merge).toEqual(merge);
  });

  it("still replaces fields rather than merging them", () => {
    const stale = [
      { column_uuid: "gone", included: true, enabled: true, order: 0 },
    ];
    const { fields } = buildFormConfigPatch(form({ fields: stale }), rows);
    expect(fields.map((f) => f.column_uuid)).toEqual(["c1", "c2"]);
  });

  /**
   * `undefined` and `null` are different answers. The first says "the editor is not
   * managing merge", so whatever is stored stands; the second says "the editor turned
   * it off". Collapsing them would make every save from a surface that does not show
   * the merge editor silently un-merge the form.
   */
  it("leaves a stored merge alone when no merge argument is given", () => {
    const merge = { link_column_uuid: "L", fields: [], allow_shared: false };
    const patch = buildFormConfigPatch(form({ merge } as never), rows);
    expect((patch as never as { merge: unknown }).merge).toEqual(merge);
  });

  it("clears a stored merge when explicitly passed null", () => {
    const merge = { link_column_uuid: "L", fields: [], allow_shared: false };
    const patch = buildFormConfigPatch(form({ merge } as never), rows, null);
    expect((patch as never as { merge: unknown }).merge).toBeNull();
  });

  it("writes a new merge block when given one", () => {
    const next = { link_column_uuid: "L2", fields: [], allow_shared: true };
    const patch = buildFormConfigPatch(form(), rows, next);
    expect((patch as never as { merge: unknown }).merge).toEqual(next);
  });
});

describe("mergeSibling", () => {
  const f = (over: Partial<Form>) =>
    ({ uuid: "x", name: "n", display_name: "N", type: "create", is_default: true, config: { fields: [] }, ...over }) as Form;
  const create = f({ uuid: "c", type: "create" });
  const update = f({ uuid: "u", type: "update" });
  const view = f({ uuid: "v", type: "view" });
  const custom = f({ uuid: "k", type: "custom" });

  it("pairs create with update, both ways", () => {
    expect(mergeSibling(create, [create, update, view])?.uuid).toBe("u");
    expect(mergeSibling(update, [create, update, view])?.uuid).toBe("c");
  });

  /**
   * A view form writes nothing, so it has no second record to write; a custom one is
   * never reached by the grid's `type === (item ? "update" : "create")` picker.
   */
  it("pairs nothing for view or custom forms", () => {
    expect(mergeSibling(view, [create, update, view])).toBeNull();
    expect(mergeSibling(custom, [create, update, custom])).toBeNull();
  });

  it("prefers the default when a type has several forms", () => {
    const extra = f({ uuid: "u2", type: "update", is_default: false });
    const marked = f({ uuid: "u3", type: "update", is_default: true });
    expect(mergeSibling(create, [create, extra, marked])?.uuid).toBe("u3");
  });

  it("returns null when the list has no counterpart", () => {
    expect(mergeSibling(create, [create])).toBeNull();
  });
});

describe("sameMerge", () => {
  const merge = { link_column_uuid: "L", fields: [], allow_shared: false };

  it("treats null and undefined as the same absence", () => {
    expect(sameMerge(null, undefined)).toBe(true);
  });

  it("spots a changed link, a changed flag, and changed fields", () => {
    expect(sameMerge(merge, { ...merge, link_column_uuid: "L2" })).toBe(false);
    expect(sameMerge(merge, { ...merge, allow_shared: true })).toBe(false);
    expect(
      sameMerge(merge, {
        ...merge,
        fields: [{ column_uuid: "a", included: true, enabled: true, order: 0 }],
      }),
    ).toBe(false);
  });

  it("says equal for an unchanged block, so it is not re-sent", () => {
    expect(sameMerge(merge, { ...merge })).toBe(true);
  });
});
