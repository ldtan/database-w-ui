import { describe, expect, it } from "vitest";

import { isEmail, mailtoHref, normalizeEmail } from "./email";

describe("normalizeEmail", () => {
  it("matches the stored form the server writes", () => {
    expect(normalizeEmail("  Ada.Lovelace@Example.COM  ")).toBe(
      "ada.lovelace@example.com",
    );
  });
});

describe("isEmail", () => {
  it.each(["ada@example.com", "  Ada@Example.COM  ", "a+b@sub.example.co.uk"])(
    "accepts %s",
    (value) => {
      expect(isEmail(value)).toBe(true);
    },
  );

  it.each([
    "not-an-address",
    "@example.com",
    "ada@",
    "ada@example",
    "ada@@example.com",
    "ada lovelace@example.com",
    "",
    null,
    42,
  ])("rejects %s", (value) => {
    expect(isEmail(value)).toBe(false);
  });
});

describe("mailtoHref", () => {
  it("builds the href from the normalised address", () => {
    expect(mailtoHref("  Ada@Example.COM  ")).toBe("mailto:ada@example.com");
  });

  it("is null for anything it will not link", () => {
    // Which is what keeps the scheme ours: a cell can never choose it, because a
    // value that is not an address never reaches an href at all.
    expect(mailtoHref("javascript:alert(1)")).toBeNull();
    expect(mailtoHref("not-an-address")).toBeNull();
    expect(mailtoHref(null)).toBeNull();
  });
});
