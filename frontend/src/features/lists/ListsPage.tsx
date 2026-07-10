import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Card } from "primereact/card";
import { InputText } from "primereact/inputtext";
import { Button } from "primereact/button";
import { Message } from "primereact/message";
import { Dialog } from "primereact/dialog";
import { ConfirmDialog, confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import type { ListRead } from "../../lib/types";
import { slugify } from "../../lib/slug";

export function ListsPage() {
  const { workspaceUuid = "" } = useParams();
  const queryClient = useQueryClient();
  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["lists", workspaceUuid] });

  const lists = useQuery({
    queryKey: ["lists", workspaceUuid],
    queryFn: () =>
      api.get<ListRead[]>(`/lists/?workspace_uuid=${workspaceUuid}`),
  });

  const [displayName, setDisplayName] = useState("");
  const create = useMutation({
    mutationFn: (name: string) =>
      api.post<ListRead>("/lists/", {
        name: slugify(name),
        display_name: name,
        workspace_uuid: workspaceUuid,
      }),
    onSuccess: () => {
      setDisplayName("");
      invalidate();
    },
  });

  const [renaming, setRenaming] = useState<ListRead | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const rename = useMutation({
    mutationFn: () =>
      api.patch<ListRead>(`/lists/${renaming!.uuid}`, {
        display_name: renameValue,
      }),
    onSuccess: () => {
      setRenaming(null);
      invalidate();
    },
  });

  const remove = useMutation({
    mutationFn: (uuid: string) => api.del(`/lists/${uuid}`),
    onSuccess: invalidate,
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
      <Link to="/" className="no-underline text-sm text-gray-500">
        ← Workspaces
      </Link>
      <h1 className="text-xl font-semibold">Lists</h1>

      <form onSubmit={onCreate} className="flex gap-2">
        <InputText
          placeholder="New list name"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          className="flex-1"
        />
        <Button type="submit" label="Create" icon="pi pi-plus" loading={create.isPending} />
      </form>
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
                <Button
                  icon="pi pi-pencil"
                  text
                  aria-label="Rename list"
                  onClick={() => {
                    setRenaming(list);
                    setRenameValue(list.display_name);
                  }}
                />
                <Button
                  icon="pi pi-trash"
                  text
                  severity="danger"
                  aria-label="Delete list"
                  onClick={() => confirmDelete(list)}
                />
              </div>
            </div>
          </Card>
        ))}
      </div>

      <Dialog
        header="Rename list"
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
