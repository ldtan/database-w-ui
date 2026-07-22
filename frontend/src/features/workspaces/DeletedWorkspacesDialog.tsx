import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DataTable } from "primereact/datatable";
import { Column as DataColumn } from "primereact/column";
import { Button } from "primereact/button";
import { Message } from "primereact/message";
import { Paginator } from "primereact/paginator";

import { api, ApiError } from "../../lib/api";
import { useToast } from "../../lib/toast";
import type { DeletedWorkspace } from "../../lib/types";
import { AppDialog } from "../../components/AppDialog";

const ROWS = 10;

/**
 * The workspaces bin: soft-deleted workspaces still inside the retention window,
 * each with a Restore action.
 *
 * The bin is your own — a deleted workspace resolves to no permission context, so
 * the backend scopes it to the owner (see WorkspaceService.list_deleted_for_owner).
 * Restoring one brings back the lists that went down with it, so it is offered
 * unconditionally on the Workspaces page rather than behind a permission gate.
 */
export function DeletedWorkspacesDialog({
  visible,
  onHide,
}: {
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
    queryKey: ["workspaces", "deleted", first, rows],
    queryFn: () =>
      api.getPage<DeletedWorkspace>(
        `/workspaces/deleted?skip=${first}&limit=${rows}`,
      ),
    enabled: visible,
    placeholderData: (prev) => prev, // don't blank the table while paging
  });

  const items = deleted.data?.items ?? [];
  const total = deleted.data?.total ?? 0;

  const restore = useMutation({
    mutationFn: (uuid: string) => api.post(`/workspaces/${uuid}/restore`),
    onSuccess: () => {
      // The workspace (and its lists) rejoin the live listing, and its restored
      // grants change what the page may show.
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      queryClient.invalidateQueries({ queryKey: ["effective"] });
      toast.success("Workspace restored");
    },
    onError: (err) =>
      toast.error(
        "Could not restore workspace",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  return (
    <AppDialog
      header="Deleted workspaces"
      visible={visible}
      onHide={onHide}
      style={{ width: 640 }}
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>
          Workspaces deleted recently can be restored here, together with the lists
          they held. They are removed for good once the retention window passes.
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
                header="Workspace"
                body={(row: DeletedWorkspace) => (
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
                body={(row: DeletedWorkspace) => (
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
                style={{ width: "7rem" }}
                body={(row: DeletedWorkspace) => (
                  <Button
                    label="Restore"
                    icon="pi pi-replay"
                    text
                    size="small"
                    onClick={() => restore.mutate(row.uuid)}
                    loading={restore.isPending && restore.variables === row.uuid}
                  />
                )}
              />
            </DataTable>
          </>
        )}
      </div>
    </AppDialog>
  );
}
