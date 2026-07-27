import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DataTable, type DataTableSortEvent } from "primereact/datatable";
import { Column as DataColumn } from "primereact/column";
import { Button } from "primereact/button";
import { SplitButton } from "primereact/splitbutton";
import { Dropdown } from "primereact/dropdown";
import { InputText } from "primereact/inputtext";
import { Message } from "primereact/message";
import { ConfirmDialog, confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { useToast } from "../../lib/toast";
import { useDebounced } from "../../lib/useDebounced";
import { useLiveList } from "../../lib/useLiveList";
import { SMALL_SCREEN, useMediaQuery } from "../../lib/useMediaQuery";
import { Breadcrumbs, type Crumb } from "../../components/Breadcrumbs";
import { RowActions } from "../../components/RowActions";
import type {
  Column,
  Form,
  ListItem,
  ListRead,
  View,
  ViewConfig,
  Workspace,
} from "../../lib/types";
import { ListSettingsDialog } from "./ListSettingsDialog";
import { RecordCards } from "../records/RecordCards";
import { RecordFormDialog } from "../records/RecordFormDialog";
import { DeletedRecordsDialog } from "../records/DeletedRecordsDialog";
import { fillFromQr, parseQrPayload } from "../records/qrFill";
import { resolveFields } from "../records/recordValues";
import { renderCell } from "../records/renderCell";
import { sortRows } from "../records/sortRows";
import { ImportExportDialog } from "../transfer/ImportExportDialog";
import { collectLookupUuids } from "../lookups/LookupControls";
import { collectPrincipalUuids } from "../principals/PrincipalControls";
import { ViewBar } from "../views/ViewBar";
import { ViewControls } from "../views/ViewControls";
import { FilterDialog } from "../views/FilterDialog";
import { ShareDialog } from "../permissions/ShareDialog";
import {
  useEffectiveActions,
  useEffectiveActionsMap,
} from "../permissions/useEffectiveActions";

type SortOrder = 1 | 0 | -1 | null | undefined;

// Split out of the bundle: the decoder it pulls in is ~110 kB gzipped — a quarter
// of the whole app — for a dialog most sessions never open. It arrives when the
// scanner does. (`qrFill`, which is small and pure, stays in the main bundle.)
const QrScanDialog = lazy(() =>
  import("../records/QrScanDialog").then((m) => ({ default: m.QrScanDialog })),
);
// Lazy for the same reason: it renders the scanner, so importing it eagerly would
// drag the decoder back into the main bundle by the back door.
const QrSubmitSession = lazy(() =>
  import("../records/QrSubmitSession").then((m) => ({
    default: m.QrSubmitSession,
  })),
);

/**
 * A list's records, and everything you do to them.
 *
 * The page owns the *view state* — sort, grouping, hidden columns, column order,
 * page size, the search term — and hands it to whatever renders the records. It is
 * seeded from the active view's saved config and diverges from it as you work; the
 * "Save view" button in the toolbar writes it back, which is why nothing here
 * persists on its own.
 *
 * Two renderers, one state: `DataTable` on a desktop, `RecordCards` below 640px
 * (see `useMediaQuery`). They share the same selection, the same page size, the
 * same per-record permissions and the same `renderCell` and `sortRows` — a value
 * or an order that differed between them would be a bug, not a design.
 *
 * Filtering and searching are *server-side* (`?view_uuid=`, `?q=`), so the records
 * arriving here are already the ones to show; sorting and paging are client-side
 * over that page of records. Grouping pins the sort to the group column, because
 * the server has already ordered by it.
 */
export function ListDetailPage() {
  const { listUuid = "" } = useParams();
  const queryClient = useQueryClient();
  const toast = useToast();
  // A table of a dozen columns is unreadable on a phone; below 640px the records
  // render as cards instead. Same data, same actions, same selection — see
  // RecordCards.
  const small = useMediaQuery(SMALL_SCREEN);
  const [showSettings, setShowSettings] = useState(false);
  const [showTransfer, setShowTransfer] = useState(false);
  const [showDeleted, setShowDeleted] = useState(false);
  const [sharingItemUuid, setSharingItemUuid] = useState<string | null>(null);
  const [recordDialog, setRecordDialog] = useState<{
    open: boolean;
    item: ListItem | null;
    // Both set only by the QR scanner: what it read, and what it could not.
    prefill?: Record<string, unknown> | null;
    notes?: string[];
  }>({ open: false, item: null });
  const [selection, setSelection] = useState<ListItem[]>([]);
  // Which QR flow is running, if any: fill the form, or submit it outright.
  const [qrMode, setQrMode] = useState<"fill" | "submit" | null>(null);
  const [qrBusy, setQrBusy] = useState(false);
  const [qrError, setQrError] = useState<string | null>(null);
  // What a submitting session sets on every scan. Held here rather than inside the
  // session so that closing the scanner and opening it again keeps the event you
  // are taking attendance for. Deliberately not persisted any further than that:
  // it is the answer to "what is happening right now", not a setting.
  const [qrPresets, setQrPresets] = useState<Record<string, unknown>>({});

  const [activeViewUuid, setActiveViewUuid] = useState<string | null>(null);
  const [sortField, setSortField] = useState<string | undefined>(undefined);
  const [sortOrder, setSortOrder] = useState<SortOrder>(undefined);
  const [pageSize, setPageSize] = useState(25);
  const [showFilter, setShowFilter] = useState(false);
  const [search, setSearch] = useState("");
  // Debounced so a query goes out per pause, not per keystroke.
  const q = useDebounced(search);
  const [groupBy, setGroupBy] = useState<string | null>(null);
  const [hiddenColumns, setHiddenColumns] = useState<string[]>([]);
  const [columnOrder, setColumnOrder] = useState<string[]>([]);

  const list = useQuery({
    queryKey: ["list", listUuid],
    queryFn: () => api.get<ListRead>(`/lists/${listUuid}`),
  });
  const columns = useQuery({
    queryKey: ["columns", listUuid],
    queryFn: () => api.get<Column[]>(`/lists/${listUuid}/columns/`),
  });
  // Live records: keep this list current as other people add/edit/remove rows.
  // Spread first so the query's own keys/fn win; today it supplies a polling
  // `refetchInterval`, later an SSE subscription (see useLiveList).
  const live = useLiveList(listUuid);
  const items = useQuery({
    ...live,
    // The search is part of the key: it is applied server-side, so the result is a
    // different set of records, not a filtered view of the same one.
    queryKey: ["items", listUuid, activeViewUuid, q],
    queryFn: () => {
      const params = new URLSearchParams();
      if (activeViewUuid) params.set("view_uuid", activeViewUuid);
      if (q.trim()) params.set("q", q.trim());
      const query = params.toString();
      return api.get<ListItem[]>(
        `/lists/${listUuid}/items/${query ? `?${query}` : ""}`,
      );
    },
  });
  const views = useQuery({
    queryKey: ["views", listUuid],
    queryFn: () => api.get<View[]>(`/lists/${listUuid}/views/`),
  });
  const forms = useQuery({
    queryKey: ["forms", listUuid],
    queryFn: () => api.get<Form[]>(`/lists/${listUuid}/forms/`),
  });

  // A list can be shared without its workspace, in which case this 403s and the
  // workspace simply drops out of the breadcrumb trail — there is nothing the
  // caller could navigate to anyway.
  const workspaceUuid = list.data?.workspace.uuid;
  const workspace = useQuery({
    queryKey: ["workspace", workspaceUuid],
    queryFn: () => api.get<Workspace>(`/workspaces/${workspaceUuid}`),
    enabled: !!workspaceUuid,
    retry: false,
  });
  const crumbs: Crumb[] = [
    { label: "Workspaces", to: "/workspaces" },
    ...(workspace.data
      ? [
          {
            label: workspace.data.display_name,
            to: `/workspaces/${workspace.data.uuid}`,
          },
        ]
      : []),
    { label: list.data?.display_name ?? "List" },
  ];

  // Records and views can each carry their own grants, so row- and view-level
  // controls are gated separately from the list as a whole.
  const listActions = useEffectiveActions("list", listUuid);
  const canItem = useEffectiveActionsMap(
    "item",
    (items.data ?? []).map((item) => item.uuid),
    listActions,
  );
  const canView = useEffectiveActionsMap(
    "view",
    (views.data ?? []).map((view) => view.uuid),
  );

  // Default to the first view once loaded.
  useEffect(() => {
    if (!activeViewUuid && views.data && views.data.length > 0) {
      setActiveViewUuid(views.data[0].uuid);
    }
  }, [views.data, activeViewUuid]);

  // Apply the active view's saved sort + page size.
  useEffect(() => {
    const view = views.data?.find((v) => v.uuid === activeViewUuid);
    if (!view) return;
    const sort = view.config.sorts?.[0];
    setSortField(sort ? `data.${sort.column_uuid}` : undefined);
    setSortOrder(sort ? (sort.direction === "asc" ? 1 : -1) : undefined);
    setPageSize(view.config.page_size ?? 25);
    setGroupBy(view.config.group_by?.column_uuid ?? null);
    setHiddenColumns(view.config.hidden_columns ?? []);
    setColumnOrder(view.config.column_order ?? []);
    setSelection([]); // selection doesn't carry across views
  }, [activeViewUuid, views.data]);

  const deleteItem = useMutation({
    mutationFn: (uuid: string) => api.del(`/lists/${listUuid}/items/${uuid}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      toast.success("Record deleted");
    },
    onError: (err) =>
      toast.error(
        "Could not delete record",
        err instanceof ApiError ? err.message : undefined,
      ),
  });
  const setListInherit = useMutation({
    mutationFn: (value: boolean) =>
      api.patch(`/lists/${listUuid}`, { permissions_inherit: value }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["list", listUuid] });
      queryClient.invalidateQueries({ queryKey: ["effective"] });
    },
    onError: (err) =>
      toast.error(
        "Could not update inheritance",
        err instanceof ApiError ? err.message : undefined,
      ),
  });
  const setItemInherit = useMutation({
    mutationFn: (vars: { uuid: string; value: boolean }) =>
      api.patch(`/lists/${listUuid}/items/${vars.uuid}`, {
        permissions_inherit: vars.value,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      queryClient.invalidateQueries({ queryKey: ["effective"] });
    },
    onError: (err) =>
      toast.error(
        "Could not update inheritance",
        err instanceof ApiError ? err.message : undefined,
      ),
  });
  // A selection can mix rows the caller may delete with rows they may not (the
  // server ignores the latter anyway), so only send — and only count — the ones
  // that will actually go.
  const deletableSelection = selection.filter((item) =>
    canItem(item.uuid, "delete"),
  );
  const bulkDelete = useMutation({
    mutationFn: () =>
      api.post<{ deleted: number }>(`/lists/${listUuid}/items/bulk-delete`, {
        uuids: deletableSelection.map((item) => item.uuid),
      }),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      setSelection([]);
      toast.success("Records deleted", `${res.deleted} record(s)`);
    },
    onError: (err) =>
      toast.error(
        "Could not delete records",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  const cols = columns.data ?? [];
  // Apply the view's column order first (for the columns it names), then any
  // remaining columns in their default position order (so new columns appear).
  const orderedCols: Column[] = (() => {
    const byUuid = new Map(cols.map((c) => [c.uuid, c]));
    const seen = new Set<string>();
    const result: Column[] = [];
    for (const uuid of columnOrder) {
      const c = byUuid.get(uuid);
      if (c) {
        result.push(c);
        seen.add(uuid);
      }
    }
    for (const c of cols) if (!seen.has(c.uuid)) result.push(c);
    return result;
  })();
  const visibleCols = orderedCols.filter(
    (c) => !hiddenColumns.includes(c.uuid),
  );

  // The reference uuids this page of records holds. The cells resolve them (one
  // shared request per column — see renderCell); the page only has to gather them,
  // so that the query key covers the whole page rather than a single row.
  const principalUuids = useMemo(
    () => collectPrincipalUuids(cols, items.data ?? []),
    [cols, items.data],
  );
  const lookupUuids = useMemo(
    () => collectLookupUuids(cols, items.data ?? []),
    [cols, items.data],
  );

  const formList = forms.data ?? [];
  const sharingItem =
    (items.data ?? []).find((i) => i.uuid === sharingItemUuid) ?? null;
  // Editing uses the "update" form; creating uses the "create" form.
  const recordForm =
    formList.find(
      (f) => f.type === (recordDialog.item ? "update" : "create"),
    ) ?? null;

  // A QR code always creates, so it is always the "create" form it fills — which
  // is what decides the fields it may write, and their names. Resolving them here
  // (rather than reading `cols`) is what stops a code from filling a column the
  // form excludes or marks read-only.
  const createForm = formList.find((f) => f.type === "create") ?? null;
  const createFields = useMemo(
    () => resolveFields(cols, createForm),
    [cols, createForm],
  );

  function openQr(mode: "fill" | "submit") {
    setQrError(null);
    setQrMode(mode);
  }

  /**
   * A scanned code, into the Add-record form.
   *
   * This is the "fill" flow only — one code, checked by a person before it is
   * saved. Submitting outright is a session rather than a scan (presets, a queue,
   * a tally), and lives in `QrSubmitSession`.
   */
  async function handleQrScan(text: string) {
    setQrBusy(true);
    setQrError(null);
    try {
      const payload = parseQrPayload(text);
      const fill = await fillFromQr(listUuid, createFields, payload);
      if (fill.filled.length === 0 && fill.issues.length === 0) {
        setQrError("This QR code has no fields for this list.");
        return;
      }
      setRecordDialog({
        open: true,
        item: null,
        prefill: fill.values,
        notes: fill.issues.map((issue) => `${issue.key}: ${issue.message}`),
      });
      setQrMode(null);
    } catch (err) {
      setQrError(
        err instanceof Error ? err.message : "Could not read that QR code.",
      );
    } finally {
      setQrBusy(false);
    }
  }

  const activeView = views.data?.find((v) => v.uuid === activeViewUuid) ?? null;
  const filterCount = activeView?.config.filters?.conditions?.length ?? 0;
  const groupColumn = groupBy
    ? (cols.find((c) => c.uuid === groupBy) ?? null)
    : null;
  const currentConfig: ViewConfig = {
    sorts: sortField
      ? [
          {
            column_uuid: sortField.replace(/^data\./, ""),
            direction: sortOrder === 1 ? "asc" : "desc",
          },
        ]
      : [],
    page_size: pageSize,
    filters: activeView?.config.filters ?? {
      conjunction: "and",
      conditions: [],
    },
    group_by: groupBy ? { column_uuid: groupBy } : null,
    hidden_columns: hiddenColumns,
    column_order: columnOrder,
  };

  function toggleColumn(uuid: string, visible: boolean) {
    setHiddenColumns((prev) =>
      visible ? prev.filter((u) => u !== uuid) : [...new Set([...prev, uuid])],
    );
  }
  // Materialize the full effective order, swap two entries, and store it — so
  // column_order is always a complete ordering after any manual reorder.
  function moveColumn(index: number, delta: number) {
    const target = index + delta;
    if (target < 0 || target >= orderedCols.length) return;
    const next = orderedCols.map((c) => c.uuid);
    [next[index], next[target]] = [next[target], next[index]];
    setColumnOrder(next);
  }

  // One object, handed to both the toolbar and List settings → Views, so the two
  // drive the same state rather than each keeping their own.
  const viewControls = {
    columns: orderedCols,
    hiddenColumns,
    onToggleColumn: toggleColumn,
    onMoveColumn: moveColumn,
    groupBy,
    onGroupBy: setGroupBy,
    filterCount,
    onOpenFilter: () => setShowFilter(true),
    // Filters live on the view, so editing them is a change to that view.
    canFilter: !!activeViewUuid && canView(activeViewUuid, "configure"),
  };

  // The cards have no column header to sort by, so the page sorts for them —
  // through the same sortRows the grid's columns use, so the two layouts cannot
  // disagree about what "sorted by Name" means. Grouping pins the order to the
  // group column (the server already returned the records in it), so it is left
  // alone. Only computed for the layout that needs it.
  const sortedItems = useMemo(
    () =>
      small && !groupColumn
        ? sortRows(items.data ?? [], sortField, sortOrder)
        : (items.data ?? []),
    [small, items.data, sortField, sortOrder, groupColumn],
  );

  function groupHeader(row: ListItem) {
    if (!groupColumn) return null;
    return (
      <span className="font-semibold">
        {groupColumn.display_name}:{" "}
        {renderCell(groupColumn, row, listUuid, principalUuids, lookupUuids)}
      </span>
    );
  }

  function confirmDeleteItem(row: ListItem) {
    confirmDialog({
      message: "Delete this record?",
      header: "Confirm",
      icon: "pi pi-exclamation-triangle",
      accept: () => deleteItem.mutate(row.uuid),
    });
  }
  function confirmBulkDelete() {
    confirmDialog({
      message: `Delete ${deletableSelection.length} selected record(s)?`,
      header: "Confirm",
      icon: "pi pi-exclamation-triangle",
      accept: () => bulkDelete.mutate(),
    });
  }

  return (
    <div className="flex flex-col gap-4">
      <ConfirmDialog />

      <div className="flex flex-wrap items-center justify-between gap-2">
        <Breadcrumbs items={crumbs} />
        <div className="flex flex-wrap gap-2">
          {/* Columns, permissions and forms all configure the *list*, so they live
              together behind one button; the toolbar is then about the data. */}
          <Button
            label="Settings"
            icon="pi pi-cog"
            outlined
            onClick={() => setShowSettings(true)}
            disabled={!list.data}
          />
          <Button
            label="Import / Export"
            icon="pi pi-file-import"
            outlined
            onClick={() => setShowTransfer(true)}
            disabled={cols.length === 0}
          />
          {/* The bin is a recovery surface gated on list-level delete (a deleted
              record has no per-item permission to check). */}
          {listActions.has("delete") && (
            <Button
              label="Deleted records"
              icon="pi pi-trash"
              outlined
              onClick={() => setShowDeleted(true)}
              disabled={cols.length === 0}
            />
          )}
          {/* The button still adds a record; the dropdown is the same act with a QR
              code doing the typing. */}
          {listActions.has("create") && (
            <SplitButton
              label="Add record"
              icon="pi pi-plus"
              onClick={() => setRecordDialog({ open: true, item: null })}
              disabled={cols.length === 0}
              model={[
                {
                  label: "Fill using QR code",
                  icon: "pi pi-qrcode",
                  command: () => openQr("fill"),
                },
                {
                  label: "Submit using QR code",
                  icon: "pi pi-qrcode",
                  command: () => openQr("submit"),
                },
              ]}
            />
          )}
        </div>
      </div>

      {/* Sort and search are about the *records*, so the toolbar stands whether or
          not the list has views: a list whose views were all deleted still has its
          records, and used to lose the search box — and, briefly, the sort control —
          along with them. Only the view-shaped controls come and go. */}
      <div className="flex flex-wrap items-center gap-2">
        {views.data && views.data.length > 0 && (
          <>
            <ViewBar
              listUuid={listUuid}
              views={views.data}
              activeUuid={activeViewUuid}
              onSelect={setActiveViewUuid}
              currentConfig={currentConfig}
            />
            <ViewControls {...viewControls} />
          </>
        )}

        {/* Sorting otherwise lives in the grid's column headers, which a card
              list does not have — so on a phone there was no way to sort at all.
              Here it drives the same sortField/sortOrder the headers do, so the
              two agree and a header click moves this dropdown. Grouping pins the
              order to the group column, which is why it disables. */}
        <Dropdown
          value={sortField ?? null}
          options={orderedCols.map((c) => ({
            label: c.display_name,
            value: `data.${c.uuid}`,
          }))}
          onChange={(e) => {
            setSortField(e.value ?? undefined);
            if (e.value && !sortOrder) setSortOrder(1);
          }}
          placeholder="Sort by…"
          showClear
          disabled={cols.length === 0 || !!groupColumn}
          className="w-40"
        />
        <Button
          icon={
            sortOrder === -1 ? "pi pi-sort-amount-down" : "pi pi-sort-amount-up"
          }
          text
          aria-label={sortOrder === -1 ? "Sort ascending" : "Sort descending"}
          tooltip={sortOrder === -1 ? "Descending" : "Ascending"}
          disabled={!sortField || !!groupColumn}
          onClick={() => setSortOrder(sortOrder === -1 ? 1 : -1)}
        />

        {/* pl-8 by hand: the icon is positioned over the input, and the theme's
              reset does not give p-input-icon-left the padding to clear it.
              Once the row wraps, search takes the whole of its own line rather
              than sitting at 16rem next to nothing. */}
        <span className="p-input-icon-left w-full sm:w-auto sm:ml-auto">
          <i className="pi pi-search pl-3" />
          <InputText
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search records…"
            disabled={cols.length === 0}
            className="w-full sm:w-64 pl-9"
          />
        </span>
      </div>

      {cols.length === 0 && !columns.isLoading && (
        <Message
          severity="info"
          text={
            listActions.has("configure")
              ? "This list has no columns yet. Add them under Settings → Columns, then add records."
              : "This list has no columns yet."
          }
        />
      )}

      {selection.length > 0 && (
        <div className="flex items-center gap-2">
          <span className="text-sm text-gray-600">
            {selection.length} selected
          </span>
          {deletableSelection.length > 0 && (
            <Button
              label={
                deletableSelection.length === selection.length
                  ? "Delete selected"
                  : `Delete ${deletableSelection.length} of ${selection.length}`
              }
              icon="pi pi-trash"
              severity="danger"
              outlined
              size="small"
              onClick={confirmBulkDelete}
              loading={bulkDelete.isPending}
            />
          )}
          <Button
            label="Clear"
            text
            size="small"
            onClick={() => setSelection([])}
          />
        </div>
      )}

      {small ? (
        <RecordCards
          listUuid={listUuid}
          items={sortedItems}
          columns={visibleCols}
          loading={columns.isLoading || items.isLoading}
          principalUuids={principalUuids}
          lookupUuids={lookupUuids}
          selection={selection}
          onSelectionChange={setSelection}
          groupColumn={groupColumn}
          // The view and the search are the question being asked; a refetch of the
          // same question must not move you off the page you were reading.
          resetKey={`${activeViewUuid ?? ""}|${q}`}
          pageSize={pageSize}
          onPageSize={setPageSize}
          can={canItem}
          onEdit={(item) => setRecordDialog({ open: true, item })}
          onShare={(item) => setSharingItemUuid(item.uuid)}
          onDelete={confirmDeleteItem}
        />
      ) : (
        <DataTable
          value={items.data ?? []}
          loading={columns.isLoading || items.isLoading}
          dataKey="uuid"
          // The table's wrapper already scrolls; left alone the table would instead
          // squash itself to whatever width it is given, so a dozen columns become a
          // dozen slivers. A floor of ~10rem a column makes it overflow — and
          // therefore scroll — rather than crush. Below 640px it is not a table at
          // all (see RecordCards); this is what carries a tablet, where a table still
          // makes sense but the columns may not fit. On a desktop the floor is under
          // the viewport, so nothing changes.
          tableStyle={{ minWidth: `${visibleCols.length * 10 + 12}rem` }}
          selectionMode="multiple"
          selection={selection}
          onSelectionChange={(e) => setSelection(e.value)}
          stripedRows
          paginator
          rows={pageSize}
          rowsPerPageOptions={[10, 25, 50, 100]}
          onPage={(e) => setPageSize(e.rows)}
          sortMode="single"
          sortField={groupColumn ? `data.${groupBy}` : sortField}
          sortOrder={groupColumn ? 1 : sortOrder}
          onSort={(e: DataTableSortEvent) => {
            if (groupColumn) return; // sort is pinned to the group column
            setSortField(e.sortField);
            setSortOrder(e.sortOrder);
          }}
          rowGroupMode={groupColumn ? "subheader" : undefined}
          groupRowsBy={groupColumn ? `data.${groupBy}` : undefined}
          rowGroupHeaderTemplate={groupColumn ? groupHeader : undefined}
          emptyMessage="No records yet."
        >
          <DataColumn
            selectionMode="multiple"
            headerStyle={{ width: "3rem" }}
            exportable={false}
          />
          {visibleCols.map((col) => (
            <DataColumn
              key={col.uuid}
              field={`data.${col.uuid}`}
              sortable={!groupColumn}
              // Without this the grid sorts with PrimeReact's comparator and the
              // cards with ours, and the same records come out in different orders
              // depending on the screen: it floats blanks to the top on a descending
              // sort, and puts "item10" before "item9".
              sortFunction={(e) =>
                sortRows(e.data as ListItem[], e.field, e.order)
              }
              // Deleting a column moved into List settings: a trash button on every
              // header crowded out the thing the grid is for.
              header={col.display_name}
              body={(row: ListItem) =>
                renderCell(col, row, listUuid, principalUuids, lookupUuids)
              }
            />
          ))}
          <DataColumn
            header=""
            style={{ width: "9rem" }}
            // RowActions stops click propagation itself, so an action never also
            // toggles the row's selection.
            body={(row: ListItem) => (
              <RowActions
                size="small"
                actions={[
                  {
                    key: "edit",
                    icon: "pi pi-pencil",
                    label: "Edit record",
                    visible: canItem(row.uuid, "edit"),
                    onClick: () => setRecordDialog({ open: true, item: row }),
                  },
                  {
                    key: "share",
                    icon: "pi pi-share-alt",
                    label: "Share record",
                    visible: canItem(row.uuid, "share"),
                    onClick: () => setSharingItemUuid(row.uuid),
                  },
                  {
                    key: "delete",
                    icon: "pi pi-trash",
                    label: "Delete record",
                    severity: "danger",
                    visible: canItem(row.uuid, "delete"),
                    onClick: () => confirmDeleteItem(row),
                  },
                ]}
              />
            )}
          />
        </DataTable>
      )}

      {list.data && (
        <ListSettingsDialog
          listUuid={listUuid}
          listName={list.data.display_name}
          workspaceUuid={list.data.workspace.uuid ?? null}
          columns={cols}
          forms={formList}
          views={views.data ?? []}
          activeViewUuid={activeViewUuid}
          onSelectView={setActiveViewUuid}
          currentConfig={currentConfig}
          viewControls={viewControls}
          canConfigure={listActions.has("configure")}
          canShare={listActions.has("share")}
          inherit={{
            value: list.data.permissions_inherit,
            onChange: (value) => setListInherit.mutate(value),
            busy: setListInherit.isPending,
          }}
          visible={showSettings}
          onHide={() => setShowSettings(false)}
        />
      )}
      <RecordFormDialog
        listUuid={listUuid}
        columns={cols}
        form={recordForm}
        item={recordDialog.item}
        prefill={recordDialog.prefill}
        notes={recordDialog.notes}
        visible={recordDialog.open}
        onHide={() => setRecordDialog({ open: false, item: null })}
      />
      <Suspense fallback={null}>
        {qrMode === "fill" && (
          <QrScanDialog
            header="Fill using QR code"
            hint="The code's values fill the Add record form, where you can check them before adding the record."
            busy={qrBusy}
            error={qrError}
            onScan={handleQrScan}
            onHide={() => setQrMode(null)}
          />
        )}
        {qrMode === "submit" && (
          <QrSubmitSession
            listUuid={listUuid}
            columns={cols}
            fields={createFields}
            presets={qrPresets}
            onPresetsChange={setQrPresets}
            onHide={() => setQrMode(null)}
          />
        )}
      </Suspense>
      <ImportExportDialog
        listUuid={listUuid}
        listName={list.data?.name ?? "records"}
        columns={cols}
        canImport={listActions.has("create") && listActions.has("edit")}
        visible={showTransfer}
        onHide={() => setShowTransfer(false)}
      />
      <DeletedRecordsDialog
        listUuid={listUuid}
        columns={cols}
        visible={showDeleted}
        onHide={() => setShowDeleted(false)}
      />
      {activeViewUuid && (
        <FilterDialog
          listUuid={listUuid}
          viewUuid={activeViewUuid}
          columns={cols}
          baseConfig={currentConfig}
          visible={showFilter}
          onHide={() => setShowFilter(false)}
        />
      )}
      {sharingItem && (
        <ShareDialog
          visible={!!sharingItem}
          onHide={() => setSharingItemUuid(null)}
          resourceType="item"
          resourceUuid={sharingItem.uuid}
          resourceLabel="Record"
          inherit={{
            value: sharingItem.permissions_inherit,
            onChange: (value) =>
              setItemInherit.mutate({ uuid: sharingItem.uuid, value }),
            busy: setItemInherit.isPending,
          }}
        />
      )}
    </div>
  );
}
