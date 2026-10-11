import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DataTable } from "primereact/datatable";
import { Column as DataColumn } from "primereact/column";
import { Button } from "primereact/button";
import { Message } from "primereact/message";
import { Paginator } from "primereact/paginator";
import { confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { BULK_MAX } from "../../lib/limits";
import { useToast } from "../../lib/toast";
import type { ArchivedTableRecord, Column, DeletedTableRecord } from "../../lib/types";
import { AppDialog } from "../../components/AppDialog";
import { collectLookupUuids } from "../lookups/LookupControls";
import { collectPrincipalUuids } from "../principals/PrincipalControls";
import { renderCell } from "./renderCell";

const ROWS = 10;

/** A record on one of the two holding surfaces. Both are a `TableRecord` plus the
 *  timestamp that put it there. */
type BinnedRow = DeletedTableRecord | ArchivedTableRecord;

export type BinKind = "deleted" | "archived";

/** A record a bulk restore left where it was, and why.
 *
 *  Only `bulk-restore` produces these. A binned row releases its `unique` values,
 *  so one may have been taken while it sat there — an ordinary thing to have
 *  happened, which is why the server answers 200 with the failures named rather
 *  than refusing the whole batch for one of them.
 */
type BulkConflict = { uuid: string; message: string };

/** Either bulk response: a count under the kind's own key, and conflicts only
 *  where they are possible. */
type BulkResponse = Record<string, number | BulkConflict[] | undefined> & {
  conflicts?: BulkConflict[];
};

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
    timestampOf: (row: BinnedRow) => (row as DeletedTableRecord).deleted_at,
    restorePath: "restore",
    restoreLabel: "Restore",
    restoredToast: "Record restored",
    restoreError: "Could not restore record",
    bulkPath: "bulk-restore",
    bulkCountKey: "restored",
    bulkLabel: "Restore",
    bulkToast: "Records restored",
    bulkError: "Could not restore records",
    // Only the bin can destroy: an archived record has no deadline and nothing
    // queued to remove it, so "delete for good" there would be a different
    // decision wearing the same button.
    purgeable: true,
  },
  archived: {
    header: "Archived records",
    blurb:
      "Archived records are kept out of the grid, searches, filters and totals — which is what keeps a long table fast. Nothing removes them; unarchive one to bring it back.",
    empty: "Nothing has been archived yet.",
    path: "archived",
    timestampHeader: "Archived",
    timestampOf: (row: BinnedRow) => (row as ArchivedTableRecord).archived_at,
    restorePath: "unarchive",
    restoreLabel: "Unarchive",
    restoredToast: "Record unarchived",
    restoreError: "Could not unarchive record",
    // Safe to offer in bulk precisely because coming back from the archive cannot
    // fail: an archived row never released its `unique` values, so the server
    // answers with a count and there is nothing per-row to report.
    bulkPath: "bulk-unarchive",
    bulkCountKey: "unarchived",
    bulkLabel: "Unarchive",
    bulkToast: "Records unarchived",
    bulkError: "Could not unarchive records",
    purgeable: false,
  },
} as const;

/**
 * The table's holding surfaces: the bin (soft-deleted, on a retention clock) and the
 * archive (finished with, kept indefinitely).
 *
 * A record is JSONB keyed by column uuid, so it can only be rendered against this
 * table's column definitions — which is why both live here (the table page already has
 * them) rather than in a global trash. Rows go through the shared `renderCell`, so a
 * reference or a link reads exactly as it does in the live grid.
 *
 * Access is gated by the *table* for both, and the reasons differ slightly. A
 * soft-deleted item resolves to no permission context at all, so there is nothing to
 * check per record. An archived one still does — but archiving is what takes a record
 * away from everyone who can read the table, so the authority to see the archive is
 * the authority to have put things in it. Either way the caller in `TableDetailPage`
 * checks table-level `delete` before offering the dialog, matching the routes.
 */
