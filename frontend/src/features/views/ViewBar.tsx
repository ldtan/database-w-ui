import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Dropdown } from "primereact/dropdown";
import { Button } from "primereact/button";
import { confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { useToast } from "../../lib/toast";
import type { View, ViewConfig } from "../../lib/types";
import { useEffectiveActionsMap } from "../permissions/useEffectiveActions";

interface Props {
  listUuid: string;
  views: View[];
  activeUuid: string | null;
  onSelect: (uuid: string) => void;
  currentConfig: ViewConfig;
}

/** Pick a view, and save what you are looking at into it.
 *
 *  Only that: creating, renaming, sharing and deleting a view are *managing* views
 *  rather than using one, and live in List settings → Views, alongside the other
 *  things that configure the list.
 */
export function ViewBar({
  listUuid,
  views,
  activeUuid,
  onSelect,
  currentConfig,
}: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();

  const activeView = views.find((v) => v.uuid === activeUuid) ?? null;
  // A view can carry its own grants, so saving one is gated per view, not by the
  // list as a whole.
  const canView = useEffectiveActionsMap(
    "view",
    views.map((view) => view.uuid),
  );
  const canSave = !!activeView && canView(activeView.uuid, "configure");

  const save = useMutation({
    mutationFn: () =>
      api.patch<View>(`/lists/${listUuid}/views/${activeUuid}`, {
        config: currentConfig,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["views", listUuid] });
      // Sort/filter are applied server-side by view, so records must refetch too.
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
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

  return (
    <div className="flex items-center gap-2">
      <Dropdown
        value={activeUuid}
        options={views.map((v) => ({ label: v.display_name, value: v.uuid }))}
        onChange={(e) => onSelect(e.value as string)}
        placeholder="View"
        className="w-56"
      />
      {canSave && (
        <Button
          label="Save view"
          icon="pi pi-save"
          text
          onClick={confirmSave}
          loading={save.isPending}
          tooltip="Save the current sort and page size to this view"
        />
      )}
    </div>
  );
}
