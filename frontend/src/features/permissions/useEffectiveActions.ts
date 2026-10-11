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
  // Memoised so the set is referentially stable while the answer is unchanged. A
  // small saving, honestly stated: one `new Set` per render, over a handful of
  // actions. Every consumer calls `.has(...)` in render and would not notice, and
  // the one caller that passes it on — as `useEffectiveActionsMap`'s fallback —
  // depends on its *contents* rather than its identity anyway.
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
 * tables) in a single request — permissions can differ per row, so gating them
 * one query at a time would mean one round trip per row.
 */
export function useEffectiveActionsMap(
  resourceType: ResourceType,
  uuids: string[],
  fallbackActions?: ActionSet,
): (uuid: string, action: PermissionAction) => boolean {
  // Sorted so a reorder of the same rows reuses the cached response. Memoised on
  // the *contents* rather than the array identity, because callers rebuild the uuid
  // table on every render and an unstable `key` would re-key the query every time.
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

  // Stable across renders, and deliberately not sold as more than it is. The grid
  // passes this predicate into every cell renderer, but this hook is called *once*
  // per component — a cell only calls the returned closure, which is a `Map.get`. So
  // memoising saves one Map of page-size per render of the caller, not one per cell.
  //
  // It is emphatically **not** the reason `TableDetailPage` runs with `cellMemo` off.
  // PrimeReact compares a body cell against twelve fixed prop names, and this
  // predicate is not among them, so its referential stability is invisible to that
  // memo either way. The note at `cellMemo={false}` has the argument in full; do not
  // come back here expecting a stable closure to be what lets the memo switch on.
  //
  // Rebuilt when the answer changes: the response, or the fallback. `fallbackActions`
  // is a `Set` the caller usually rebuilds each render, so it is depended on by
  // content rather than by identity. It is small.
  //
  // `key` is deliberately *not* a dependency. The closure never reads it, and
  // `placeholderData` keeps `query.data` referentially identical while a new key is
  // in flight — so depending on it would rebuild the Map for a pure row reorder and
  // arrive at the same answer. `query.data` is what moves when the answer does.
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
    // No `eslint-disable` here, and none above the `key` memo either: this repo has
    // no ESLint config, no plugin and no lint script, so a directive would be
    // documentation for a rule that never runs — and would imply these dep arrays are
    // mechanically checked when nothing checks them. The reasoning above is the guard.
  }, [query.data, fallbackKey]);
}
