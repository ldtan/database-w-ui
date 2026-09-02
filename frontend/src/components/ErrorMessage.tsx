import { Message } from "primereact/message";

import { ApiError } from "../lib/api";

/**
 * A failed mutation, said inside the dialog that failed.
 *
 * Replaces `{x.error instanceof ApiError && <Message text={x.error.message} />}`,
 * which was the shape in every dialog and which is wrong for the failures a user is
 * most likely to meet. `fetch` rejects with a plain `TypeError` when the browser is
 * offline, when DNS fails, when CORS refuses, and when the request is aborted — none
 * of which are an `ApiError`. So the guard was false, nothing rendered, and since
 * none of these dialogs had an `onError` either, pressing Save produced a spinner
 * that stopped and absolutely nothing else. The dialog stays open with the form
 * intact, which looks exactly like a button that does not work, so people press it
 * again.
 *
 * Renders nothing when there is no error, so it can sit unconditionally at the top
 * of a dialog body.
 *
 * Note the toast call sites elsewhere in the app are a *different* shape and are
 * fine: `toast.error("Could not save", err instanceof ApiError ? err.message :
 * undefined)` still fires the toast for any error and uses the check only to decide
 * whether to add a detail line.
 */
export function ErrorMessage({ error }: { error: unknown }) {
  if (!error) return null;
  const text =
    error instanceof ApiError
      ? error.message
      : "Could not reach the server. Check your connection and try again.";
  return <Message severity="error" text={text} />;
}

/** The same sentence, for a caller that takes a string rather than a node. */
export function errorText(error: unknown): string | null {
  if (!error) return null;
  return error instanceof ApiError
    ? error.message
    : "Could not reach the server. Check your connection and try again.";
}
