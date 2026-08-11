import { toCsv } from "../../lib/csv";
import type { ImportRowError } from "../../lib/types";

/**
 * The skipped rows of an import, as a file to fix and upload again.
 *
 * The point of the download is that the fix loop stays in the spreadsheet the data
 * came from: each row is reproduced exactly as the uploaded file wrote it, so
 * correcting the cell the message complains about and re-importing is the whole
 * repair — no hunting for line 47 of the original.
 *
 * `_row` (the line it was on) and `_error` (why it was skipped) lead each row rather
 * than trailing it, so the reason is the first thing on screen instead of something
 * to scroll a wide file for. Both survive re-import untouched: import ignores headers
 * that match no column, and a column *cannot* be named `_error` — the backend's
 * `NameSlug` requires a leading lowercase letter — so neither can ever collide with
 * real data.
 *
 * Headers are the union across the failed rows, in the order the file introduced
 * them; a row missing one of them writes an empty cell, which is what import reads as
 * "leave this out" anyway.
 *
 * Returns null when no error carries any cells — an upload that failed outright, or a
 * JSON file of bare scalars. There is nothing to fix in a file then, and an empty one
 * with two headings would look like a bug. That is what the dialog keys its download
 * button off, exactly as `credentialsCsv` does.
 */
export function importErrorsCsv(errors: ImportRowError[]): string | null {
  const headers: string[] = [];
  for (const error of errors) {
    for (const key of Object.keys(error.values ?? {})) {
      if (!headers.includes(key)) headers.push(key);
    }
  }
  if (headers.length === 0) return null;

  // Valueless rows are still written — a file that is a complete account of what was
  // skipped beats one that quietly drops the failures it cannot reproduce.
  const rows = errors.map((error) => [
    String(error.row),
    error.message,
    ...headers.map((key) => error.values?.[key] ?? ""),
  ]);
  return toCsv(["_row", "_error", ...headers], rows);
}