export function BinnedRecordsDialog({
  kind,
  tableUuid,
  columns,
  visible,
  onHide,
}: {
  kind: BinKind;
  tableUuid: string;
  columns: Column[];
  visible: boolean;
  onHide: () => void;
}) {
  const spec = KINDS[kind];
  const queryClient = useQueryClient();
  const toast = useToast();

  const [first, setFirst] = useState(0);
  const [rows, setRows] = useState(ROWS);
  // Held as rows rather than uuids so the table can mark them, and accumulated
  // across pages like the grid's — picking rows off page one and then paging must
  // not quietly drop them. PrimeReact only renders ticks for the rows it has, which
  // is why the count below is the honest statement of what is selected.
  const [selection, setSelection] = useState<BinnedRow[]>([]);
  // Held rather than only toasted: a conflict names a condition on the table that
  // somebody has to go and resolve, which is not something to read in three seconds
  // and then have to remember.
  const [conflicts, setConflicts] = useState<BulkConflict[]>([]);
  // Reopening the dialog starts at the top rather than wherever it was left, and
  // with nothing selected: a selection made before the dialog was closed is not a
  // decision the next person to open it should inherit.
  useEffect(() => {
    if (visible) {
      setFirst(0);
      setSelection([]);
      setConflicts([]);
    }
  }, [visible]);

  const binned = useQuery({
    queryKey: [kind, tableUuid, first, rows],
    queryFn: () =>
      api.getPage<BinnedRow>(
        `/tables/${tableUuid}/records/${spec.path}?skip=${first}&limit=${rows}`,
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
      api.post(`/tables/${tableUuid}/records/${uuid}/${spec.restorePath}`),
    onSuccess: () => {
      // The record leaves this surface and rejoins the live listing; both must
      // refetch, and its grants change what actions the page shows.
      queryClient.invalidateQueries({ queryKey: [kind, tableUuid] });
      queryClient.invalidateQueries({ queryKey: ["records", tableUuid] });
      queryClient.invalidateQueries({ queryKey: ["effective"] });
      toast.success(spec.restoredToast);
    },
    onError: (err) =>
      toast.error(
        spec.restoreError,
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  const bulkRestore = useMutation({
    mutationFn: async () => {
      // Chunked against the server's cap, and sequentially: the cap bounds what one
      // request costs, which several in flight would defeat. Same reasoning — and
      // the same constant — as the grid's bulk delete.
      const uuids = selection.map((row) => row.uuid);
      let moved = 0;
      const conflicts: BulkConflict[] = [];
      for (let i = 0; i < uuids.length; i += BULK_MAX) {
        const res = await api.post<BulkResponse>(
          `/tables/${tableUuid}/records/${spec.bulkPath}`,
          { uuids: uuids.slice(i, i + BULK_MAX) },
        );
        moved += (res[spec.bulkCountKey] as number) ?? 0;
        // Only a restore can answer with these; unarchive cannot conflict, so its
        // response carries a count and nothing else.
        if (res.conflicts) conflicts.push(...res.conflicts);
      }
      return { moved, conflicts };
    },
    onSuccess: ({ moved, conflicts }) => {
      queryClient.invalidateQueries({ queryKey: [kind, tableUuid] });
      queryClient.invalidateQueries({ queryKey: ["records", tableUuid] });
      queryClient.invalidateQueries({ queryKey: ["effective"] });
      setConflicts(conflicts);
      // The rows that came back leave this surface; the ones that did not stay
      // selected, so the next thing the person sees is the set still to deal with
      // rather than a cleared table and a toast they have to remember.
      const blocked = new Set(conflicts.map((c) => c.uuid));
      setSelection((prev) => prev.filter((row) => blocked.has(row.uuid)));
      if (conflicts.length > 0) {
        // Not `success`: some of what was asked for did not happen, and a green
        // toast over a partial result is how that gets missed. The detail below the
        // table is the durable statement — this is just the nudge towards it.
        toast.warn(
          `${moved} of ${moved + conflicts.length} restored`,
          `${conflicts.length} record(s) could not come back`,
        );
      } else {
        toast.success(spec.bulkToast, `${moved} record(s)`);
      }
    },
    onError: (err) => {
      // An earlier chunk may already have gone, so both surfaces are refetched
      // here too rather than left showing rows that have moved.
      queryClient.invalidateQueries({ queryKey: [kind, tableUuid] });
      queryClient.invalidateQueries({ queryKey: ["records", tableUuid] });
      toast.error(
        spec.bulkError,
        err instanceof ApiError ? err.message : undefined,
      );
    },
  });

  const purge = useMutation({
    mutationFn: (uuid: string) =>
      api.del(`/tables/${tableUuid}/records/${uuid}/permanent`),
    onSuccess: () => {
      // Gone for good; only the bin changes (it never returns to the live listing).
      queryClient.invalidateQueries({ queryKey: [kind, tableUuid] });
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

            <DataTable
              value={items}
              dataKey="uuid"
              stripedRows
              // "checkbox", not "multiple", for the reason the grid gives: a click
              // in the row should stay available for something else later, and a
              // selection belongs to the checkbox that shows it.
              //
              // `null` rather than left off when the kind has no bulk route —
              // PrimeReact's props are discriminated on this field, so an
              // `undefined` from a conditional spread matches neither variant.
              selectionMode={spec.bulkPath ? "checkbox" : null}
              selection={selection}
              onSelectionChange={(e: { value: unknown }) =>
                setSelection(e.value as BinnedRow[])
              }
            >
              {spec.bulkPath && (
                <DataColumn
                  selectionMode="multiple"
                  headerStyle={{ width: "3rem" }}
                />
              )}
              {columns.map((col) => (
                <DataColumn
                  key={col.uuid}
                  header={col.display_name}
                  body={(row: BinnedRow) =>
                    renderCell(col, row, tableUuid, principalUuids, lookupUuids)
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

            {/* What did not come back, and why — one line per distinct reason
                rather than per record, since fifty rows blocked by one taken value
                is one thing to go and fix, not fifty. The rows themselves are still
                selected and still in the table above. */}
            {conflicts.length > 0 && (
              <Message
                severity="warn"
                content={
                  <div className="flex flex-col gap-1 text-sm">
                    <span className="font-medium">
                      {conflicts.length} record(s) stayed in the bin
                    </span>
                    {[...new Set(conflicts.map((c) => c.message))].map(
                      (message) => (
                        <span key={message}>{message}</span>
                      ),
                    )}
                    <span style={{ color: "var(--text-muted)" }}>
                      Resolve the clash and try again — they are still restorable.
                    </span>
                  </div>
                }
              />
            )}

            {/* After the table, matching the grid's selection bar: the rows it
                applies to are what you were just looking at. */}
            {spec.bulkPath && selection.length > 0 && (
              <div className="flex items-center gap-3">
                <span className="text-sm font-medium">
                  {selection.length} selected
                </span>
                <Button
                  label={`${spec.bulkLabel} selected`}
                  icon="pi pi-replay"
                  text
                  size="small"
                  onClick={() => bulkRestore.mutate()}
                  loading={bulkRestore.isPending}
                />
                <Button
                  label="Clear"
                  text
                  size="small"
                  onClick={() => setSelection([])}
                />
              </div>
            )}
          </>
        )}
      </div>
    </AppDialog>
  );
}
