import { describe, expect, it } from "vitest";

import type { Column } from "./types";
import { linkLabel, linkSchemes, safeHref } from "./links";

/**
 * `safeHref` is the last thing between a stored string and an `href`, and it had
 * no test.
 *
 * The backend refuses to store a URL whose scheme is outside the column's
 * `allowed_schemes`, itself capped at `SAFE_LINK_SCHEMES` — so a column cannot opt
 * back into `javascript:`. This function exists because that is not the same as "no
 * cell can hold one": rows predating a config change, an odd import, or a value
 * written before the check existed all reach the renderer anyway. known-gaps puts it
 * plainly — "a raw `href={value}` is stored XSS" — and this is the function that
 * makes that not happen. `tests/test_link_column.py` pins the server half; this is
 * the client half of the same boundary.
 */
function column(config: Record<string, unknown> = {}): Column {
  return {
    uuid: "c1",
    name: "site",
    display_name: "Site",
    type: "link",
    config,
  } as Column;
}

describe("safeHref", () => {
  it("passes an ordinary http and https URL", () => {
    expect(safeHref(column(), "https://example.com/a?b=c")).toBe(
      "https://example.com/a?b=c",
    );
    expect(safeHref(column(), "http://example.com")).toBe("http://example.com");
  });

  /** The whole reason the function exists. */
  it.each([
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "  javascript:alert(1)  ",
    "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
  ])("refuses %s", (value) => {
    expect(safeHref(column(), value)).toBeNull();
    // Even if a hand-edited config claims to allow it: SAFE_SCHEMES is the ceiling
    // and the column's list can only narrow it, never widen it.
    expect(
      safeHref(column({ allowed_schemes: ["javascript", "data", "vbscript", "file"] }), value),
    ).toBeNull();
  });

  it("refuses anything that is not a parseable absolute URL", () => {
    for (const value of ["", "   ", "example.com", "/relative/path", "://nope"]) {
      expect(safeHref(column(), value)).toBeNull();
    }
  });

  it("refuses a non-string cell", () => {
    for (const value of [null, undefined, 42, {}, ["https://example.com"]]) {
      expect(safeHref(column(), value)).toBeNull();
    }
  });

  it("honours a column narrowing the schemes it accepts", () => {
    const mailOnly = column({ allowed_schemes: ["mailto"] });
    expect(safeHref(mailOnly, "mailto:a@example.com")).toBe("mailto:a@example.com");
    // Allowed globally, but not by this column.
    expect(safeHref(mailOnly, "https://example.com")).toBeNull();
  });

  it("accepts the two other safe schemes when a column asks for them", () => {
    expect(safeHref(column({ allowed_schemes: ["tel"] }), "tel:+639171234567")).toBe(
      "tel:+639171234567",
    );
  });
});

describe("linkSchemes", () => {
  it("defaults to http and https when the column says nothing", () => {
    expect(linkSchemes(column())).toEqual(["http", "https"]);
    // An empty list is "unset", not "nothing allowed" — otherwise clearing the
    // control in the dialog would silently stop every link rendering.
    expect(linkSchemes(column({ allowed_schemes: [] }))).toEqual(["http", "https"]);
  });

  it("uses the column's list when it has one", () => {
    expect(linkSchemes(column({ allowed_schemes: ["mailto", "tel"] }))).toEqual([
      "mailto",
      "tel",
    ]);
  });
});

describe("linkLabel", () => {
  it("prefers the column's fixed label", () => {
    expect(linkLabel(column({ label: "Homepage" }), "https://x.test")).toBe(
      "Homepage",
    );
  });

  it("falls back to the URL when the label is absent or blank", () => {
    expect(linkLabel(column(), "https://x.test")).toBe("https://x.test");
    expect(linkLabel(column({ label: "   " }), "https://x.test")).toBe(
      "https://x.test",
    );
  });
});
