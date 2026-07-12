import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Card } from "primereact/card";
import { InputText } from "primereact/inputtext";
import { Button } from "primereact/button";
import { Message } from "primereact/message";
import { Dialog } from "primereact/dialog";
import { ConfirmDialog, confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import type { Workspace } from "../../lib/types";
import { slugify } from "../../lib/slug";
import { useToast } from "../../lib/toast";
import { ShareDialog } from "../permissions/ShareDialog";
import { useEffectiveActionsMap } from "../permissions/useEffectiveActions";

export function WorkspacesPage() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["workspaces"] });

  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: () => api.get<Workspace[]>("/workspaces/"),
  });
  // A shared workspace may be view-only, so gate its controls per row.
  const can = useEffectiveActionsMap(
    "workspace",
    (workspaces.data ?? []).map((ws) => ws.uuid),
  );

  const [displayName, setDisplayName] = useState("");
  const create = useMutation({
    mutationFn: (name: string) =>
      api.post<Workspace>("/workspaces/", {
        name: slugify(name),
        display_name: name,
      }),
    onSuccess: (ws) => {
      setDisplayName("");
      invalidate();
      toast.success("Workspace created", ws.display_name);
    },
  });

  const [sharing, setSharing] = useState<Workspace | null>(null);
  const [renaming, setRenaming] = useState<Workspace | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const rename = useMutation({
    mutationFn: () =>
      api.patch<Workspace>(`/workspaces/${renaming!.uuid}`, {
        display_name: renameValue,
      }),
    onSuccess: () => {
      setRenaming(null);
      invalidate();
      toast.success("Workspace renamed");
    },
  });

  const remove = useMutation({
    mutationFn: (uuid: string) => api.del(`/workspaces/${uuid}`),
    onSuccess: () => {
      invalidate();
      toast.success("Workspace deleted");
    },
    onError: (err) =>
      toast.error(
        "Could not delete workspace",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  function onCreate(event: FormEvent) {
    event.preventDefault();
    if (displayName.trim()) create.mutate(displayName.trim());
  }
  function confirmDelete(ws: Workspace) {
    confirmDialog({
      message: `Delete workspace "${ws.display_name}" and all its lists?`,
      header: "Confirm",
      icon: "pi pi-exclamation-triangle",
      accept: () => remove.mutate(ws.uuid),
    });
  }

  return (
    <div className="flex flex-col gap-4" style={{ maxWidth: 720 }}>
      <ConfirmDialog />
      <h1 className="text-xl font-semibold">Workspaces</h1>

      <form onSubmit={onCreate} className="flex gap-2">
        <InputText
          placeholder="New workspace name"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          className="flex-1"
        />
        <Button type="submit" label="Create" icon="pi pi-plus" loading={create.isPending} />
      </form>
      {create.error instanceof ApiError && (
        <Message severity="error" text={create.error.message} />
      )}

      {workspaces.isLoading && <p>Loading…</p>}
      {workspaces.data?.length === 0 && <p>No workspaces yet.</p>}

      <div className="flex flex-col gap-2">
        {workspaces.data?.map((ws) => (
          <Card key={ws.uuid}>
            <div className="flex items-center justify-between">
              <Link to={`/workspaces/${ws.uuid}`} className="no-underline">
                <div className="font-medium">{ws.display_name}</div>
                <div className="text-sm text-gray-500">{ws.name}</div>
              </Link>
              <div className="flex gap-1">
                {can(ws.uuid, "share") && (
                  <Button
                    icon="pi pi-share-alt"
                    text
                    aria-label="Share workspace"
                    onClick={() => setSharing(ws)}
                  />
                )}
                {can(ws.uuid, "configure") && (
                  <Button
                    icon="pi pi-pencil"
                    text
                    aria-label="Rename workspace"
                    onClick={() => {
                      setRenaming(ws);
                      setRenameValue(ws.display_name);
                    }}
                  />
                )}
                {can(ws.uuid, "delete") && (
                  <Button
                    icon="pi pi-trash"
                    text
                    severity="danger"
                    aria-label="Delete workspace"
                    onClick={() => confirmDelete(ws)}
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
          onHide={() => setSharing(null)}
          resourceType="workspace"
          resourceUuid={sharing.uuid}
          resourceLabel={sharing.display_name}
        />
      )}

      <Dialog
        header="Rename workspace"
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
