import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "../../lib/api";
import type { PermissionAction, ResourceType } from "../../lib/types";

export type ActionSet = ReadonlySet<PermissionAction>;

const NONE: ActionSet = new Set<PermissionAction>();

// The caller's effective actions on one resource. Until it resolves the set is
// empty, so controls stay hidden rather than flashing in and then away.
export function useEffectiveActions(
  resourceType: ResourceType,
  resourceUuid: string | null | undefined,
): ActionSet {
  const query = useQuery({
    queryKey: ["effective", resourceType, resourceUuid],
    enabled: !!resourceUuid,
    queryFn: () =>
      api.get<{ actions: PermissionAction[] }>(
        `/permissions/effective?resource_type=${resourceType}&resource_uuid=${resourceUuid}`,
      ),
  });
  // Memoised so the set is referentially stable while the answer is unchanged.
  // Callers pass it straight into `useEffectiveActionsMap` as the fallback and into
  // dependency arrays, and a fresh `Set` on every render makes every one of those
  // recompute for nothing.
  return useMemo(
    () => (query.data ? new Set(query.data.actions) : NONE),
    [query.data],
  );
}

interface ResourceActions {
  resource_type: ResourceType;
  resource_uuid: string;
  actions: PermissionAction[];
}

/**
 * Effective actions for a whole collection (a page of records, a workspace's
 * lists) in a single request — permissions can differ per row, so gating them
 * one query at a time would mean one round trip per row.
 */
export function useEffectiveActionsMap(
  resourceType: ResourceType,
  uuids: string[],
  fallbackActions?: ActionSet,
): (uuid: string, action: PermissionAction) => boolean {
  // Sorted so a reorder of the same rows reuses the cached response. Memoised on
  // the *contents* rather than the array identity, because callers rebuild the uuid
  // list on every render and this feeds both the query key and the memo below.
  const key = useMemo(() => [...uuids].sort(), [uuids.join(",")]);
  const query = useQuery({
    queryKey: ["effective", "batch", resourceType, key],
    enabled: key.length > 0,
    queryFn: () =>
      api.post<ResourceActions[]>("/permissions/effective", {
        resources: key.map((uuid) => ({
          resource_type: resourceType,
          resource_uuid: uuid,
        })),
      }),
    // Keep the last answer while a new one is in flight. The key is the *set* of
    // uuids, so adding or removing a single row re-keys the whole query and every
    // row would otherwise fall back to "no permissions" for the length of a round
    // trip — a page of records losing its row actions each time one is deleted,
    // and the toolbar's "Save view" vanishing and returning each time a view is
    // created. Answers are looked up by uuid below, so a carried-over response is
    // simply silent about a row it has never been asked about, which is what
    // `fallbackActions` is for.
    placeholderData: (prev) => prev,
  });

  // **Stable across renders, and that is load-bearing rather than tidy.** The grid
  // passes this predicate into every cell renderer, and PrimeReact memoises a body
  // cell against a fixed prop list that cannot see it — so a fresh closure on every
  // render is what forced `cellMemo` off, which in turn made every `setState` on the
  // page re-render every cell of every row. Rebuilt only when the answer actually
  // changes: the response, the set of rows asked about, or the fallback.
  //
  // `fallbackActions` is a `Set` the caller usually rebuilds too, so it is depended
  // on by content for the same reason `key` is. Both are small.
  const fallbackKey = fallbackActions ? [...fallbackActions].sort().join(",") : "";
  return useMemo(() => {
    const byUuid = new Map<string, ActionSet>();
    for (const row of query.data ?? []) {
      byUuid.set(row.resource_uuid, new Set(row.actions));
    }
    return (uuid: string, action: PermissionAction) => {
      const rowActions = byUuid.get(uuid);
      if (rowActions) return rowActions.has(action);
      return fallbackActions?.has(action) ?? false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.data, key, fallbackKey]);
}
