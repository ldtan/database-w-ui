/** Email cells: the address as it is stored, and the href it renders as.
 *
 *  The server is the enforcement point — `validate_scalar` refuses a malformed
 *  address and `normalize_email` decides the stored form. This module exists for
 *  the two things that happen on the client anyway: rendering an address that is
 *  already in a cell (which may predate the column being an `email` column at all,
 *  if it was converted from `text`), and showing someone their mistake before they
 *  submit it.
 */

// The same check as `_EMAIL_RE` in backend/src/lists/columns/validation.py, and
// deliberately as loose: one `@`, something either side, a dot in the host, no
// whitespace. Kept in step with it — a stricter rule here would reject a value the
// server accepts and refuse to render a cell that is legitimately stored.
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/** The stored form of an address: trimmed and lowercased, as `normalize_email` does. */
export function normalizeEmail(value: string): string {
  return value.trim().toLowerCase();
}

export function isEmail(value: unknown): value is string {
  return typeof value === "string" && EMAIL_RE.test(normalizeEmail(value));
}

/** The `mailto:` URL for a cell, or null if it isn't an address we'll link.
 *
 *  Not routed through `safeHref`: that reads the column's `allowed_schemes`, which
 *  on an email column is unset and therefore means http(s) — so it would reject
 *  every `mailto:` it was handed. The scheme here is ours rather than the value's,
 *  which is also what makes this safe: the address is checked before it is used,
 *  so nothing a cell holds can choose the scheme.
 */
export function mailtoHref(value: unknown): string | null {
  if (!isEmail(value)) return null;
  return `mailto:${normalizeEmail(value)}`;
}
