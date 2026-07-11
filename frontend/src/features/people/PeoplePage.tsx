import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Card } from "primereact/card";
import { InputText } from "primereact/inputtext";
import { Button } from "primereact/button";
import { MultiSelect } from "primereact/multiselect";
import { Message } from "primereact/message";
import { Dialog } from "primereact/dialog";
import { ConfirmDialog, confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import type { ApiUser, ApiUserGroup } from "../../lib/types";
import { useToast } from "../../lib/toast";

export function PeoplePage() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const invalidateGroups = () =>
    queryClient.invalidateQueries({ queryKey: ["groups"] });

  const users = useQuery({
    queryKey: ["users"],
    queryFn: () => api.get<ApiUser[]>("/auth/users/"),
  });
  const groups = useQuery({
    queryKey: ["groups"],
    queryFn: () => api.get<ApiUserGroup[]>("/auth/groups/"),
  });

  const userOptions = (users.data ?? []).map((u) => ({
    label: u.username,
    value: u.uuid,
  }));

  const [groupName, setGroupName] = useState("");
  const createGroup = useMutation({
    mutationFn: (name: string) =>
      api.post<ApiUserGroup>("/auth/groups/", { name }),
    onSuccess: (g) => {
      setGroupName("");
      invalidateGroups();
      toast.success("Group created", g.name);
    },
    onError: (err) =>
      toast.error(
        "Could not create group",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  const setMembers = useMutation({
    mutationFn: (vars: { uuid: string; user_uuids: string[] }) =>
      api.patch(`/auth/groups/${vars.uuid}`, { user_uuids: vars.user_uuids }),
    onSuccess: invalidateGroups,
    onError: (err) =>
      toast.error(
        "Could not update members",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  const removeGroup = useMutation({
    mutationFn: (uuid: string) => api.del(`/auth/groups/${uuid}`),
    onSuccess: () => {
      invalidateGroups();
      toast.success("Group deleted");
    },
    onError: (err) =>
      toast.error(
        "Could not delete group",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  const [renaming, setRenaming] = useState<ApiUserGroup | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const rename = useMutation({
    mutationFn: () =>
      api.patch(`/auth/groups/${renaming!.uuid}`, { name: renameValue }),
    onSuccess: () => {
      setRenaming(null);
      invalidateGroups();
      toast.success("Group renamed");
    },
    onError: (err) =>
      toast.error(
        "Could not rename group",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  function onCreate(event: FormEvent) {
    event.preventDefault();
    if (groupName.trim()) createGroup.mutate(groupName.trim());
  }
  function confirmDelete(group: ApiUserGroup) {
    confirmDialog({
      message: `Delete group "${group.name}"? Its grants are removed too.`,
      header: "Confirm",
      icon: "pi pi-exclamation-triangle",
      accept: () => removeGroup.mutate(group.uuid),
    });
  }

  return (
    <div className="flex flex-col gap-6" style={{ maxWidth: 820 }}>
      <ConfirmDialog />
      <Link to="/" className="no-underline text-sm text-gray-500">
        ← Workspaces
      </Link>
      <h1 className="text-xl font-semibold">People &amp; Groups</h1>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Groups</h2>
        <form onSubmit={onCreate} className="flex gap-2">
          <InputText
            placeholder="New group name"
            value={groupName}
            onChange={(e) => setGroupName(e.target.value)}
            className="flex-1"
          />
          <Button
            type="submit"
            label="Create group"
            icon="pi pi-plus"
            loading={createGroup.isPending}
          />
        </form>

        {groups.isLoading && <p>Loading…</p>}
        {groups.data?.length === 0 && <p>No groups yet.</p>}

        <div className="flex flex-col gap-2">
          {groups.data?.map((group) => (
            <Card key={group.uuid}>
              <div className="flex items-center justify-between gap-2">
                <div className="font-medium">{group.name}</div>
                <div className="flex gap-1">
                  <Button
                    icon="pi pi-pencil"
                    text
                    aria-label="Rename group"
                    onClick={() => {
                      setRenaming(group);
                      setRenameValue(group.name);
                    }}
                  />
                  <Button
                    icon="pi pi-trash"
                    text
                    severity="danger"
                    aria-label="Delete group"
                    onClick={() => confirmDelete(group)}
                  />
                </div>
              </div>
              <div className="mt-2 flex items-center gap-2">
                <span className="text-sm" style={{ color: "var(--text-muted)" }}>
                  Members
                </span>
                <MultiSelect
                  value={group.users.map((u) => u.uuid)}
                  options={userOptions}
                  onChange={(e) =>
                    setMembers.mutate({ uuid: group.uuid, user_uuids: e.value })
                  }
                  placeholder="Add members…"
                  display="chip"
                  filter
                  className="flex-1"
                />
              </div>
            </Card>
          ))}
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Users</h2>
        {users.data?.length === 0 && (
          <Message severity="info" text="No users yet." />
        )}
        <div className="flex flex-col gap-2">
          {users.data?.map((user) => (
            <Card key={user.uuid}>
              <div className="flex items-center justify-between">
                <div>
                  <div className="font-medium">{user.username}</div>
                  <div className="text-sm" style={{ color: "var(--text-muted)" }}>
                    {user.email}
                  </div>
                </div>
                {!user.is_active && (
                  <span className="text-sm" style={{ color: "var(--text-muted)" }}>
                    inactive
                  </span>
                )}
              </div>
            </Card>
          ))}
        </div>
      </section>

      <Dialog
        header="Rename group"
        visible={!!renaming}
        onHide={() => setRenaming(null)}
        style={{ width: 380 }}
      >
        <div className="flex flex-col gap-4">
          {rename.error instanceof ApiError && (
            <Message severity="error" text={rename.error.message} />
          )}
          <InputText
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            autoFocus
          />
          <div className="flex justify-end gap-2">
            <Button label="Cancel" text onClick={() => setRenaming(null)} />
            <Button
              label="Save"
              onClick={() => rename.mutate()}
              loading={rename.isPending}
              disabled={!renameValue.trim()}
            />
          </div>
        </div>
      </Dialog>
    </div>
  );
}
