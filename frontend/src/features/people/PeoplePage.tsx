import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "primereact/button";
import { Message } from "primereact/message";
import { Paginator } from "primereact/paginator";
import { ConfirmDialog, confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { useCurrentUser } from "../../lib/auth";
import { fullName } from "../../lib/people";
import { useDebounced } from "../../lib/useDebounced";
import { useToast } from "../../lib/toast";
import type { ApiUser, ApiUserGroup, Person } from "../../lib/types";
import { EmptyState } from "../../components/EmptyState";
import { PageHeader } from "../../components/PageHeader";
import { RowActions } from "../../components/RowActions";
import { SearchInput } from "../../components/SearchInput";
import { SkeletonRows } from "../../components/Skeleton";
import { UserAvatar } from "../../components/UserAvatar";
import { BatchUserDialog } from "./BatchUserDialog";
import { DeletedPrincipalsDialog } from "./DeletedPrincipalsDialog";
import { GroupDialog } from "./GroupDialog";
import { UserDialog } from "./UserDialog";

// Searching and paging both happen server-side, so a term finds a match no matter
// how far down the list it sits. Filtering one fetched page in the browser would
// report "no match" for anything past the limit, which is worse than no search.
const ROWS = 10;

/**
 * Accounts and groups — identity administration, not access.
 *
 * Nothing here grants anyone access to a workspace, list or record; that is the
 * grant system's business (see the Share dialog). What it administers sits
 * *underneath* permissions, which is why writing is superuser-only: groups are
 * principals in the grant table, so whoever edits membership can hand themselves
 * any grant a group holds, and whoever resets a password can simply become the
 * workspace owner. Reading stays open, so everyone can see who is in what.
 *
 * Both lists are searched and paged **server-side** — a client-side filter over one
 * fetched page silently fails to find anyone past the page limit, which in the
 * Share dialog would mean being unable to grant to them at all.
 */
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

  const [userFirst, setUserFirst] = useState(0);
  const [groupFirst, setGroupFirst] = useState(0);
  const [userRows, setUserRows] = useState(ROWS);
  const [groupRows, setGroupRows] = useState(ROWS);

  // A new search re-ranks everything, so page 3 of the old results is meaningless.
  // Without this you can land on an empty page and think there are no matches.
  useEffect(() => setUserFirst(0), [userQ]);
  useEffect(() => setGroupFirst(0), [groupQ]);

  const users = useQuery({
    queryKey: ["users", "search", userQ, userFirst, userRows],
    queryFn: () =>
      api.getPage<ApiUser>(
        `/auth/users/?skip=${userFirst}&limit=${userRows}` +
          `&q=${encodeURIComponent(userQ.trim())}`,
      ),
    placeholderData: (prev) => prev, // don't blank the list while paging
  });
  const groups = useQuery({
    queryKey: ["groups", "search", groupQ, groupFirst, groupRows],
    queryFn: () =>
      api.getPage<ApiUserGroup>(
        `/auth/groups/?skip=${groupFirst}&limit=${groupRows}` +
          `&q=${encodeURIComponent(groupQ.trim())}`,
      ),
    placeholderData: (prev) => prev,
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
  // One bin per principal, beside the section that holds it, and one flag each so
  // the two keep their own paging. Superuser-only like the rest of the writing
  // here — restoring a group hands back every grant it held, which is the same
  // authority as editing its membership.
  const [showDeletedUsers, setShowDeletedUsers] = useState(false);
  const [showDeletedGroups, setShowDeletedGroups] = useState(false);
  // Creating accounts from a file, rather than one at a time in UserDialog.
  const [showBatchUsers, setShowBatchUsers] = useState(false);

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

  const shownUsers = users.data?.items ?? [];
  const shownGroups = groups.data?.items ?? [];
  const userTotal = users.data?.total ?? 0;
  const groupTotal = groups.data?.total ?? 0;

  const usernameByUuid = new Map(
    shownUsers.map((user) => [user.uuid, user.username]),
  );

  return (
    <div className="flex flex-col gap-6" style={{ maxWidth: 900 }}>
      <ConfirmDialog />
      <PageHeader
        crumbs={[{ label: "People & Groups" }]}
        title="People & Groups"
        subtitle="Who exists, and which groups they are in. Access itself is granted per workspace, list or record."
      />

      {!canAdminister && (
        <Message
          severity="info"
          text="Read-only. Creating users and groups, and changing membership, requires a superuser."
        />
      )}

      {/* ---------------------------------------------------------- Users */}
      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold">Users</h2>
            {userTotal > 0 && <span className="badge">{userTotal}</span>}
          </div>
          {canAdminister && (
            <div className="flex items-center gap-2">
              <Button
                label="Deleted"
                icon="pi pi-trash"
                text
                onClick={() => setShowDeletedUsers(true)}
              />
              <Button
                label="Import users"
                icon="pi pi-upload"
                text
                onClick={() => setShowBatchUsers(true)}
              />
              <Button
                label="Add user"
                icon="pi pi-user-plus"
                onClick={() => setUserDialog({ open: true, user: null })}
              />
            </div>
          )}
        </div>

        <SearchInput
          value={userSearch}
          onChange={setUserSearch}
          placeholder={
            canAdminister
              ? "Search users by username, email, or person name…"
              : "Search users by username or email…"
          }
        />

        {users.isLoading ? (
          <SkeletonRows count={3} />
        ) : shownUsers.length === 0 ? (
          <EmptyState
            icon={userSearch ? "pi pi-search" : "pi pi-users"}
            title={userSearch ? "No matches" : "No users yet"}
            text={
              userSearch
                ? `No account matches “${userSearch}”.`
                : undefined
            }
            action={
              userSearch ? (
                <Button
                  label="Clear search"
                  icon="pi pi-times"
                  text
                  onClick={() => setUserSearch("")}
                />
              ) : undefined
            }
          />
        ) : (
          <div className="flex flex-col gap-2">
            {shownUsers.map((user) => {
              const person = personByUser.get(user.uuid);
              return (
                <div
                  key={user.uuid}
                  className="surface-card p-3 flex items-center justify-between gap-3"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <UserAvatar
                      person={person ?? null}
                      username={user.username}
                      size="2.25rem"
                    />
                    <div className="min-w-0">
                      <div className="font-medium truncate">
                        {person ? fullName(person) : user.username}
                      </div>
                      <div className="text-sm truncate text-muted">
                        {person ? `${user.username} · ` : ""}
                        {user.email}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {user.is_superuser && (
                      <span className="badge is-brand">superuser</span>
                    )}
                    {!user.is_active && <span className="badge">inactive</span>}
                    {canAdminister && (
                      <Button
                        icon="pi pi-pencil"
                        text
                        rounded
                        aria-label={`Edit ${user.username}`}
                        onClick={() => setUserDialog({ open: true, user })}
                      />
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {userTotal > userRows && (
          <Paginator
            first={userFirst}
            rows={userRows}
            totalRecords={userTotal}
            rowsPerPageOptions={[10, 25, 50, 100]}
            onPageChange={(e) => {
              setUserFirst(e.first);
              setUserRows(e.rows);
            }}
            template="CurrentPageReport FirstPageLink PrevPageLink PageLinks NextPageLink LastPageLink RowsPerPageDropdown"
            currentPageReportTemplate="{first}–{last} of {totalRecords}"
          />
        )}
      </section>

      {/* --------------------------------------------------------- Groups */}
      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold">Groups</h2>
            {groupTotal > 0 && <span className="badge">{groupTotal}</span>}
          </div>
          {canAdminister && (
            <div className="flex items-center gap-2">
              <Button
                label="Deleted"
                icon="pi pi-trash"
                text
                onClick={() => setShowDeletedGroups(true)}
              />
              <Button
                label="New group"
                icon="pi pi-plus"
                onClick={() => setGroupDialog({ open: true, group: null })}
              />
            </div>
          )}
        </div>

        <SearchInput
          value={groupSearch}
          onChange={setGroupSearch}
          placeholder="Search groups…"
        />

        {groups.isLoading ? (
          <SkeletonRows count={2} />
        ) : shownGroups.length === 0 ? (
          <EmptyState
            icon={groupSearch ? "pi pi-search" : "pi pi-sitemap"}
            title={groupSearch ? "No matches" : "No groups yet"}
            text={
              groupSearch
                ? `No group matches “${groupSearch}”.`
                : "A group is a principal you can grant to once instead of naming everyone in it."
            }
            action={
              groupSearch ? (
                <Button
                  label="Clear search"
                  icon="pi pi-times"
                  text
                  onClick={() => setGroupSearch("")}
                />
              ) : canAdminister ? (
                <Button
                  label="New group"
                  icon="pi pi-plus"
                  onClick={() => setGroupDialog({ open: true, group: null })}
                />
              ) : undefined
            }
          />
        ) : (
          <div className="flex flex-col gap-2">
            {shownGroups.map((group) => (
              <div key={group.uuid} className="surface-card p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-start gap-3 min-w-0">
                    <span className="entity-icon" aria-hidden>
                      <i className="pi pi-sitemap" />
                    </span>
                    <div className="min-w-0">
                      <div className="font-medium truncate">{group.name}</div>
                      {group.description && (
                        <div className="text-sm truncate text-muted">
                          {group.description}
                        </div>
                      )}
                    </div>
                  </div>
                  <RowActions
                    actions={[
                      {
                        key: "edit",
                        icon: "pi pi-pencil",
                        label: `Edit ${group.name}`,
                        visible: canAdminister,
                        onClick: () => setGroupDialog({ open: true, group }),
                      },
                      {
                        key: "delete",
                        icon: "pi pi-trash",
                        label: `Delete ${group.name}`,
                        severity: "danger",
                        visible: canAdminister,
                        onClick: () => confirmDeleteGroup(group),
                      },
                    ]}
                  />
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <span className="text-sm text-muted">Members:</span>
                  {group.users.length === 0 ? (
                    <span className="text-sm text-muted">none</span>
                  ) : (
                    group.users.map((u) => (
                      <span key={u.uuid} className="badge">
                        {usernameByUuid.get(u.uuid) ?? u.uuid}
                      </span>
                    ))
                  )}
                </div>
              </div>
            ))}
          </div>
        )}

        {groupTotal > groupRows && (
          <Paginator
            first={groupFirst}
            rows={groupRows}
            totalRecords={groupTotal}
            rowsPerPageOptions={[10, 25, 50, 100]}
            onPageChange={(e) => {
              setGroupFirst(e.first);
              setGroupRows(e.rows);
            }}
            template="CurrentPageReport FirstPageLink PrevPageLink PageLinks NextPageLink LastPageLink RowsPerPageDropdown"
            currentPageReportTemplate="{first}–{last} of {totalRecords}"
          />
        )}
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
      <BatchUserDialog
        visible={showBatchUsers}
        onHide={() => setShowBatchUsers(false)}
      />
      <DeletedPrincipalsDialog
        kind="users"
        visible={showDeletedUsers}
        onHide={() => setShowDeletedUsers(false)}
      />
      <DeletedPrincipalsDialog
        kind="groups"
        visible={showDeletedGroups}
        onHide={() => setShowDeletedGroups(false)}
      />
    </div>
  );
}
