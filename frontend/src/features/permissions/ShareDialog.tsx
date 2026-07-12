import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Dialog } from "primereact/dialog";
import { Dropdown } from "primereact/dropdown";
import { MultiSelect } from "primereact/multiselect";
import { Button } from "primereact/button";
import { Checkbox } from "primereact/checkbox";
import { Message } from "primereact/message";

import { api, ApiError } from "../../lib/api";
import { useDebounced } from "../../lib/useDebounced";
import { useToast } from "../../lib/toast";
import { ACTIONS, ROLE_PRESETS } from "../../lib/permissions";
import type {
  ApiUser,
  ApiUserGroup,
  Grant,
  PermissionAction,
  ResourceType,
} from "../../lib/types";

interface Props {
  visible: boolean;
  onHide: () => void;
  resourceType: ResourceType;
  resourceUuid: string;
  resourceLabel: string;
  // Inheritance ("unique") control — omitted for workspaces (the root).
  inherit?: { value: boolean; onChange: (next: boolean) => void; busy?: boolean };
}

const ACTION_OPTIONS = ACTIONS.map((a) => ({ label: a.label, value: a.value }));

export function ShareDialog({
  visible,
  onHide,
  resourceType,
  resourceUuid,
  resourceLabel,
  inherit,
}: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const grantsKey = ["grants", resourceType, resourceUuid];
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: grantsKey });
    // Changing a grant changes what the caller (and everyone else) may do, so
    // drop the cached effective-action sets that gate the UI's controls.
    queryClient.invalidateQueries({ queryKey: ["effective"] });
  };

  const grants = useQuery({
    queryKey: grantsKey,
    queryFn: () =>
      api.get<Grant[]>(
        `/permissions/grants?resource_type=${resourceType}&resource_uuid=${resourceUuid}`,
      ),
    enabled: visible,
  });
  // Typed into the principal dropdown. Searching server-side matters here beyond
  // convenience: with a client-filtered single page, a user past the page limit
  // could not be granted anything at all.
  const [search, setSearch] = useState("");
  const q = useDebounced(search);

  const users = useQuery({
    queryKey: ["users", "search", q],
    queryFn: () =>
      api.get<ApiUser[]>(`/auth/users/?limit=50&q=${encodeURIComponent(q.trim())}`),
    enabled: visible,
  });
  const groups = useQuery({
    queryKey: ["groups", "search", q],
    queryFn: () =>
      api.get<ApiUserGroup[]>(
        `/auth/groups/?limit=50&q=${encodeURIComponent(q.trim())}`,
      ),
    enabled: visible,
  });

  const [principal, setPrincipal] = useState<string | null>(null);
  const [actions, setActions] = useState<PermissionAction[]>(["view"]);

  const upsert = useMutation({
    mutationFn: (vars: {
      kind: string;
      uuid: string;
      actions: PermissionAction[];
    }) =>
      api.post<Grant>("/permissions/grants", {
        resource_type: resourceType,
        resource_uuid: resourceUuid,
        actions: vars.actions,
        ...(vars.kind === "user"
          ? { user_uuid: vars.uuid }
          : { group_uuid: vars.uuid }),
      }),
    onSuccess: () => {
      invalidate();
      setPrincipal(null);
      setActions(["view"]);
    },
    onError: (err) =>
      toast.error(
        "Could not save access",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  const remove = useMutation({
    mutationFn: (grantUuid: string) => api.del(`/permissions/grants/${grantUuid}`),
    onSuccess: invalidate,
    onError: (err) =>
      toast.error(
        "Could not remove access",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  const grantedUsers = new Set(
    (grants.data ?? []).filter((g) => g.user_uuid).map((g) => g.user_uuid!),
  );
  const grantedGroups = new Set(
    (grants.data ?? []).filter((g) => g.group_uuid).map((g) => g.group_uuid!),
  );
  const principalGroups = [
    {
      label: "Users",
      items: (users.data ?? [])
        .filter((u) => !grantedUsers.has(u.uuid))
        .map((u) => ({ label: u.username, value: `user:${u.uuid}` })),
    },
    {
      label: "Groups",
      items: (groups.data ?? [])
        .filter((g) => !grantedGroups.has(g.uuid))
        .map((g) => ({ label: g.name, value: `group:${g.uuid}` })),
    },
  ];

  function onAdd() {
    if (!principal || actions.length === 0) return;
    const [kind, uuid] = principal.split(":");
    upsert.mutate({ kind, uuid, actions });
  }

  return (
    <Dialog
      header={`Share “${resourceLabel}”`}
      visible={visible}
      onHide={onHide}
      style={{ width: 560 }}
    >
      <div className="flex flex-col gap-4">
        {inherit && (
          <div className="flex items-start gap-2">
            <Checkbox
              inputId="unique-perms"
              checked={!inherit.value}
              disabled={inherit.busy}
              onChange={(e) => inherit.onChange(!e.checked)}
            />
            <label htmlFor="unique-perms" className="text-sm">
              <span className="font-medium">Unique permissions</span>
              <br />
              <span style={{ color: "var(--text-muted)" }}>
                Ignore access inherited from the parent; use only the grants below.
              </span>
            </label>
          </div>
        )}

        {/* Add a person or group */}
        <div className="flex flex-col gap-2">
          <span className="text-sm font-medium">Add people or groups</span>
          <Dropdown
            value={principal}
            options={principalGroups}
            optionGroupLabel="label"
            optionGroupChildren="items"
            onChange={(e) => setPrincipal(e.value)}
            placeholder="Select a user or group…"
            filter
            // Drive the server-side query; the options are already narrowed, so
            // don't also filter them locally.
            filterMatchMode="contains"
            onFilter={(e) => setSearch(e.filter)}
            resetFilterOnHide
            emptyFilterMessage="No matching users or groups"
            className="w-full"
          />
          <div className="flex flex-wrap items-center gap-2">
            {ROLE_PRESETS.map((preset) => (
              <Button
                key={preset.label}
                label={preset.label}
                size="small"
                outlined
                onClick={() => setActions(preset.actions)}
              />
            ))}
            <MultiSelect
              value={actions}
              options={ACTION_OPTIONS}
              onChange={(e) => setActions(e.value)}
              placeholder="Actions"
              display="chip"
              className="flex-1"
              style={{ minWidth: 220 }}
            />
            <Button
              label="Add"
              icon="pi pi-plus"
              onClick={onAdd}
              loading={upsert.isPending}
              disabled={!principal || actions.length === 0}
            />
          </div>
        </div>

        {/* Existing access */}
        <div className="flex flex-col gap-2">
          <span className="text-sm font-medium">People with access</span>
          {grants.isLoading && <p>Loading…</p>}
          {grants.data?.length === 0 && (
            <Message
              severity="info"
              text="No direct grants yet. The owner always has full access."
            />
          )}
          {grants.data?.map((grant) => {
            const isUser = !!grant.user_uuid;
            const name = isUser ? grant.user_username : grant.group_name;
            const kind = isUser ? "user" : "group";
            const uuid = (grant.user_uuid ?? grant.group_uuid)!;
            return (
              <div key={grant.uuid} className="flex items-center gap-2">
                <i className={isUser ? "pi pi-user" : "pi pi-users"} />
                <span className="flex-1 truncate">{name}</span>
                <MultiSelect
                  value={grant.actions}
                  options={ACTION_OPTIONS}
                  onChange={(e) =>
                    upsert.mutate({ kind, uuid, actions: e.value })
                  }
                  display="chip"
                  style={{ maxWidth: 300 }}
                />
                <Button
                  icon="pi pi-trash"
                  text
                  severity="danger"
                  aria-label={`Remove ${name}`}
                  onClick={() => remove.mutate(grant.uuid)}
                />
              </div>
            );
          })}
        </div>

        <div className="flex justify-end">
          <Button label="Done" text onClick={onHide} />
        </div>
      </div>
    </Dialog>
  );
}
