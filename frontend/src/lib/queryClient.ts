import { QueryClient } from "@tanstack/react-query";

/**
 * The app's one query client.
 *
 * Both defaults are deliberate departures from TanStack's:
 *
 * - **`retry: false`** — most failures here are 401/403/404, which are *answers*,
 *   not blips: retrying them three times only delays the message the user needs to
 *   see. (The API client already retries the one thing worth retrying, an expired
 *   access token, by refreshing once.)
 * - **`refetchOnWindowFocus: false`** — alt-tabbing back should not silently refetch
 *   a table you are mid-edit on.
 *
 * Freshness comes from explicit `invalidateQueries` after a mutation instead. Query
 * keys are shared across the app on purpose (`["records", tableUuid]`, `["columns",
 * tableUuid]`, `["person", "me"]`…), so an invalidation from a dialog updates the
 * page behind it, and two components asking the same question make one request.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      refetchOnWindowFocus: false,
    },
  },
});
