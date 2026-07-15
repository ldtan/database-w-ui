import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Dropdown } from "primereact/dropdown";
import { MultiSelect } from "primereact/multiselect";
import { Button } from "primereact/button";
import { Calendar } from "primereact/calendar";
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

export interface SharePanelProps {
  resourceType: ResourceType;
  resourceUuid: string;
  // Inheritance ("unique") control — omitted for workspaces (the root).
  inherit?: { value: boolean; onChange: (next: boolean) => void; busy?: boolean };
  // False while the panel is off-screen (a closed dialog, an inactive tab), so it
  // does not fetch grants nobody is looking at.
  active?: boolean;
}

const ACTION_OPTIONS = ACTIONS.map((a) => ({ label: a.label, value: a.value }));

/** How an expiry reads on a grant row: nothing, an "Expired" flag, or "in N days".
 *  The backend already stops honouring an expired grant; this is just the label. */
function expiryLabel(
  expiresAt: string | null | undefined,
): { text: string; expired: boolean } | null {
  if (!expiresAt) return null;
  const ms = new Date(expiresAt).getTime() - Date.now();
  if (ms <= 0) return { text: "Expired", expired: true };
  const days = Math.ceil(ms / 86_400_000);
  return { text: days <= 1 ? "Expires today" : `Expires in ${days}d`, expired: false };
}

/** Who may do what to one resource. The body of the Share dialog, split out so the
 *  list's own permissions can live inside List settings while records and forms
 *  still open it as a dialog of its own. */
export function SharePanel({
  resourceType,
  resourceUuid,
  inherit,
  active = true,
}: SharePanelProps) {
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
    enabled: active,
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
    enabled: active,
  });
  const groups = useQuery({
    queryKey: ["groups", "search", q],
    queryFn: () =>
      api.get<ApiUserGroup[]>(
        `/auth/groups/?limit=50&q=${encodeURIComponent(q.trim())}`,
      ),
    enabled: active,
  });

  const [principal, setPrincipal] = useState<string | null>(null);
  const [actions, setActions] = useState<PermissionAction[]>(["view"]);
  const [expiresAt, setExpiresAt] = useState<Date | null>(null);

  const upsert = useMutation({
    mutationFn: (vars: {
      kind: string;
      uuid: string;
      actions: PermissionAction[];
      // Sent verbatim: the backend writes it as given, so an edit that omits it
      // would clear the grant's expiry. Editing actions on an existing row passes
      // the grant's current value; the add form passes the picker's.
      expiresAt: string | null;
    }) =>
      api.post<Grant>("/permissions/grants", {
        resource_type: resourceType,
        resource_uuid: resourceUuid,
        actions: vars.actions,
        expires_at: vars.expiresAt,
        ...(vars.kind === "user"
          ? { user_uuid: vars.uuid }
          : { group_uuid: vars.uuid }),
      }),
    onSuccess: () => {
      invalidate();
      setPrincipal(null);
      setActions(["view"]);
      setExpiresAt(null);
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
    upsert.mutate({
      kind,
      uuid,
      actions,
      expiresAt: expiresAt ? expiresAt.toISOString() : null,
    });
  }

  return (
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
          <Calendar
            value={expiresAt}
            onChange={(e) => setExpiresAt((e.value as Date) ?? null)}
            minDate={new Date()}
            showButtonBar
            showIcon
            showTime
            hourFormat="24"
            dateFormat="yy-mm-dd"
            placeholder="Expires (optional)"
            className="w-64"
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
          const expiry = expiryLabel(grant.expires_at);
          return (
            <div key={grant.uuid} className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <i className={isUser ? "pi pi-user" : "pi pi-users"} />
                <span className="flex-1 truncate">{name}</span>
                <MultiSelect
                  value={grant.actions}
                  options={ACTION_OPTIONS}
                  // Editing actions must resend the grant's expiry, or the backend's
                  // verbatim write would clear it.
                  onChange={(e) =>
                    upsert.mutate({
                      kind,
                      uuid,
                      actions: e.value,
                      expiresAt: grant.expires_at ?? null,
                    })
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
              {/* Expiry: set/extend/clear (the button bar's Clear removes it), plus a
                  status once one is set. */}
              <div className="flex items-center gap-2 pl-6 text-sm">
                <span style={{ color: "var(--text-muted)" }}>Access expires:</span>
                <Calendar
                  value={grant.expires_at ? new Date(grant.expires_at) : null}
                  onChange={(e) =>
                    upsert.mutate({
                      kind,
                      uuid,
                      actions: grant.actions,
                      expiresAt: e.value ? (e.value as Date).toISOString() : null,
                    })
                  }
                  minDate={new Date()}
                  showButtonBar
                  showTime
                  hourFormat="24"
                  dateFormat="yy-mm-dd"
                  placeholder="Never"
                  className="w-64"
                />
                {expiry &&
                  (expiry.expired ? (
                    <span className="text-red-600 font-medium">{expiry.text}</span>
                  ) : (
                    <span style={{ color: "var(--text-muted)" }}>{expiry.text}</span>
                  ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
