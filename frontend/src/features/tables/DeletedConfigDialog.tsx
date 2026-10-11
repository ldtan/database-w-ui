import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { QueryKey } from "@tanstack/react-query";
import { DataTable } from "primereact/datatable";
import { Column as DataColumn } from "primereact/column";
import { Button } from "primereact/button";
import { Message } from "primereact/message";
import { Paginator } from "primereact/paginator";
import { confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { useToast } from "../../lib/toast";
import type { BinnedChild, RestoredColumn } from "../../lib/types";
import { AppDialog } from "../../components/AppDialog";

const ROWS = 10;

/** The four things a table is configured with, each of which has a bin behind the
 *  same three routes: `/deleted`, `/{uuid}/restore`, `/{uuid}/permanent`. */
export type ConfigKind = "columns" | "views" | "forms" | "metrics";

interface KindSpec {
  header: string;
  /** Lower-case and singular: goes into toasts and the confirm prompt. */
  noun: string;
  blurb: string;
  purgePrompt: string;
  /** What a *restore* puts back on screen. A purge changes nothing but the bin
   *  itself, which is why it is not a second table — see `purge` below. */
  restored: (tableUuid: string) => QueryKey[];
}

const KINDS: Record<ConfigKind, KindSpec> = {
  columns: {
    header: "Deleted columns",
    noun: "column",
    blurb:
      "A deleted column's values were only hidden, never removed — restoring one " +
      "shows them again in every record that has them.",
    purgePrompt:
      "Permanently delete this column and its values in every record? This cannot be undone.",
    // The values reappear in the grid, and a restored lookup or user column has
    // labels to resolve again — the same set ColumnDialog invalidates on save.
    restored: (tableUuid) => [
      ["columns", tableUuid],
      ["records", tableUuid],
      ["lookup-labels"],
      ["principal-labels"],
    ],
  },
  views: {
    header: "Deleted views",
    noun: "view",
    blurb:
      "A view is a saved sort, filter and column layout. Deleting one never touched " +
      "the records, and restoring one brings back the grants it carried.",
    purgePrompt:
      "Permanently delete this view? The records are untouched; only this saved layout goes. This cannot be undone.",
    restored: (tableUuid) => [["views", tableUuid], ["effective"]],
  },
  forms: {
    header: "Deleted forms",
    noun: "form",
    blurb:
      "A form is a layout for entering a record. Deleting one never touched the " +
      "records, and restoring one brings back the grants it carried.",
    purgePrompt:
      "Permanently delete this form? The records are untouched; only this layout goes. This cannot be undone.",
    restored: (tableUuid) => [["forms", tableUuid], ["effective"]],
  },
  metrics: {
    header: "Deleted metrics",
    noun: "metric",
    blurb:
      "A metric holds no data of its own — it counts or totals the records. Restoring " +
      "one puts its card back above them.",
    purgePrompt:
      "Permanently delete this metric? The records it counted are untouched. This cannot be undone.",
    // The definition, and the values keyed under ["records", …] that the card reads.
    restored: (tableUuid) => [["metrics", tableUuid], ["records", tableUuid]],
  },
};

/**
 * The bin for one of a table's four config kinds: columns, views, forms, metrics.
 *
 * One component for all four because the API is one module behind four prefixes —
 * same `BinnedChildRead` row, same three routes, same table-level `configure` gate.
 * Four near-identical dialogs would be four places to fix anything that turned out
 * wrong about the first one.
 *
 * Mounted from each kind's own settings panel rather than from a single "deleted
 * things" screen: the bin belongs beside the table it holds, which is where the
 * records, tables and workspaces bins already sit.
 *
 * **The bin's query key sits under its kind's live key** (`["views", tableUuid,
 * "deleted", …]`), so every existing `invalidateQueries({ queryKey: ["views",
 * tableUuid] })` in the app refreshes the bin by prefix match. Deleting a view with
 * this dialog shut therefore leaves the bin correct when it is next opened, without
 * the delete having to know the bin exists.
 */
export function DeletedConfigDialog({
  tableUuid,
  kind,
  visible,
  onHide,
}: {
  tableUuid: string;
  kind: ConfigKind;
  visible: boolean;
  onHide: () => void;
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const spec = KINDS[kind];
  // The same noun starts a sentence in the toasts and sits mid-sentence in the
  // errors, so it is stored once in the form the errors need.
  const Noun = spec.noun[0].toUpperCase() + spec.noun.slice(1);

  const [first, setFirst] = useState(0);
  const [rows, setRows] = useState(ROWS);
  // Reopening the dialog starts at the top rather than wherever it was left.
  useEffect(() => {
    if (visible) setFirst(0);
  }, [visible]);

  const binKey: QueryKey = [kind, tableUuid, "deleted", first, rows];

  const deleted = useQuery({
    queryKey: binKey,
    queryFn: () =>
      api.getPage<BinnedChild>(
        `/tables/${tableUuid}/${kind}/deleted?skip=${first}&limit=${rows}`,
      ),
    enabled: visible,
    placeholderData: (prev) => prev, // don't blank the table while paging
  });

  const items = deleted.data?.items ?? [];
  const total = deleted.data?.total ?? 0;

  const restore = useMutation({
    mutationFn: (uuid: string) =>
      api.post<unknown>(`/tables/${tableUuid}/${kind}/${uuid}/restore`),
    onSuccess: (data) => {
      for (const key of spec.restored(tableUuid)) {
        queryClient.invalidateQueries({ queryKey: key });
      }
      // Restoring a column only un-hides it. If deleting it also cleared the table's
      // primary column, nothing recorded that, so the restore cannot put it back —
      // the response says whether the table is currently without one, and that is
      // worth saying out loud rather than leaving to be noticed.
      if (kind === "columns" && (data as RestoredColumn).table_has_no_primary_column) {
        toast.warn(
          "Column restored",
          "The table has no primary column: deleting this one clears it, and " +
            "restoring does not put it back. Lookups at this table label their " +
            "rows by its first column.",
        );
        return;
      }
      toast.success(`${Noun} restored`);
    },
    onError: (err) =>
      // A restore genuinely can fail: the name is only unique among live rows, so
      // something created while this sat here may hold it now.
      toast.error(
        `Could not restore ${spec.noun}`,
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  const purge = useMutation({
    mutationFn: (uuid: string) =>
      api.del(`/tables/${tableUuid}/${kind}/${uuid}/permanent`),
    onSuccess: () => {
      // Gone for good; only the bin changes. A purged column's values go with it,
      // but they were already hidden, so nothing on the page was showing them.
      queryClient.invalidateQueries({ queryKey: [kind, tableUuid, "deleted"] });
      toast.success(`${Noun} permanently deleted`);
    },
    onError: (err) =>
      toast.error(
        `Could not delete ${spec.noun}`,
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  const confirmPurge = (uuid: string) =>
    confirmDialog({
      message: spec.purgePrompt,
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
      style={{ width: 640 }}
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>
          {spec.blurb} They are removed for good once the retention window passes.
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
                header="Name"
                body={(row: BinnedChild) => (
                  <div>
                    <div className="font-medium">{row.display_name}</div>
                    <div
                      className="text-sm"
                      style={{ color: "var(--text-muted)" }}
                    >
                      {row.description || row.name}
                    </div>
                  </div>
                )}
              />
              <DataColumn
                header="Deleted"
                body={(row: BinnedChild) => (
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
                body={(row: BinnedChild) => (
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
