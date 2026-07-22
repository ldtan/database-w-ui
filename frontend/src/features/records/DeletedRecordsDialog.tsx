import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DataTable } from "primereact/datatable";
import { Column as DataColumn } from "primereact/column";
import { Button } from "primereact/button";
import { Message } from "primereact/message";
import { Paginator } from "primereact/paginator";
import { confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { useToast } from "../../lib/toast";
import type { Column, DeletedListItem } from "../../lib/types";
import { AppDialog } from "../../components/AppDialog";
import { collectLookupUuids } from "../lookups/LookupControls";
import { collectPrincipalUuids } from "../principals/PrincipalControls";
import { renderCell } from "./renderCell";

const ROWS = 10;

/**
 * The list's bin: soft-deleted records still inside the retention window, each with
 * a Restore action.
 *
 * A record is JSONB keyed by column uuid, so it can only be rendered against this
 * list's column definitions — which is why the bin lives here (the list page already
 * has them) rather than in a global trash. Rows go through the shared `renderCell`,
 * so a reference or a link reads exactly as it does in the live grid.
 *
 * Access is gated by the *list*, not the record: a soft-deleted item resolves to no
 * permission context, so the caller reaches this dialog only when they hold `delete`
 * on the list (the caller in `ListDetailPage` already checks that before offering it).
 */
export function DeletedRecordsDialog({
  listUuid,
  columns,
  visible,
  onHide,
}: {
  listUuid: string;
  columns: Column[];
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
    queryKey: ["deleted", listUuid, first, rows],
    queryFn: () =>
      api.getPage<DeletedListItem>(
        `/lists/${listUuid}/items/deleted?skip=${first}&limit=${rows}`,
      ),
    enabled: visible,
    placeholderData: (prev) => prev, // don't blank the table while paging
  });

  const items = deleted.data?.items ?? [];
  const total = deleted.data?.total ?? 0;

  // Same batching the live grid uses: gather the page's reference uuids so each cell
  // resolves its label from one shared request per column.
  const principalUuids = useMemo(
    () => collectPrincipalUuids(columns, items),
    [columns, items],
  );
  const lookupUuids = useMemo(
    () => collectLookupUuids(columns, items),
    [columns, items],
  );

  const restore = useMutation({
    mutationFn: (uuid: string) =>
      api.post(`/lists/${listUuid}/items/${uuid}/restore`),
    onSuccess: () => {
      // The record leaves the bin and rejoins the live listing; both must refetch,
      // and its restored grants change what actions the page shows.
      queryClient.invalidateQueries({ queryKey: ["deleted", listUuid] });
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      queryClient.invalidateQueries({ queryKey: ["effective"] });
      toast.success("Record restored");
    },
    onError: (err) =>
      toast.error(
        "Could not restore record",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  const purge = useMutation({
    mutationFn: (uuid: string) =>
      api.del(`/lists/${listUuid}/items/${uuid}/permanent`),
    onSuccess: () => {
      // Gone for good; only the bin changes (it never returns to the live listing).
      queryClient.invalidateQueries({ queryKey: ["deleted", listUuid] });
      toast.success("Record permanently deleted");
    },
    onError: (err) =>
      toast.error(
        "Could not delete record",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  const confirmPurge = (uuid: string) =>
    confirmDialog({
      message:
        "Permanently delete this record? This cannot be undone — it will not go to the bin.",
      header: "Delete permanently",
      icon: "pi pi-exclamation-triangle",
      acceptClassName: "p-button-danger",
      accept: () => purge.mutate(uuid),
    });

  return (
    <AppDialog
      header="Deleted records"
      visible={visible}
      onHide={onHide}
      style={{ width: 900 }}
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>
          Records deleted recently can be restored here. They are removed for good
          once the retention window passes.
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
              {columns.map((col) => (
                <DataColumn
                  key={col.uuid}
                  header={col.display_name}
                  body={(row: DeletedListItem) =>
                    renderCell(col, row, listUuid, principalUuids, lookupUuids)
                  }
                />
              ))}
              <DataColumn
                header="Deleted"
                body={(row: DeletedListItem) => (
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
                body={(row: DeletedListItem) => (
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
