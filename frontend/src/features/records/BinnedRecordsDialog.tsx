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
import type { ArchivedListItem, Column, DeletedListItem } from "../../lib/types";
import { AppDialog } from "../../components/AppDialog";
import { collectLookupUuids } from "../lookups/LookupControls";
import { collectPrincipalUuids } from "../principals/PrincipalControls";
import { renderCell } from "./renderCell";

const ROWS = 10;

/** A record on one of the two holding surfaces. Both are a `ListItem` plus the
 *  timestamp that put it there. */
type BinnedRow = DeletedListItem | ArchivedListItem;

export type BinKind = "deleted" | "archived";

/** Everything that differs between the bin and the archive.
 *
 *  Written against the *kind* rather than copied per kind, matching
 *  `DeletedConfigDialog`, which serves columns, views, forms and metrics the same
 *  way. The two surfaces are the same table with the same cell rendering and the
 *  same paginator; what changes is which endpoint answers, what the timestamp
 *  means, and whether there is a permanent delete to offer.
 */
const KINDS = {
  deleted: {
    header: "Deleted records",
    // Why they are here and what happens next. The retention window is the whole
    // difference between the two surfaces, so it is what this sentence is about.
    blurb:
      "Records deleted recently can be restored here. They are removed for good once the retention window passes.",
    empty: "The bin is empty.",
    path: "deleted",
    timestampHeader: "Deleted",
    timestampOf: (row: BinnedRow) => (row as DeletedListItem).deleted_at,
    restorePath: "restore",
    restoreLabel: "Restore",
    restoredToast: "Record restored",
    restoreError: "Could not restore record",
    // Only the bin can destroy: an archived record has no deadline and nothing
    // queued to remove it, so "delete for good" there would be a different
    // decision wearing the same button.
    purgeable: true,
  },
  archived: {
    header: "Archived records",
    blurb:
      "Archived records are kept out of the grid, searches, filters and totals — which is what keeps a long list fast. Nothing removes them; unarchive one to bring it back.",
    empty: "Nothing has been archived yet.",
    path: "archived",
    timestampHeader: "Archived",
    timestampOf: (row: BinnedRow) => (row as ArchivedListItem).archived_at,
    restorePath: "unarchive",
    restoreLabel: "Unarchive",
    restoredToast: "Record unarchived",
    restoreError: "Could not unarchive record",
    purgeable: false,
  },
} as const;

/**
 * The list's holding surfaces: the bin (soft-deleted, on a retention clock) and the
 * archive (finished with, kept indefinitely).
 *
 * A record is JSONB keyed by column uuid, so it can only be rendered against this
 * list's column definitions — which is why both live here (the list page already has
 * them) rather than in a global trash. Rows go through the shared `renderCell`, so a
 * reference or a link reads exactly as it does in the live grid.
 *
 * Access is gated by the *list* for both, and the reasons differ slightly. A
 * soft-deleted item resolves to no permission context at all, so there is nothing to
 * check per record. An archived one still does — but archiving is what takes a record
 * away from everyone who can read the list, so the authority to see the archive is
 * the authority to have put things in it. Either way the caller in `ListDetailPage`
 * checks list-level `delete` before offering the dialog, matching the routes.
 */
export function BinnedRecordsDialog({
  kind,
  listUuid,
  columns,
  visible,
  onHide,
}: {
  kind: BinKind;
  listUuid: string;
  columns: Column[];
  visible: boolean;
  onHide: () => void;
}) {
  const spec = KINDS[kind];
  const queryClient = useQueryClient();
  const toast = useToast();

  const [first, setFirst] = useState(0);
  const [rows, setRows] = useState(ROWS);
  // Reopening the dialog starts at the top rather than wherever it was left.
  useEffect(() => {
    if (visible) setFirst(0);
  }, [visible]);

  const binned = useQuery({
    queryKey: [kind, listUuid, first, rows],
    queryFn: () =>
      api.getPage<BinnedRow>(
        `/lists/${listUuid}/items/${spec.path}?skip=${first}&limit=${rows}`,
      ),
    enabled: visible,
    placeholderData: (prev) => prev, // don't blank the table while paging
  });

  const items = binned.data?.items ?? [];
  const total = binned.data?.total ?? 0;

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
      api.post(`/lists/${listUuid}/items/${uuid}/${spec.restorePath}`),
    onSuccess: () => {
      // The record leaves this surface and rejoins the live listing; both must
      // refetch, and its grants change what actions the page shows.
      queryClient.invalidateQueries({ queryKey: [kind, listUuid] });
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      queryClient.invalidateQueries({ queryKey: ["effective"] });
      toast.success(spec.restoredToast);
    },
    onError: (err) =>
      toast.error(
        spec.restoreError,
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  const purge = useMutation({
    mutationFn: (uuid: string) =>
      api.del(`/lists/${listUuid}/items/${uuid}/permanent`),
    onSuccess: () => {
      // Gone for good; only the bin changes (it never returns to the live listing).
      queryClient.invalidateQueries({ queryKey: [kind, listUuid] });
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
      header={spec.header}
      visible={visible}
      onHide={onHide}
      style={{ width: 900 }}
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>
          {spec.blurb}
        </p>

        {binned.isLoading && <p>Loading…</p>}
        {!binned.isLoading && items.length === 0 && (
          <Message severity="info" text={spec.empty} />
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
                  body={(row: BinnedRow) =>
                    renderCell(col, row, listUuid, principalUuids, lookupUuids)
                  }
                />
              ))}
              <DataColumn
                header={spec.timestampHeader}
                body={(row: BinnedRow) => (
                  <span
                    className="text-sm whitespace-nowrap"
                    style={{ color: "var(--text-muted)" }}
                  >
                    {new Date(spec.timestampOf(row)).toLocaleString()}
                  </span>
                )}
              />
              <DataColumn
                header=""
                style={{ width: "11rem" }}
                body={(row: BinnedRow) => (
                  <div className="flex gap-1">
                    <Button
                      label={spec.restoreLabel}
                      icon="pi pi-replay"
                      text
                      size="small"
                      onClick={() => restore.mutate(row.uuid)}
                      loading={restore.isPending && restore.variables === row.uuid}
                    />
                    {spec.purgeable && (
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
                    )}
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
