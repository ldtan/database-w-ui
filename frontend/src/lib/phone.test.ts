import { describe, expect, it } from "vitest";

import {
  countryOptions,
  formatAsTyped,
  formatPhone,
  normalizePhoneQuery,
  phoneFormat,
  phonePlaceholder,
  phoneRegion,
  telHref,
} from "./phone";
import type { Column } from "./types";

function column(config: Record<string, unknown>): Column {
  return {
    uuid: "c1",
    name: "mobile",
    display_name: "Mobile",
    type: "phone",
    config,
  } as Column;
}

const PH = "+639171234567";
const US = "+12125551234";

describe("phoneFormat", () => {
  it("defaults to international, the only format safe for mixed countries", () => {
    expect(phoneFormat(column({}))).toBe("international");
    expect(phoneFormat(column({ display_format: "shouty" }))).toBe("international");
    expect(phoneFormat(column({ display_format: "e164" }))).toBe("e164");
  });
});

describe("phoneRegion", () => {
  it("normalises and checks the code against the metadata", () => {
    expect(phoneRegion(column({ default_region: "ph" }))).toBe("PH");
    expect(phoneRegion(column({ default_region: "  PH  " }))).toBe("PH");
    // A code this build does not know must not reach the library, or a cell throws.
    expect(phoneRegion(column({ default_region: "ZZ" }))).toBeUndefined();
    expect(phoneRegion(column({ default_region: "PHL" }))).toBeUndefined();
    expect(phoneRegion(column({}))).toBeUndefined();
  });
});

describe("formatPhone", () => {
  it("writes the stored E.164 the way the column asks", () => {
    expect(formatPhone(column({ display_format: "e164" }), PH)).toBe(PH);
    expect(formatPhone(column({}), PH)).toBe("+63 917 123 4567");
    expect(
      formatPhone(column({ display_format: "national", default_region: "PH" }), PH),
    ).toBe("0917 123 4567");
  });

  it("falls back to international for a number outside the column's country", () => {
    // A US number printed in PH national form would be a number nobody can dial.
    expect(
      formatPhone(column({ display_format: "national", default_region: "PH" }), US),
    ).toBe("+1 212 555 1234");
  });

  it("passes through anything it cannot parse rather than blanking the cell", () => {
    // A cell can hold a value written before the column was a phone column.
    expect(formatPhone(column({}), "call the front desk")).toBe("call the front desk");
    expect(formatPhone(column({}), null)).toBe("");
  });
});

describe("telHref", () => {
  it("always dials E.164, whatever the column shows", () => {
    expect(telHref(PH)).toBe(`tel:${PH}`);
  });

  it("is null for anything it will not link", () => {
    // Which is what keeps the scheme ours rather than the value's.
    expect(telHref("javascript:alert(1)")).toBeNull();
    expect(telHref("call the front desk")).toBeNull();
    expect(telHref(null)).toBeNull();
  });
});

describe("formatAsTyped", () => {
  it("spaces a number as it is typed", () => {
    expect(formatAsTyped(column({ default_region: "PH" }), "09171234567")).toBe(
      "0917 123 4567",
    );
  });

  it("only ever reformats — never adds or drops a digit", () => {
    // The property that makes it safe on every keystroke: what is submitted is what
    // was typed, so the server still has the last word.
    for (const typed of ["0917", "091712", "+6391712", "12345678901234"]) {
      const formatted = formatAsTyped(column({ default_region: "PH" }), typed);
      expect(formatted.replace(/\D/g, "")).toBe(typed.replace(/\D/g, ""));
    }
  });
});

describe("phonePlaceholder", () => {
  it("gives a real example for the column's own country", () => {
    const national = phonePlaceholder(
      column({ default_region: "GB", display_format: "national" }),
    );
    expect(national.replace(/\D/g, "")).not.toBe("");
    expect(national.startsWith("+")).toBe(false);

    expect(
      phonePlaceholder(column({ default_region: "GB" })).startsWith("+44"),
    ).toBe(true);
  });

  it("never suggests a local number on a column that would refuse one", () => {
    // With no region the server takes only numbers carrying their country code, so
    // the hint has to show one.
    expect(phonePlaceholder(column({})).startsWith("+")).toBe(true);
    expect(
      phonePlaceholder(column({ display_format: "national" })).startsWith("+"),
    ).toBe(true);
  });

  it("falls back to allowed_regions when no default is set", () => {
    expect(
      phonePlaceholder(column({ allowed_regions: ["GB"] })).startsWith("+44"),
    ).toBe(true);
  });
});

describe("normalizePhoneQuery", () => {
  it("rewrites a whole number to the form the comparison will see", () => {
    // Cells hold E.164, so a condition typed the way somebody says a number out
    // loud would otherwise match nothing — silently, on the one type where the
    // stored form and the spoken form always differ.
    expect(normalizePhoneQuery(column({ default_region: "PH" }), "0917 123 4567")).toBe(
      PH,
    );
    expect(normalizePhoneQuery(column({}), "+63 917 123 4567")).toBe(PH);
  });

  it("leaves a fragment alone, so `contains` still works", () => {
    for (const fragment of ["917", "+63", "0917 12", "", "  "]) {
      expect(normalizePhoneQuery(column({ default_region: "PH" }), fragment)).toBe(
        fragment,
      );
    }
  });
});

describe("countryOptions", () => {
  it("offers every country the metadata knows, sorted, and is stable", () => {
    const first = countryOptions();
    expect(first.length).toBeGreaterThan(200);
    expect(first.some((o) => o.value === "PH")).toBe(true);
    const labels = first.map((o) => o.label);
    expect([...labels].sort((a, b) => a.localeCompare(b))).toEqual(labels);
    // Memoised: the same array, not an equal one.
    expect(countryOptions()).toBe(first);
  });
});
