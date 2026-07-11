import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Dropdown } from "primereact/dropdown";
import { Button } from "primereact/button";
import { Dialog } from "primereact/dialog";
import { InputText } from "primereact/inputtext";
import { Message } from "primereact/message";
import { confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { slugify } from "../../lib/slug";
import { useToast } from "../../lib/toast";
import type { View, ViewConfig } from "../../lib/types";

interface Props {
  listUuid: string;
  views: View[];
  activeUuid: string | null;
  onSelect: (uuid: string) => void;
  currentConfig: ViewConfig;
}

export function ViewBar({
  listUuid,
  views,
  activeUuid,
  onSelect,
  currentConfig,
}: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["views", listUuid] });
    // Sort/filter are applied server-side by view, so records must refetch too.
    queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
  };

  const activeView = views.find((v) => v.uuid === activeUuid) ?? null;

  const save = useMutation({
    mutationFn: () =>
      api.patch<View>(`/lists/${listUuid}/views/${activeUuid}`, {
        config: currentConfig,
      }),
    onSuccess: () => {
      invalidate();
      toast.success("View saved");
    },
    onError: (err) =>
      toast.error(
        "Could not save view",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  // Saving overwrites the view's stored sort/filter/columns, so confirm first.
  function confirmSave() {
    confirmDialog({
      message: `Overwrite "${
        activeView?.display_name ?? "this view"
      }" with the current sort, filter, and columns?`,
      header: "Save view",
      icon: "pi pi-save",
      acceptLabel: "Save",
      accept: () => save.mutate(),
    });
  }

  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const create = useMutation({
    mutationFn: () =>
      api.post<View>(`/lists/${listUuid}/views/`, {
        name: slugify(newName),
        display_name: newName,
        config: currentConfig,
      }),
    onSuccess: (view) => {
      setCreating(false);
      setNewName("");
      invalidate();
      onSelect(view.uuid);
      toast.success("View created", view.display_name);
    },
  });

  return (
    <div className="flex items-center gap-2">
      <Dropdown
        value={activeUuid}
        options={views.map((v) => ({ label: v.display_name, value: v.uuid }))}
        onChange={(e) => onSelect(e.value as string)}
        placeholder="View"
        className="w-56"
      />
      <Button
        label="Save view"
        icon="pi pi-save"
        text
        onClick={confirmSave}
        loading={save.isPending}
        disabled={!activeUuid}
        tooltip="Save the current sort and page size to this view"
      />
      <Button
        label="New view"
        icon="pi pi-plus"
        text
        onClick={() => setCreating(true)}
      />

      <Dialog
        header="New view"
        visible={creating}
        onHide={() => setCreating(false)}
        style={{ width: 360 }}
      >
        <div className="flex flex-col gap-4">
          {create.error instanceof ApiError && (
            <Message severity="error" text={create.error.message} />
          )}
          <InputText
            placeholder="View name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            autoFocus
          />
          <div className="flex justify-end gap-2">
            <Button label="Cancel" text onClick={() => setCreating(false)} />
            <Button
              label="Create"
              onClick={() => create.mutate()}
              loading={create.isPending}
              disabled={!newName.trim()}
            />
          </div>
        </div>
      </Dialog>
    </div>
  );
}
