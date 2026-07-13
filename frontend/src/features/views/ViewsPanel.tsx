import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "primereact/button";
import { Dialog } from "primereact/dialog";
import { InputText } from "primereact/inputtext";
import { Message } from "primereact/message";
import { confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { useToast } from "../../lib/toast";
import type { Column, View, ViewConfig } from "../../lib/types";
import { useEffectiveActionsMap } from "../permissions/useEffectiveActions";
import { ViewBar } from "./ViewBar";
import { ViewControls } from "./ViewControls";

export interface ViewControlsState {
  columns: Column[]; // in display order
  hiddenColumns: string[];
  onToggleColumn: (uuid: string, visible: boolean) => void;
  onMoveColumn: (index: number, delta: number) => void;
  groupBy: string | null;
  onGroupBy: (uuid: string | null) => void;
  filterCount: number;
  onOpenFilter: () => void;
  canFilter: boolean;
}

interface Props {
  listUuid: string;
  views: View[];
  activeUuid: string | null;
  onSelect: (uuid: string | null) => void;
  currentConfig: ViewConfig;
  controls: ViewControlsState;
}

/** The whole of a view's settings, in one place.
 *
 *  Everything here is also on the toolbar — the same components, not a second copy
 *  of them — because changing a filter while watching the grid react is the natural
 *  way to do it. This tab exists so the list's configuration is all reachable from
 *  one button, and adds the two things the toolbar has no room for: renaming a view
 *  and deleting one.
 */
export function ViewsPanel({
  listUuid,
  views,
  activeUuid,
  onSelect,
  currentConfig,
  controls,
}: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [renaming, setRenaming] = useState<View | null>(null);
  const [name, setName] = useState("");

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["views", listUuid] });
    queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
    queryClient.invalidateQueries({ queryKey: ["effective"] });
  };

  // A view can carry its own grants, so who may rename or delete one is decided per
  // view, not by the list.
  const canView = useEffectiveActionsMap(
    "view",
    views.map((v) => v.uuid),
  );

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

  function confirmDelete(view: View) {
    confirmDialog({
      message: `Delete the view “${view.display_name}”? The records stay; only this saved sort/filter goes.`,
      header: "Confirm",
      icon: "pi pi-exclamation-triangle",
      accept: () => remove.mutate(view.uuid),
    });
  }

  return (
    <div className="flex flex-col gap-4">
      {views.length === 0 && (
        <Message severity="info" text="This list has no views." />
      )}

      {/* Pick a view, save it, share it, add one — the toolbar's own bar. */}
      <ViewBar
        listUuid={listUuid}
        views={views}
        activeUuid={activeUuid}
        onSelect={onSelect}
        currentConfig={currentConfig}
        showShare
      />

      {/* What the current view shows: filter, grouping, columns. */}
      <ViewControls {...controls} stacked />

      <div className="flex flex-col gap-2">
        <span className="text-sm font-medium">All views</span>
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
      </div>

      <Dialog
        header={renaming ? `Rename “${renaming.display_name}”` : ""}
        visible={!!renaming}
        onHide={() => setRenaming(null)}
        style={{ width: 360 }}
      >
        <div className="flex flex-col gap-4">
          {rename.error instanceof ApiError && (
            <Message severity="error" text={rename.error.message} />
          )}
          <InputText
            placeholder="View name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoFocus
          />
          <div className="flex justify-end gap-2">
            <Button label="Cancel" text onClick={() => setRenaming(null)} />
            <Button
              label="Save"
              onClick={() => rename.mutate()}
              loading={rename.isPending}
              disabled={!name.trim()}
            />
          </div>
        </div>
      </Dialog>
    </div>
  );
}
