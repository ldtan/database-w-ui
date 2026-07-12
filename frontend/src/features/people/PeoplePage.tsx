import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Card } from "primereact/card";
import { InputText } from "primereact/inputtext";
import { IconField } from "primereact/iconfield";
import { InputIcon } from "primereact/inputicon";
import { Button } from "primereact/button";
import { Message } from "primereact/message";
import { ConfirmDialog, confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { useCurrentUser } from "../../lib/auth";
import { useDebounced } from "../../lib/useDebounced";
import { useToast } from "../../lib/toast";
import type { ApiUser, ApiUserGroup, Person } from "../../lib/types";
import { Breadcrumbs } from "../../components/Breadcrumbs";
import { GroupDialog } from "./GroupDialog";
import { UserDialog } from "./UserDialog";

// One page of results. Searching happens server-side, so a term always finds a
// match no matter how far down the list it sits — filtering a single fetched page
// client-side would report "no match" for anything past this limit, which is worse
// than no search at all.
const PAGE = 100;

export function PeoplePage() {
  const queryClient = useQueryClient();
  const toast = useToast();

  // Groups are principals in the grant table, so whoever can edit membership can
  // hand themselves any grant a group holds — hence superuser-only, matching the
  // backend. Reads stay open so everyone can see who is in what.
  const me = useCurrentUser();
  const canAdminister = me?.is_superuser ?? false;

  const [userSearch, setUserSearch] = useState("");
  const [groupSearch, setGroupSearch] = useState("");
  const userQ = useDebounced(userSearch);
  const groupQ = useDebounced(groupSearch);

  const users = useQuery({
    queryKey: ["users", "search", userQ],
    queryFn: () =>
      api.get<ApiUser[]>(
        `/auth/users/?limit=${PAGE}&q=${encodeURIComponent(userQ.trim())}`,
      ),
  });
  const groups = useQuery({
    queryKey: ["groups", "search", groupQ],
    queryFn: () =>
      api.get<ApiUserGroup[]>(
        `/auth/groups/?limit=${PAGE}&q=${encodeURIComponent(groupQ.trim())}`,
      ),
  });
  // Person records are personal data: only a superuser may list them. Used to
  // annotate each user with their connected person and to seed the edit dialog.
  const people = useQuery({
    queryKey: ["people", "map"],
    queryFn: () => api.get<Person[]>("/people/?limit=1000"),
    enabled: canAdminister,
  });

  const personByUser = new Map<string, Person>();
  for (const person of people.data ?? []) {
    personByUser.set(person.user.uuid, person);
  }

  const [userDialog, setUserDialog] = useState<{
    open: boolean;
    user: ApiUser | null;
  }>({ open: false, user: null });
  const [groupDialog, setGroupDialog] = useState<{
    open: boolean;
    group: ApiUserGroup | null;
  }>({ open: false, group: null });

  const removeGroup = useMutation({
    mutationFn: (uuid: string) => api.del(`/auth/groups/${uuid}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["groups"] });
      toast.success("Group deleted");
    },
    onError: (err) =>
      toast.error(
        "Could not delete group",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  function confirmDeleteGroup(group: ApiUserGroup) {
    confirmDialog({
      message: `Delete group "${group.name}"? Its grants are removed too.`,
      header: "Confirm",
      icon: "pi pi-exclamation-triangle",
      accept: () => removeGroup.mutate(group.uuid),
    });
  }

  const shownUsers = users.data ?? [];
  const shownGroups = groups.data ?? [];
  // A full page back means there are probably more; say so instead of pretending
  // this is everything.
  const usersCapped = shownUsers.length === PAGE;
  const groupsCapped = shownGroups.length === PAGE;

  const usernameByUuid = new Map(
    shownUsers.map((user) => [user.uuid, user.username]),
  );

  function fullName(person: Person): string {
    return [person.first_name, person.middle_name, person.last_name]
      .filter(Boolean)
      .join(" ");
  }

  return (
    <div className="flex flex-col gap-6" style={{ maxWidth: 820 }}>
      <ConfirmDialog />
      <Breadcrumbs items={[{ label: "People & Groups" }]} />
      <h1 className="text-xl font-semibold">People &amp; Groups</h1>

      {!canAdminister && (
        <Message
          severity="info"
          text="Read-only. Creating users and groups, and changing membership, requires a superuser."
        />
      )}

      {/* ---------------------------------------------------------- Users */}
      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-lg font-medium">Users</h2>
          {canAdminister && (
            <Button
              label="Add user"
              icon="pi pi-user-plus"
              onClick={() => setUserDialog({ open: true, user: null })}
            />
          )}
        </div>

        <IconField iconPosition="left">
          <InputIcon className="pi pi-search" />
          <InputText
            value={userSearch}
            onChange={(e) => setUserSearch(e.target.value)}
            placeholder={
              canAdminister
                ? "Search users by username, email, or person name…"
                : "Search users by username or email…"
            }
            className="w-full"
          />
        </IconField>

        {users.isLoading && <p>Loading…</p>}
        {!users.isLoading && shownUsers.length === 0 && (
          <Message
            severity="info"
            text={userSearch ? "No users match that search." : "No users yet."}
          />
        )}
        {usersCapped && (
          <Message
            severity="info"
            text={`Showing the first ${PAGE} users. Search to narrow them down.`}
          />
        )}

        <div className="flex flex-col gap-2">
          {shownUsers.map((user) => {
            const person = personByUser.get(user.uuid);
            return (
              <Card key={user.uuid}>
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="font-medium">{user.username}</div>
                    <div
                      className="text-sm truncate"
                      style={{ color: "var(--text-muted)" }}
                    >
                      {user.email}
                      {person && ` · ${fullName(person)}`}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {user.is_superuser && (
                      <span
                        className="text-sm font-medium"
                        style={{ color: "var(--brand)" }}
                      >
                        superuser
                      </span>
                    )}
                    {!user.is_active && (
                      <span
                        className="text-sm"
                        style={{ color: "var(--text-muted)" }}
                      >
                        inactive
                      </span>
                    )}
                    {canAdminister && (
                      <Button
                        icon="pi pi-pencil"
                        text
                        aria-label={`Edit ${user.username}`}
                        onClick={() => setUserDialog({ open: true, user })}
                      />
                    )}
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      </section>

      {/* --------------------------------------------------------- Groups */}
      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-lg font-medium">Groups</h2>
          {canAdminister && (
            <Button
              label="New group"
              icon="pi pi-plus"
              onClick={() => setGroupDialog({ open: true, group: null })}
            />
          )}
        </div>

        <IconField iconPosition="left">
          <InputIcon className="pi pi-search" />
          <InputText
            value={groupSearch}
            onChange={(e) => setGroupSearch(e.target.value)}
            placeholder="Search groups…"
            className="w-full"
          />
        </IconField>

        {groups.isLoading && <p>Loading…</p>}
        {!groups.isLoading && shownGroups.length === 0 && (
          <Message
            severity="info"
            text={groupSearch ? "No groups match that search." : "No groups yet."}
          />
        )}
        {groupsCapped && (
          <Message
            severity="info"
            text={`Showing the first ${PAGE} groups. Search to narrow them down.`}
          />
        )}

        <div className="flex flex-col gap-2">
          {shownGroups.map((group) => (
            <Card key={group.uuid}>
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="font-medium">{group.name}</div>
                  {group.description && (
                    <div
                      className="text-sm truncate"
                      style={{ color: "var(--text-muted)" }}
                    >
                      {group.description}
                    </div>
                  )}
                </div>
                {canAdminister && (
                  <div className="flex gap-1">
                    <Button
                      icon="pi pi-pencil"
                      text
                      aria-label={`Edit ${group.name}`}
                      onClick={() => setGroupDialog({ open: true, group })}
                    />
                    <Button
                      icon="pi pi-trash"
                      text
                      severity="danger"
                      aria-label={`Delete ${group.name}`}
                      onClick={() => confirmDeleteGroup(group)}
                    />
                  </div>
                )}
              </div>
              <div className="mt-2 text-sm">
                <span style={{ color: "var(--text-muted)" }}>Members: </span>
                {group.users.length === 0
                  ? "none"
                  : group.users
                      .map((u) => usernameByUuid.get(u.uuid) ?? u.uuid)
                      .join(", ")}
              </div>
            </Card>
          ))}
        </div>
      </section>

      <UserDialog
        visible={userDialog.open}
        onHide={() => setUserDialog({ open: false, user: null })}
        user={userDialog.user}
        person={
          userDialog.user ? personByUser.get(userDialog.user.uuid) ?? null : null
        }
        isSelf={!!me && userDialog.user?.uuid === me.uuid}
      />
      <GroupDialog
        visible={groupDialog.open}
        onHide={() => setGroupDialog({ open: false, group: null })}
        group={groupDialog.group}
      />
    </div>
  );
}
