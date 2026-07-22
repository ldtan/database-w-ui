import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DataTable } from "primereact/datatable";
import { Column as DataColumn } from "primereact/column";
import { Button } from "primereact/button";
import { Message } from "primereact/message";
import { Paginator } from "primereact/paginator";
import { confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { useToast } from "../../lib/toast";
import type { DeletedList } from "../../lib/types";
import { AppDialog } from "../../components/AppDialog";

const ROWS = 10;

/**
 * A workspace's list bin: soft-deleted lists still inside the retention window, each
 * with a Restore action.
 *
 * These are lists deleted on their own — a list that went down with its workspace is
 * recovered from the *workspace* bin instead. Restoring one brings back its columns,
 * records, views and forms. Access mirrors the records bin: gated on the parent
 * (here the workspace's `delete`), because a soft-deleted list has no permission
 * context of its own — the caller in ListsPage checks that before offering this.
 */
export function DeletedListsDialog({
  workspaceUuid,
  visible,
  onHide,
}: {
  workspaceUuid: string;
  visible: boolean;
  onHide: () => void;
}) {
  const queryClient = useQueryClient();
  const toast = useToast();

  const [first, setFirst] = useState(0);
  const [rows, setRows] = useState(ROWS);
  // Reopening the dialog starts at the top rather than wherever it was left.
  useEffect(() => {
    if (visible) setFirst(0);
  }, [visible]);

  const deleted = useQuery({
    queryKey: ["lists", "deleted", workspaceUuid, first, rows],
    queryFn: () =>
      api.getPage<DeletedList>(
        `/lists/deleted?workspace_uuid=${workspaceUuid}` +
          `&skip=${first}&limit=${rows}`,
      ),
    enabled: visible,
    placeholderData: (prev) => prev, // don't blank the table while paging
  });

  const items = deleted.data?.items ?? [];
  const total = deleted.data?.total ?? 0;

  const restore = useMutation({
    mutationFn: (uuid: string) => api.post(`/lists/${uuid}/restore`),
    onSuccess: () => {
      // The list (and its records) rejoin the workspace, and its restored grants
      // change what the page may show.
      queryClient.invalidateQueries({ queryKey: ["lists", workspaceUuid] });
      queryClient.invalidateQueries({ queryKey: ["lists", "deleted", workspaceUuid] });
      queryClient.invalidateQueries({ queryKey: ["effective"] });
      toast.success("List restored");
    },
    onError: (err) =>
      toast.error(
        "Could not restore list",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  const purge = useMutation({
    mutationFn: (uuid: string) => api.del(`/lists/${uuid}/permanent`),
    onSuccess: () => {
      // Gone for good; only the bin changes.
      queryClient.invalidateQueries({ queryKey: ["lists", "deleted", workspaceUuid] });
      toast.success("List permanently deleted");
    },
    onError: (err) =>
      toast.error(
        "Could not delete list",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  const confirmPurge = (uuid: string) =>
    confirmDialog({
      message:
        "Permanently delete this list and all its records? This cannot be undone.",
      header: "Delete permanently",
      icon: "pi pi-exclamation-triangle",
      acceptClassName: "p-button-danger",
      accept: () => purge.mutate(uuid),
    });

  return (
    <AppDialog
      header="Deleted lists"
      visible={visible}
      onHide={onHide}
      style={{ width: 640 }}
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>
          Lists deleted recently can be restored here, together with their columns
          and records. They are removed for good once the retention window passes.
        </p>

        {deleted.isLoading && <p>Loading…</p>}
        {!deleted.isLoading && items.length === 0 && (
          <Message severity="info" text="The bin is empty." />
        )}

        {total > 0 && (
          <>
            <Paginator
              first={first}
              rows={rows}
              totalRecords={total}
              rowsPerPageOptions={[10, 25, 50, 100]}
              onPageChange={(e) => {
                setFirst(e.first);
                setRows(e.rows);
              }}
              template="CurrentPageReport FirstPageLink PrevPageLink PageLinks NextPageLink LastPageLink RowsPerPageDropdown"
              currentPageReportTemplate="{first}–{last} of {totalRecords}"
            />

            <DataTable value={items} dataKey="uuid" stripedRows>
              <DataColumn
                header="List"
                body={(row: DeletedList) => (
                  <div>
                    <div className="font-medium">{row.display_name}</div>
                    <div
                      className="text-sm"
                      style={{ color: "var(--text-muted)" }}
                    >
                      {row.name}
                    </div>
                  </div>
                )}
              />
              <DataColumn
                header="Deleted"
                body={(row: DeletedList) => (
                  <span
                    className="text-sm whitespace-nowrap"
                    style={{ color: "var(--text-muted)" }}
                  >
                    {new Date(row.deleted_at).toLocaleString()}
                  </span>
                )}
              />
              <DataColumn
                header=""
                style={{ width: "11rem" }}
                body={(row: DeletedList) => (
                  <div className="flex gap-1">
                    <Button
                      label="Restore"
                      icon="pi pi-replay"
                      text
                      size="small"
                      onClick={() => restore.mutate(row.uuid)}
                      loading={restore.isPending && restore.variables === row.uuid}
                    />
                    <Button
                      icon="pi pi-trash"
                      text
                      size="small"
                      severity="danger"
                      aria-label="Delete permanently"
                      tooltip="Delete permanently"
                      onClick={() => confirmPurge(row.uuid)}
                      loading={purge.isPending && purge.variables === row.uuid}
                    />
                  </div>
                )}
              />
            </DataTable>
          </>
        )}
      </div>
    </AppDialog>
  );
}
