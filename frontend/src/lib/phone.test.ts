import { describe, expect, it } from "vitest";

import {
  allowedRegions,
  cleanPasted,
  composePhone,
  countryChoices,
  countryOptions,
  formatAsTyped,
  formatPhone,
  initialRegion,
  normalizePhoneQuery,
  phoneCountries,
  phoneFormat,
  phonePlaceholder,
  phoneRegion,
  phoneStatus,
  splitPhone,
  telHref,
  typedRegion,
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

describe("allowedRegions", () => {
  it("normalises the way the server's `phone_regions` does", () => {
    expect(allowedRegions(column({ allowed_regions: [" ph ", "us"] }))).toEqual([
      "PH",
      "US",
    ]);
    expect(allowedRegions(column({}))).toEqual([]);
    // A code this build does not know is dropped rather than passed to the library:
    // a config written by a newer server must not narrow the picker to nothing.
    expect(allowedRegions(column({ allowed_regions: ["PH", "ZZ", "", 7] }))).toEqual([
      "PH",
    ]);
  });
});

describe("phoneCountries", () => {
  it("labels every country with the code the picker actually contributes", () => {
    const all = phoneCountries(column({}));
    expect(all.length).toBeGreaterThan(200);
    expect(all.find((o) => o.value === "PH")?.label).toContain("(+63)");
  });

  it("offers only what the column accepts", () => {
    // Offering a country the server will refuse is offering a mistake.
    const narrowed = phoneCountries(column({ allowed_regions: ["PH", "US"] }));
    expect(narrowed.map((o) => o.value).sort()).toEqual(["PH", "US"]);
  });

  it("puts the column's own country first and leaves the rest alphabetical", () => {
    const list = phoneCountries(column({ default_region: "PH" }));
    expect(list[0].value).toBe("PH");
    const rest = list.slice(1).map((o) => o.label);
    expect([...rest].sort((a, b) => a.localeCompare(b))).toEqual(rest);
  });

  it("is memoised per config, not recomputed per render", () => {
    expect(phoneCountries(column({ default_region: "GB" }))).toBe(
      phoneCountries(column({ default_region: "GB" })),
    );
  });

  it("hoists nothing when the default is not on offer", () => {
    // A column can hold a default outside its own allow-list: the server refuses
    // that config, but the dialog can be mid-edit and must not throw on it.
    expect(countryChoices(["US"], "PH").map((o) => o.value)).toEqual(["US"]);
  });
});

describe("splitPhone", () => {
  it("pulls a stored number into the country and the digits people say", () => {
    expect(splitPhone(PH)).toEqual({ region: "PH", national: "0917 123 4567" });
    expect(splitPhone(US)).toEqual({ region: "US", national: "(212) 555-1234" });
  });

  it("keeps an unparseable cell whole rather than emptying the box", () => {
    expect(splitPhone("call the front desk")).toEqual({
      national: "call the front desk",
    });
    expect(splitPhone(null)).toEqual({ national: "" });
    expect(splitPhone("")).toEqual({ national: "" });
  });
});

describe("composePhone", () => {
  it("round-trips a stored number through the editor unchanged", () => {
    // The property the whole control rests on: what the box shows and the country
    // beside it add back up to the value that was in the cell.
    for (const stored of [PH, US, "+442071838750"]) {
      const { region, national } = splitPhone(stored);
      expect(composePhone(region, national)).toBe(stored);
    }
  });

  it("joins through the parser, not through concatenation", () => {
    // `0917 123 4567` is `+63 917 123 4567`, not `+63 0917 123 4567` — the national
    // prefix is not part of the number, and gluing the dial code on the front would
    // produce a refusal that looks like the picker's fault.
    expect(composePhone("PH", "0917 123 4567")).toBe(PH);
    expect(composePhone("PH", "09171234567")).toBe(PH);
  });

  it("lets a typed country code outrank the dropdown", () => {
    expect(composePhone("PH", "+1 212 555 1234")).toBe(US);
  });

  it("adds nothing when there is nothing to add to", () => {
    expect(composePhone(undefined, "0917 123 4567")).toBe("0917 123 4567");
    expect(composePhone("PH", "")).toBe("");
    // Not a number at all: handed back as typed, so the server's complaint names
    // what was written rather than a bare "+63".
    expect(composePhone("PH", "front desk")).toBe("front desk");
  });
});

describe("typedRegion", () => {
  it("answers only for text that has named its own country", () => {
    expect(typedRegion("+63 917")).toBe("PH");
    expect(typedRegion("0917 123 4567")).toBeUndefined();
    // `+1` is shared by two dozen countries; undefined is the library being honest
    // rather than a gap, and the dropdown simply stays where it was.
    expect(typedRegion("+1 212")).toBeUndefined();
  });
});

describe("initialRegion", () => {
  it("takes the country from the value in the cell first", () => {
    expect(initialRegion(column({ default_region: "PH" }), US)).toBe("US");
  });

  it("falls back to the column, then to its only allowed country", () => {
    expect(initialRegion(column({ default_region: "PH" }), null)).toBe("PH");
    expect(initialRegion(column({ allowed_regions: ["GB"] }), null)).toBe("GB");
  });

  it("guesses nothing when the column has not said", () => {
    // The whole point: a locale-guessed US would read `9171234567` as a real New
    // York number for whoever meant a Manila mobile. `normalize_phone` refuses to
    // guess on the server, and this refuses in front of it.
    expect(initialRegion(column({}), null)).toBeUndefined();
    expect(
      initialRegion(column({ allowed_regions: ["PH", "US"] }), null),
    ).toBeUndefined();
  });
});

describe("cleanPasted", () => {
  it("strips the wrapping a pasted number arrives in", () => {
    expect(cleanPasted("tel:+639171234567")).toBe("+639171234567");
    expect(cleanPasted("  (0917) 123-4567  ")).toBe("(0917) 123-4567");
  });

  it("rewrites separators without touching a digit", () => {
    // The same property `formatAsTyped` holds: a paste is not a place to invent.
    for (const raw of [
      "+63 917–123—4567",
      "＋６３ 917 123 4567",
      "tel://+63 917 123 4567",
    ]) {
      const cleaned = cleanPasted(raw);
      const digitsIn = raw
        .replace(/[０-９]/g, (d) =>
          String.fromCharCode(d.charCodeAt(0) - 0xfee0),
        )
        .replace(/\D/g, "");
      expect(cleaned.replace(/\D/g, "")).toBe(digitsIn);
      // And nothing exotic survives to reach the parser.
      expect(cleaned).not.toMatch(/[‐-― ＀-｠]/);
    }
  });
});

describe("phoneStatus", () => {
  const ph = column({ default_region: "PH" });

  it("says nothing about an empty box", () => {
    expect(phoneStatus(ph, "", "PH").state).toBe("empty");
    expect(phoneStatus(ph, "   ", "PH").message).toBe("");
  });

  it("names a finished number rather than merely allowing it", () => {
    // The check nobody can do by eye: that these digits are a real mobile line.
    const ok = phoneStatus(ph, "0917 123 4567", "PH");
    expect(ok.state).toBe("valid");
    expect(ok.region).toBe("PH");
    expect(ok.kind).toBe("Mobile");
    expect(ok.message).toContain("Mobile");
  });

  it("treats a number still being typed as a hint, never an error", () => {
    // A red ring around every number on its way to being right is how a validating
    // field becomes the thing people learn to ignore.
    for (const partial of ["0917", "0917 123"]) {
      expect(phoneStatus(ph, partial, "PH").state).toBe("partial");
    }
  });

  it("refuses what the server would refuse", () => {
    // Well-formed and still not dialable — the right length for no operator in that
    // country. This is the case `min` metadata cannot see, and the reason the module
    // is on `libphonenumber-js/max`; `test_rejects_what_is_not_a_number` is the
    // server-side half of it.
    expect(phoneStatus(ph, "900 000 0000", "PH").state).toBe("invalid");
    expect(phoneStatus(ph, "banana", "PH").state).toBe("invalid");
  });

  it("asks for a country before it calls anything wrong", () => {
    const none = phoneStatus(column({}), "0917 123 4567", undefined);
    expect(none.state).toBe("needsCountry");
    expect(none.message).toContain("country code");
  });

  it("blocks a country the column does not accept", () => {
    const narrowed = column({ allowed_regions: ["PH"] });
    const blocked = phoneStatus(narrowed, "+1 212 555 1234", "US");
    expect(blocked.state).toBe("blocked");
    expect(blocked.message).toContain("PH");
    expect(phoneStatus(narrowed, "+63 917 123 4567", "PH").state).toBe("valid");
  });
});
