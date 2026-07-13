import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Card } from "primereact/card";
import { InputText } from "primereact/inputtext";
import { Button } from "primereact/button";
import { Message } from "primereact/message";
import { AppDialog } from "../../components/AppDialog";
import { ConfirmDialog, confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { Breadcrumbs } from "../../components/Breadcrumbs";
import { NameFields } from "../../components/NameFields";
import type { ListRead, Workspace } from "../../lib/types";
import { isValidName, slugify } from "../../lib/slug";
import { useToast } from "../../lib/toast";
import { ShareDialog } from "../permissions/ShareDialog";
import {
  useEffectiveActions,
  useEffectiveActionsMap,
} from "../permissions/useEffectiveActions";

export function ListsPage() {
  const { workspaceUuid = "" } = useParams();
  const queryClient = useQueryClient();
  const toast = useToast();
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["lists", workspaceUuid] });
    queryClient.invalidateQueries({ queryKey: ["effective"] });
  };

  const lists = useQuery({
    queryKey: ["lists", workspaceUuid],
    queryFn: () =>
      api.get<ListRead[]>(`/lists/?workspace_uuid=${workspaceUuid}`),
  });
  const workspace = useQuery({
    queryKey: ["workspace", workspaceUuid],
    queryFn: () => api.get<Workspace>(`/workspaces/${workspaceUuid}`),
  });
  // Adding a list is a workspace-level change; the rest is gated per list.
  const workspaceActions = useEffectiveActions("workspace", workspaceUuid);
  const can = useEffectiveActionsMap(
    "list",
    (lists.data ?? []).map((list) => list.uuid),
  );

  const [displayName, setDisplayName] = useState("");
  const create = useMutation({
    mutationFn: (name: string) =>
      api.post<ListRead>("/lists/", {
        name: slugify(name),
        display_name: name,
        workspace_uuid: workspaceUuid,
      }),
    onSuccess: (list) => {
      setDisplayName("");
      invalidate();
      toast.success("List created", list.display_name);
    },
  });

  const [renaming, setRenaming] = useState<ListRead | null>(null);
  const [names, setNames] = useState({ displayName: "", name: "" });
  const rename = useMutation({
    mutationFn: () =>
      api.patch<ListRead>(`/lists/${renaming!.uuid}`, {
        name: names.name,
        display_name: names.displayName,
      }),
    onSuccess: () => {
      setRenaming(null);
      invalidate();
      toast.success("List renamed");
    },
  });

  const remove = useMutation({
    mutationFn: (uuid: string) => api.del(`/lists/${uuid}`),
    onSuccess: () => {
      invalidate();
      toast.success("List deleted");
    },
    onError: (err) =>
      toast.error(
        "Could not delete list",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  // Share dialog: track by uuid so the inherit toggle reflects fresh data.
  const [sharingUuid, setSharingUuid] = useState<string | null>(null);
  const sharing = lists.data?.find((l) => l.uuid === sharingUuid) ?? null;
  const setInherit = useMutation({
    mutationFn: (vars: { uuid: string; value: boolean }) =>
      api.patch(`/lists/${vars.uuid}`, { permissions_inherit: vars.value }),
    onSuccess: invalidate,
    onError: (err) =>
      toast.error(
        "Could not update inheritance",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  function onCreate(event: FormEvent) {
    event.preventDefault();
    if (displayName.trim()) create.mutate(displayName.trim());
  }
  function confirmDelete(list: ListRead) {
    confirmDialog({
      message: `Delete list "${list.display_name}" and its records?`,
      header: "Confirm",
      icon: "pi pi-exclamation-triangle",
      accept: () => remove.mutate(list.uuid),
    });
  }

  return (
    <div className="flex flex-col gap-4" style={{ maxWidth: 720 }}>
      <ConfirmDialog />
      <Breadcrumbs items={[{ label: workspace.data?.display_name ?? "Lists" }]} />
      <h1 className="text-xl font-semibold">
        {workspace.data?.display_name ?? "Lists"}
      </h1>

      {workspaceActions.has("configure") && (
        <form onSubmit={onCreate} className="flex gap-2">
          <InputText
            placeholder="New list name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            className="flex-1"
          />
          <Button type="submit" label="Create" icon="pi pi-plus" loading={create.isPending} />
        </form>
      )}
      {create.error instanceof ApiError && (
        <Message severity="error" text={create.error.message} />
      )}

      {lists.isLoading && <p>Loading…</p>}
      {lists.data?.length === 0 && <p>No lists in this workspace yet.</p>}

      <div className="flex flex-col gap-2">
        {lists.data?.map((list) => (
          <Card key={list.uuid}>
            <div className="flex items-center justify-between">
              <Link to={`/lists/${list.uuid}`} className="no-underline">
                <div className="font-medium">{list.display_name}</div>
                <div className="text-sm text-gray-500">{list.name}</div>
              </Link>
              <div className="flex gap-1">
                {can(list.uuid, "share") && (
                  <Button
                    icon="pi pi-share-alt"
                    text
                    aria-label="Share list"
                    onClick={() => setSharingUuid(list.uuid)}
                  />
                )}
                {can(list.uuid, "configure") && (
                  <Button
                    icon="pi pi-pencil"
                    text
                    aria-label="Rename list"
                    onClick={() => {
                      setRenaming(list);
                      setNames({
                        displayName: list.display_name,
                        name: list.name,
                      });
                    }}
                  />
                )}
                {can(list.uuid, "delete") && (
                  <Button
                    icon="pi pi-trash"
                    text
                    severity="danger"
                    aria-label="Delete list"
                    onClick={() => confirmDelete(list)}
                  />
                )}
              </div>
            </div>
          </Card>
        ))}
      </div>

      {sharing && (
        <ShareDialog
          visible={!!sharing}
          onHide={() => setSharingUuid(null)}
          resourceType="list"
          resourceUuid={sharing.uuid}
          resourceLabel={sharing.display_name}
          inherit={{
            value: sharing.permissions_inherit,
            onChange: (value) =>
              setInherit.mutate({ uuid: sharing.uuid, value }),
            busy: setInherit.isPending,
          }}
        />
      )}

      <AppDialog
        header="Rename list"
        visible={!!renaming}
        onHide={() => setRenaming(null)}
        style={{ width: 380 }}
      >
        <div className="flex flex-col gap-4">
          {rename.error instanceof ApiError && (
            <Message severity="error" text={rename.error.message} />
          )}
          <NameFields
            key={renaming?.uuid ?? "none"}
            idPrefix="list"
            value={names}
            onChange={setNames}
            linked={false}
            autoFocus
          />
          <div className="flex justify-end gap-2">
            <Button label="Cancel" text onClick={() => setRenaming(null)} />
            <Button
              label="Save"
              onClick={() => rename.mutate()}
              loading={rename.isPending}
              disabled={!names.displayName.trim() || !isValidName(names.name)}
            />
          </div>
        </div>
      </AppDialog>
    </div>
  );
}
