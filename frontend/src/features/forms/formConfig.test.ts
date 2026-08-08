import { describe, expect, it } from "vitest";

import type { Form } from "../../lib/types";
import { buildFormConfigPatch, type FormFieldRow } from "./formConfig";

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
});
