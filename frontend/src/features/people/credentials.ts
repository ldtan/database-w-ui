import { toCsv } from "../../lib/csv";
import type { CreatedUser } from "../../lib/types";

/**
 * The one-time credentials file, for the accounts whose passwords the server minted.
 *
 * Rows the admin supplied a password for are left out: they already know it, and the
 * server never sends it back. Returns null when there is nothing to hand over, which
 * is what the dialog keys its download button off — an empty file with a header row
 * would look like a bug.
 *
 * This is the only place these passwords are ever written down. They are not stored
 * server-side and cannot be requested again; a lost one is a password reset.
 */
export function credentialsCsv(created: CreatedUser[]): string | null {
  const rows = created
    .filter((user) => !!user.generated_password)
    .map((user) => [user.username, user.generated_password as string]);
  return rows.length ? toCsv(["username", "password"], rows) : null;
}
