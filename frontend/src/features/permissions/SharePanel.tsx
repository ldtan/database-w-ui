import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Dropdown } from "primereact/dropdown";
import { MultiSelect } from "primereact/multiselect";
import { Button } from "primereact/button";
import { Calendar } from "primereact/calendar";
import { Message } from "primereact/message";

import { api, ApiError } from "../../lib/api";
import { CheckboxField, Field, FormSection } from "../../components/Field";
import { SkeletonRows } from "../../components/Skeleton";
import { useDebounced } from "../../lib/useDebounced";
import { useToast } from "../../lib/toast";
import { actionsFor, presetsFor } from "../../lib/permissions";
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

// Not a module constant: some actions only mean something on some resource types
// (`lookup` on a table or workspace, never on a record, view, or form).
const actionOptions = (resourceType: ResourceType) =>
  actionsFor(resourceType).map((a) => ({ label: a.label, value: a.value }));

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
 *  table's own permissions can live inside Table settings while records and forms
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
  // A group's `display_name` is not unique, and this is the one picker where that
  // costs something: choosing the wrong "Teachers" grants access to the wrong people.
  // So the slug rides along as a second line, and as something the filter matches —
  // otherwise typing the slug an export taught someone would find nothing.
  const principalGroups = [
    {
      label: "Users",
      items: (users.data ?? [])
        .filter((u) => !grantedUsers.has(u.uuid))
        .map((u) => ({
          label: u.username,
          sublabel: null,
          value: `user:${u.uuid}`,
        })),
    },
    {
      label: "Groups",
      items: (groups.data ?? [])
        .filter((g) => !grantedGroups.has(g.uuid))
        .map((g) => ({
          label: g.display_name,
          sublabel: g.name,
          value: `group:${g.uuid}`,
        })),
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
    <div className="flex flex-col gap-5">
      {inherit && (
        <CheckboxField
          inputId="unique-perms"
          checked={!inherit.value}
          disabled={inherit.busy}
          onChange={(next) => inherit.onChange(!next)}
          label="Unique permissions"
          hint="Ignore access inherited from the parent; use only the grants below."
        />
      )}

      {/* Adding someone is one step with several parts, so it sits in a panel of
          its own. Loose on the dialog, its picker, its four presets, its action
          table, its expiry and its Add button read as six unrelated controls
          between the checkbox above and the table below. */}
      <FormSection title="Add people or groups">
        <div className="inset-panel flex flex-col gap-3">
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
            // Both of a group's names, matching what the server searches. Without
            // the second field a slug typed in full would be fetched and then
            // filtered straight back out again.
            filterBy="label,sublabel"
            onFilter={(e) => setSearch(e.filter)}
            resetFilterOnHide
            emptyFilterMessage="No matching users or groups"
            className="w-full"
            itemTemplate={(option) => (
              <div className="flex flex-col">
                <span>{option.label}</span>
                {option.sublabel && (
                  <span className="text-xs text-faint">{option.sublabel}</span>
                )}
              </div>
            )}
          />

          <Field label="Can">
            {/* The presets are the answer almost every time, so they lead and the
                action table is the way to depart from one — quiet, small buttons
                rather than four outlined blocks competing with Add. */}
            <div className="flex flex-wrap items-center gap-1">
              {presetsFor(resourceType).map((preset) => {
                const chosen =
                  preset.actions.length === actions.length &&
                  preset.actions.every((a) => actions.includes(a));
                return (
                  <Button
                    key={preset.label}
                    label={preset.label}
                    size="small"
                    text
                    className={chosen ? "is-on" : undefined}
                    onClick={() => setActions(preset.actions)}
                  />
                );
              })}
            </div>
            <MultiSelect
              value={actions}
              options={actionOptions(resourceType)}
              onChange={(e) => setActions(e.value)}
              placeholder="Actions"
              display="chip"
              className="w-full"
            />
          </Field>

          <Field label="Access expires" optional>
            <Calendar
              value={expiresAt}
              onChange={(e) => setExpiresAt((e.value as Date) ?? null)}
              minDate={new Date()}
              showButtonBar
              showIcon
              showTime
              hourFormat="24"
              dateFormat="yy-mm-dd"
              placeholder="Never"
              className="w-full"
            />
          </Field>

          <Button
            label="Add"
            icon="pi pi-plus"
            className="self-end"
            onClick={onAdd}
            loading={upsert.isPending}
            disabled={!principal || actions.length === 0}
          />
        </div>
      </FormSection>

      {/* Existing access */}
      <FormSection title="People with access">
        {grants.isLoading && <SkeletonRows count={2} />}
        {grants.data?.length === 0 && (
          <Message
            severity="info"
            text="No direct grants yet. The owner always has full access."
          />
        )}
        {grants.data && grants.data.length > 0 && (
          <div className="row-table">
            {grants.data.map((grant) => {
              const isUser = !!grant.user_uuid;
              const name = isUser ? grant.user_username : grant.group_name;
              const kind = isUser ? "user" : "group";
              const uuid = (grant.user_uuid ?? grant.group_uuid)!;
              const expiry = expiryLabel(grant.expires_at);
              return (
                <div key={grant.uuid} className="row-table-item flex-col !items-stretch gap-2">
                  <div className="flex items-center gap-2">
                    <span className="entity-icon is-muted" style={{ width: "1.75rem", height: "1.75rem", fontSize: "0.8rem" }} aria-hidden>
                      <i className={isUser ? "pi pi-user" : "pi pi-users"} />
                    </span>
                    <span className="flex-1 truncate">
                      <span className="font-medium">{name}</span>
                      {/* Same reasoning as the picker: two groups can read alike,
                          and this row is where someone decides one has too much
                          access. */}
                      {grant.group_slug && (
                        <span className="text-xs text-faint">
                          {" "}
                          {grant.group_slug}
                        </span>
                      )}
                    </span>
                    {expiry && (
                      <span
                        className={`badge${expiry.expired ? "" : ""}`}
                        style={
                          expiry.expired
                            ? { color: "var(--danger)" }
                            : undefined
                        }
                      >
                        {expiry.text}
                      </span>
                    )}
                    <Button
                      icon="pi pi-trash"
                      text
                      rounded
                      severity="danger"
                      aria-label={`Remove ${name}`}
                      onClick={() => remove.mutate(grant.uuid)}
                    />
                  </div>
                  {/* What they may do, and until when — the two things a grant
                      says, on one line under the name it belongs to. */}
                  <div className="grant-controls">
                    <MultiSelect
                      value={grant.actions}
                      options={actionOptions(resourceType)}
                      // Editing actions must resend the grant's expiry, or the
                      // backend's verbatim write would clear it.
                      onChange={(e) =>
                        upsert.mutate({
                          kind,
                          uuid,
                          actions: e.value,
                          expiresAt: grant.expires_at ?? null,
                        })
                      }
                      display="chip"
                      placeholder="No actions"
                    />
                    {/* Expiry: set, extend or clear it (the button bar's Clear
                        removes it). */}
                    <Calendar
                      value={grant.expires_at ? new Date(grant.expires_at) : null}
                      onChange={(e) =>
                        upsert.mutate({
                          kind,
                          uuid,
                          actions: grant.actions,
                          expiresAt: e.value
                            ? (e.value as Date).toISOString()
                            : null,
                        })
                      }
                      minDate={new Date()}
                      showButtonBar
                      showTime
                      hourFormat="24"
                      dateFormat="yy-mm-dd"
                      placeholder="Never expires"
                    />
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </FormSection>
    </div>
  );
}
