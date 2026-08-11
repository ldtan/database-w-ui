import { describe, expect, it } from "vitest";

import type { ImportRowError } from "../../lib/types";
import { importErrorsCsv } from "./importErrors";

function failure(over: Partial<ImportRowError> = {}): ImportRowError {
  return {
    row: 2,
    message: "column 'age': 'x' is not a number",
    values: { name: "Ann", age: "x" },
    ...over,
  } as ImportRowError;
}

describe("importErrorsCsv", () => {
  it("reproduces each failed row behind its line number and reason", () => {
    const csv = importErrorsCsv([failure()]);
    expect(csv).toBe(
      "_row,_error,name,age\r\n" + "2,column 'age': 'x' is not a number,Ann,x",
    );
  });

  it("keeps every header any failed row had, in file order", () => {
    // The second row's `note` matches no column and is ignored on import — but the
    // fix file must still hand it back, or re-uploading would lose it.
    const csv = importErrorsCsv([
      failure(),
      failure({ row: 3, message: "dup", values: { name: "Bo", note: "keep" } }),
    ]);
    const lines = csv!.split("\r\n");
    expect(lines[0]).toBe("_row,_error,name,age,note");
    expect(lines[2]).toBe("3,dup,Bo,,keep"); // no age, no note on the first row
  });

  it("writes a row whose cells could not be reproduced", () => {
    // A bare scalar in a JSON file. Still skipped, so still accounted for.
    const csv = importErrorsCsv([failure(), failure({ row: 4, values: {} })]);
    expect(csv!.split("\r\n")[2]).toBe("4,column 'age': 'x' is not a number,,");
  });

  it("returns null when no row carries cells", () => {
    // What the dialog keys the download button off.
    expect(importErrorsCsv([failure({ values: {} })])).toBeNull();
    expect(importErrorsCsv([])).toBeNull();
  });
});
