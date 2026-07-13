import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "primereact/button";
import { Dialog } from "primereact/dialog";
import { InputText } from "primereact/inputtext";
import { Message } from "primereact/message";
import { confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { slugify } from "../../lib/slug";
import { useToast } from "../../lib/toast";
import type { View, ViewConfig } from "../../lib/types";
import { ShareDialog } from "../permissions/ShareDialog";
import { useEffectiveActionsMap } from "../permissions/useEffectiveActions";

interface Props {
  listUuid: string;
  views: View[];
  activeUuid: string | null;
  onSelect: (uuid: string | null) => void;
  // The sort/filter/columns showing right now — what a new view is created from.
  currentConfig: ViewConfig;
  canConfigure: boolean;
}

/** Managing the views themselves: create, rename, share, delete.
 *
 *  What a view *contains* — sort, filter, grouping, which columns show — stays on
 *  the toolbar, because that is the thing you are looking at while you change it.
 *  This tab is for the views as objects, the same way the Columns tab is for the
 *  fields as objects.
 */
export function ViewsPanel({
  listUuid,
  views,
  activeUuid,
  onSelect,
  currentConfig,
  canConfigure,
}: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [renaming, setRenaming] = useState<View | null>(null);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [sharing, setSharing] = useState<View | null>(null);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["views", listUuid] });
    queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
    queryClient.invalidateQueries({ queryKey: ["effective"] });
  };

  // A view can carry its own grants, so who may rename, delete or re-share one is
  // decided per view — not by the list.
  const canView = useEffectiveActionsMap(
    "view",
    views.map((v) => v.uuid),
  );

  const create = useMutation({
    mutationFn: () =>
      api.post<View>(`/lists/${listUuid}/views/`, {
        name: slugify(name),
        display_name: name,
        config: currentConfig,
      }),
    onSuccess: (view) => {
      setCreating(false);
      setName("");
      invalidate();
      onSelect(view.uuid);
      toast.success("View created", view.display_name);
    },
  });

  const rename = useMutation({
    mutationFn: () =>
      // `name` (the slug) is left alone, as with columns: it identifies the view to
      // code, and only the label is being changed.
      api.patch<View>(`/lists/${listUuid}/views/${renaming!.uuid}`, {
        display_name: name,
      }),
    onSuccess: (view) => {
      setRenaming(null);
      invalidate();
      toast.success("View renamed", view.display_name);
    },
  });

  const remove = useMutation({
    mutationFn: (uuid: string) => api.del(`/lists/${listUuid}/views/${uuid}`),
    onSuccess: (_data, uuid) => {
      // The page is showing this view; drop the selection so it falls back to
      // whichever view remains rather than filtering by one that is gone.
      if (uuid === activeUuid) onSelect(null);
      invalidate();
      toast.success("View deleted");
    },
    onError: (err) =>
      toast.error(
        "Could not delete view",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  const setInherit = useMutation({
    mutationFn: (vars: { uuid: string; value: boolean }) =>
      api.patch(`/lists/${listUuid}/views/${vars.uuid}`, {
        permissions_inherit: vars.value,
      }),
    onSuccess: invalidate,
    onError: (err) =>
      toast.error(
        "Could not update inheritance",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  function confirmDelete(view: View) {
    confirmDialog({
      message: `Delete the view “${view.display_name}”? The records stay; only this saved sort/filter goes.`,
      header: "Confirm",
      icon: "pi pi-exclamation-triangle",
      accept: () => remove.mutate(view.uuid),
    });
  }

  return (
    <div className="flex flex-col gap-3">
      {views.length === 0 && (
        <Message severity="info" text="This list has no views." />
      )}

      {views.map((view) => (
        <div
          key={view.uuid}
          className="flex items-center gap-3 border-b pb-2"
          style={{ borderColor: "var(--border)" }}
        >
          <div className="flex-1 min-w-0">
            <div className="truncate">{view.display_name}</div>
            <small style={{ color: "var(--text-muted)" }}>
              {view.uuid === activeUuid ? "Showing now" : view.name}
            </small>
          </div>
          {canView(view.uuid, "configure") && (
            <Button
              icon="pi pi-pencil"
              text
              size="small"
              aria-label={`Rename view ${view.display_name}`}
              onClick={() => {
                setName(view.display_name);
                setRenaming(view);
              }}
            />
          )}
          {canView(view.uuid, "share") && (
            <Button
              icon="pi pi-share-alt"
              text
              size="small"
              aria-label={`Share view ${view.display_name}`}
              onClick={() => setSharing(view)}
            />
          )}
          {canView(view.uuid, "delete") && views.length > 1 && (
            <Button
              icon="pi pi-trash"
              text
              severity="danger"
              size="small"
              aria-label={`Delete view ${view.display_name}`}
              onClick={() => confirmDelete(view)}
            />
          )}
        </div>
      ))}

      {canConfigure && (
        <div>
          <Button
            label="New view"
            icon="pi pi-plus"
            outlined
            size="small"
            onClick={() => {
              setName("");
              setCreating(true);
            }}
          />
          <small className="block pt-1" style={{ color: "var(--text-muted)" }}>
            Saves the sort, filter and columns showing right now.
          </small>
        </div>
      )}

      <Dialog
        header={renaming ? `Rename “${renaming.display_name}”` : "New view"}
        visible={creating || !!renaming}
        onHide={() => {
          setCreating(false);
          setRenaming(null);
        }}
        style={{ width: 360 }}
      >
        <div className="flex flex-col gap-4">
          {(create.error instanceof ApiError ||
            rename.error instanceof ApiError) && (
            <Message
              severity="error"
              text={
                ((create.error ?? rename.error) as ApiError).message
              }
            />
          )}
          <InputText
            placeholder="View name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoFocus
          />
          <div className="flex justify-end gap-2">
            <Button
              label="Cancel"
              text
              onClick={() => {
                setCreating(false);
                setRenaming(null);
              }}
            />
            <Button
              label={renaming ? "Save" : "Create"}
              onClick={() => (renaming ? rename.mutate() : create.mutate())}
              loading={create.isPending || rename.isPending}
              disabled={!name.trim()}
            />
          </div>
        </div>
      </Dialog>

      {sharing && (
        <ShareDialog
          visible={!!sharing}
          onHide={() => setSharing(null)}
          resourceType="view"
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
    </div>
  );
}
