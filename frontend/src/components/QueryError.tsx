import { Button } from "primereact/button";

import { ApiError } from "../lib/api";
import { EmptyState } from "./EmptyState";

interface Props {
  /** What could not be loaded, as a noun phrase: `records`, `the columns`. */
  what: string;
  /** The query's `error`. Anything; only an `Error` contributes a detail line. */
  error?: unknown;
  /** The query's `refetch`, when the caller has one to offer. */
  onRetry?: () => void;
}

/**
 * A failed request, said as a failure.
 *
 * The app had no way to say this. `retry: false` is the right default here —
 * most failures are 401/403/404, which are *answers* — but nothing rendered the
 * answer, so every failed query fell through to whatever the component drew for
 * *no data*. The result was that stopping the API and reloading a list produced
 * "No records yet — Add your first record with the button above.", and losing
 * access to a list produced "This list has no columns yet." Both are inviting,
 * confident, and wrong: they describe an empty list, and the one thing the user
 * needs to know is that they are not looking at their data.
 *
 * Deliberately **not** a global `QueryCache({ onError })` toast. Several queries
 * in this app are *supposed* to fail and degrade: `LookupCell` falls back to uuid
 * stubs when the reader cannot see the target list, `ColumnDialog` reads a 403 on
 * the target's columns as `targetSchemaHidden`, and `useEffectiveActions` treats a
 * refusal as "no actions". Those are documented, routine, and per-cell — a blanket
 * toast would fire a stack of them for one ordinary lookup grid. So the fix is
 * aimed at the surfaces that were drawing a *lie*, and it is one component so they
 * cannot drift apart.
 */
export function QueryError({ what, error, onRetry }: Props) {
  const detail =
    error instanceof ApiError
      ? error.message
      : error instanceof Error
        ? // A network failure is a bare TypeError whose message ("Failed to
          // fetch") says nothing a user can act on; name the likely cause instead.
          "The server could not be reached. Check your connection and try again."
        : undefined;

  return (
    <EmptyState
      icon="pi pi-exclamation-triangle"
      title={`Could not load ${what}`}
      text={detail}
      action={
        onRetry ? (
          <Button label="Try again" icon="pi pi-refresh" text onClick={onRetry} />
        ) : undefined
      }
    />
  );
}
