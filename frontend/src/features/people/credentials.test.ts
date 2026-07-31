import { describe, expect, it } from "vitest";

import type { CreatedUser } from "../../lib/types";
import { credentialsCsv } from "./credentials";

function created(over: Partial<CreatedUser> = {}): CreatedUser {
  return {
    row: 2,
    uuid: "u1",
    username: "ada",
    generated_password: "s3cret",
    ...over,
  } as CreatedUser;
}

describe("credentialsCsv", () => {
  it("writes a row per generated password", () => {
    const csv = credentialsCsv([
      created(),
      created({ username: "grace", generated_password: "hunter2" }),
    ]);
    expect(csv).toBe("username,password\r\nada,s3cret\r\ngrace,hunter2");
  });

  it("leaves out rows whose password the admin supplied", () => {
    // The server never sends those back, so there is nothing to hand over.
    const csv = credentialsCsv([
      created(),
      created({ username: "grace", generated_password: null }),
    ]);
    expect(csv).toBe("username,password\r\nada,s3cret");
  });

  it("returns null when nothing was generated", () => {
    // What the dialog keys the download button off.
    expect(credentialsCsv([created({ generated_password: null })])).toBeNull();
    expect(credentialsCsv([])).toBeNull();
  });

  it("escapes a username that would otherwise break the file", () => {
    const csv = credentialsCsv([created({ username: 'a,b"c' })]);
    expect(csv).toBe('username,password\r\n"a,b""c",s3cret');
  });
});
