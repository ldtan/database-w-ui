import { describe, expect, it } from "vitest";

import {
  byteLength,
  DENSE_VERSION_FROM,
  encode,
  payloadText,
  QR_UUID_KEY,
  TOO_LONG,
} from "./qr";

describe("payloadText", () => {
  it("keeps the server's key order, so the dialog shows what the code encodes", () => {
    const payload = { [QR_UUID_KEY]: "01a1", full_name: "Ana Cruz", status: "Active" };
    expect(payloadText(payload)).toBe(
      '{"_uuid":"01a1","full_name":"Ana Cruz","status":"Active"}',
    );
  });

  it("does not pretty-print — whitespace is bytes, and bytes are modules", () => {
    expect(payloadText({ a: 1 })).not.toContain("\n");
  });
});

describe("byteLength", () => {
  it("counts bytes, not UTF-16 units", () => {
    // The case that makes `string.length` wrong in a way that looks right: a name with an
    // accent is the ordinary input here, not the exotic one.
    expect("Café".length).toBe(4);
    expect(byteLength("Café")).toBe(5);
    expect(byteLength("Ana Cruz")).toBe(8);
  });

  it("counts an emoji as the four bytes it is", () => {
    expect(byteLength("🎫")).toBe(4);
  });
});

describe("encode", () => {
  it("encodes a typical badge payload into a sparse, scannable code", async () => {
    const text = payloadText({
      [QR_UUID_KEY]: "01a10e25-6758-7338-b513-88e76862315b",
      full_name: "Ana Cruz",
      status: "Active",
    });
    const result = await encode(text);
    expect(result.dataUrl.startsWith("data:image/png;base64,")).toBe(true);
    expect(result.svg).toContain("<svg");
    // A real badge should be nowhere near the density limit; if this ever climbs, the
    // payload or the defaults changed.
    expect(result.version).toBeLessThan(DENSE_VERSION_FROM);
    expect(result.dense).toBe(false);
    // Version 1 is 21 modules and each version adds 4.
    expect(result.modules).toBe(17 + 4 * result.version);
  });

  it("reports density before it reports failure", async () => {
    // The point of the advisory: a code can be perfectly encodable and still too dense to
    // scan off a printed badge, so the warning has to fire well before the error does.
    const result = await encode(payloadText({ notes: "x".repeat(900) }));
    expect(result.version).toBeGreaterThanOrEqual(DENSE_VERSION_FROM);
    expect(result.dense).toBe(true);
  });

  it("turns an over-capacity payload into advice rather than a library error", async () => {
    await expect(encode(payloadText({ notes: "x".repeat(3000) }))).rejects.toThrow(
      TOO_LONG,
    );
  });

  it("names the fix in the message", () => {
    // The library's own wording is accurate and tells nobody what to do; the thing to do
    // is always to take a column off.
    expect(TOO_LONG).toMatch(/remove a column/i);
  });
});
