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
  return query.data ? new Set(query.data.actions) : NONE;
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
  // Sorted so a reorder of the same rows reuses the cached response.
  const key = [...uuids].sort();
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

  const byUuid = new Map<string, ActionSet>();
  for (const row of query.data ?? []) {
    byUuid.set(row.resource_uuid, new Set(row.actions));
  }
  return (uuid, action) => {
    const rowActions = byUuid.get(uuid);
    if (rowActions) return rowActions.has(action);
    return fallbackActions?.has(action) ?? false;
  };
}
