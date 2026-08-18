/** Query keys for metrics, and the one decision behind them.
 *
 * A metric's *definition* is its own resource, so it has its own key. Its
 * *value* is not: it is a read of the list's records, and it goes stale at
 * exactly the moment they do — a record added, edited, deleted, restored or
 * imported changes every total on screen.
 *
 * So the values key is nested **under the items key**. Every
 * `invalidateQueries({ queryKey: ["items", listUuid] })` already in the app
 * therefore refreshes the cards too, by prefix match, and a future call site
 * that adds a record gets it right without knowing metrics exist. The
 * alternative — a sibling key and a second `invalidateQueries` beside each of
 * the existing ones — is the kind of pairing that is correct on the day it is
 * written and quietly wrong six call sites later.
 *
 * The literal `"metrics"` in the values key is what keeps that prefix a
 * namespace rather than a collision: it names this query among the others living
 * under `["items", listUuid]`, so a cache *write* aimed at one of them cannot
 * also be handed this one. `recordKeys` states the rule from the other side.
 */
import type { FilterGroup } from "../../lib/types";

export const metricsKey = (listUuid: string) => ["metrics", listUuid] as const;

export const metricValuesKey = (
  listUuid: string,
  viewUuid: string | null,
  q: string,
  filters: FilterGroup,
) => ["items", listUuid, "metrics", viewUuid, q, filters] as const;

/** Every metric-values query for this list, whatever scope it was asked in — the
 * prefix `metricValuesKey` sits under. See `recordCountsKey` for why this is spelled
 * beside its key rather than at the one call site that needs it (`useLiveList`). */
export const metricValuesPrefix = (listUuid: string) =>
  ["items", listUuid, "metrics"] as const;
