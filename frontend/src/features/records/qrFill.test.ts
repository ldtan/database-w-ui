/** `qrFill` — the first tests it has had.
 *
 *  It is deliberately not a validator (the backend is the only one), so there is little
 *  here about values. What there *is* worth pinning is the part that decides whether a key
 *  reaches the form at all, because that is the access rule — and the reserved `_`
 *  namespace, which is what lets a code this app generated scan back without complaining
 *  about its own metadata.
 *
 *  `shape()` is left alone: everything interesting it does needs a network call
 *  (`toReference`) or a column type's own helper, both of which are tested where they live.
 */
import { describe, expect, it } from "vitest";

import { fillFromQr, parseQrPayload } from "./qrFill";
import type { Column } from "../../lib/types";
import type { ResolvedField } from "./recordValues";

function field(
  name: string,
  overrides: { enabled?: boolean; type?: Column["type"] } = {},
): ResolvedField {
  return {
    column: {
      uuid: `uuid-${name}`,
      name,
      display_name: name,
      type: overrides.type ?? "text",
      position: 0,
      config: {},
    } as Column,
    enabled: overrides.enabled ?? true,
    label: name,
  };
}

describe("parseQrPayload", () => {
  it("explains a QR that is not JSON, rather than failing obscurely", () => {
    // A code that turns out to be a URL or a Wi-Fi credential is the common case, not a
    // corrupt one.
    expect(() => parseQrPayload("https://example.com")).toThrow(/does not contain JSON/);
  });

  it("refuses JSON that is not an object of fields", () => {
    expect(() => parseQrPayload("[1,2]")).toThrow(/must be an object/);
    expect(() => parseQrPayload("null")).toThrow(/must be an object/);
  });

  it("accepts a flat object", () => {
    expect(parseQrPayload('{"a":1}')).toEqual({ a: 1 });
  });
});

describe("fillFromQr", () => {
  it("fills by column name, keyed out by column uuid", async () => {
    const fill = await fillFromQr("L", [field("full_name")], { full_name: "Ana Cruz" });
    expect(fill.values).toEqual({ "uuid-full_name": "Ana Cruz" });
    expect(fill.filled).toEqual(["full_name"]);
    expect(fill.issues).toEqual([]);
  });

  it("accepts a column uuid as a key too, for a code a machine generated", async () => {
    const fill = await fillFromQr("L", [field("full_name")], {
      "uuid-full_name": "Ana Cruz",
    });
    expect(fill.values).toEqual({ "uuid-full_name": "Ana Cruz" });
  });

  it("matches a key case-insensitively and ignores surrounding space", async () => {
    const fill = await fillFromQr("L", [field("full_name")], {
      "  FULL_NAME ": "Ana Cruz",
    });
    expect(fill.values).toEqual({ "uuid-full_name": "Ana Cruz" });
  });

  it("reports a key the form has nowhere to put", async () => {
    const fill = await fillFromQr("L", [field("full_name")], { nickname: "Annie" });
    expect(fill.values).toEqual({});
    expect(fill.issues).toEqual([
      { key: "nickname", message: "there is no such field on this form" },
    ]);
  });

  it("reports a field the form marks read-only", async () => {
    const fill = await fillFromQr("L", [field("created_by", { enabled: false })], {
      created_by: "admin",
    });
    expect(fill.issues[0].message).toBe("this field is read-only");
  });

  it("treats an explicit blank as “leave it blank” rather than an issue", async () => {
    // This is what stops a code erasing a preset in a submitting session by carrying "".
    const fill = await fillFromQr("L", [field("full_name")], { full_name: "" });
    expect(fill.values).toEqual({});
    expect(fill.issues).toEqual([]);
    expect(fill.filled).toEqual([]);
  });

  describe("the reserved `_` namespace", () => {
    it("skips `_uuid` silently instead of reporting it", async () => {
      // The key this app's own generator writes. Reporting it would put a spurious "there
      // is no such field" on every scan of every generated code — and a column name cannot
      // begin with an underscore, so nothing legitimate is being hidden.
      const fill = await fillFromQr("L", [field("full_name")], {
        _uuid: "01a10e25-6758-7338-b513-88e76862315b",
        full_name: "Ana Cruz",
      });
      expect(fill.issues).toEqual([]);
      expect(fill.values).toEqual({ "uuid-full_name": "Ana Cruz" });
      expect(fill.filled).toEqual(["full_name"]);
    });

    it("skips any `_`-prefixed key, not just the one we write today", async () => {
      const fill = await fillFromQr("L", [field("full_name")], {
        _printed_at: "2026-10-06",
        _v: 2,
      });
      expect(fill.issues).toEqual([]);
      expect(fill.values).toEqual({});
    });

    it("still reports an ordinary unknown key alongside skipped metadata", async () => {
      // The skip must not turn into a general shrug: a typo is still worth reporting.
      const fill = await fillFromQr("L", [field("full_name")], {
        _uuid: "01a1",
        nickanme: "Annie",
      });
      expect(fill.issues).toEqual([
        { key: "nickanme", message: "there is no such field on this form" },
      ]);
    });
  });
});
