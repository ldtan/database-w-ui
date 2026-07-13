import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DataTable, type DataTableSortEvent } from "primereact/datatable";
import { Column as DataColumn } from "primereact/column";
import { Button } from "primereact/button";
import { InputText } from "primereact/inputtext";
import { Message } from "primereact/message";
import { ConfirmDialog, confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { linkLabel, safeHref } from "../../lib/links";
import { displayTemporal, isTemporal } from "../../lib/temporal";
import { useToast } from "../../lib/toast";
import { useDebounced } from "../../lib/useDebounced";
import { Breadcrumbs, type Crumb } from "../../components/Breadcrumbs";
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
import { RecordFormDialog } from "../records/RecordFormDialog";
import { ImportExportDialog } from "../transfer/ImportExportDialog";
import { LookupCell, collectLookupUuids } from "../lookups/LookupControls";
import {
  PrincipalCell,
  collectPrincipalUuids,
  type PrincipalType,
} from "../principals/PrincipalControls";
import { ViewBar } from "../views/ViewBar";
import { ViewControls } from "../views/ViewControls";
import { FilterDialog } from "../views/FilterDialog";
import { ShareDialog } from "../permissions/ShareDialog";
import {
  useEffectiveActions,
  useEffectiveActionsMap,
} from "../permissions/useEffectiveActions";

type SortOrder = 1 | 0 | -1 | null | undefined;

function renderCell(
  column: Column,
  row: ListItem,
  listUuid: string,
  principalUuids: Record<PrincipalType, string[]>,
  lookupUuids: Map<string, string[]>,
) {
  const value = row.data?.[column.uuid];
  // Reference cells are handed the uuids the *whole page* holds, and resolve them
  // themselves. They share one query key per column, so it is still one request —
  // but each cell subscribes to it, which a map passed down as a prop cannot do:
  // DataTable re-renders only when its `value` changes, so the labels would land
  // after an edit and never reach the DOM.
  if (column.type === "lookup") {
    return (
      <LookupCell
        listUuid={listUuid}
        column={column}
        value={value}
        pageUuids={lookupUuids.get(column.uuid) ?? []}
      />
    );
  }
  if (column.type === "user" || column.type === "group") {
    return (
      <PrincipalCell
        column={column}
        value={value}
        pageUuids={principalUuids[column.type]}
      />
    );
  }
  if (value === null || value === undefined || value === "") {
    return <span className="text-gray-300">—</span>;
  }
  if (column.type === "checkbox") {
    return value ? "✓" : "✗";
  }
  if (column.type === "choice") {
    const option = column.config.options?.find((o) => o.id === value);
    return option?.label ?? String(value);
  }
  if (column.type === "link") {
    const href = safeHref(column, value);
    // A value the scheme check rejects is shown as plain text, never an anchor.
    if (!href) return String(value);
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="hover:underline"
        style={{ color: "var(--brand)" }} // themed per palette; no Tailwind token for it
        onClick={(e) => e.stopPropagation()} // don't also toggle row selection
      >
        {linkLabel(column, href)}
      </a>
    );
  }
  // Otherwise a datetime renders as its raw stored string, seconds and all.
  if (isTemporal(column.type)) {
    return displayTemporal(column.type, value);
  }
  return String(value);
}

export function ListDetailPage() {
  const { listUuid = "" } = useParams();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [showSettings, setShowSettings] = useState(false);
  const [showTransfer, setShowTransfer] = useState(false);
  const [sharingItemUuid, setSharingItemUuid] = useState<string | null>(null);
  const [recordDialog, setRecordDialog] = useState<{
    open: boolean;
    item: ListItem | null;
  }>({ open: false, item: null });
  const [selection, setSelection] = useState<ListItem[]>([]);

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
  const items = useQuery({
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
  const visibleCols = orderedCols.filter((c) => !hiddenColumns.includes(c.uuid));

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
    formList.find((f) => f.type === (recordDialog.item ? "update" : "create")) ??
    null;
  const activeView = views.data?.find((v) => v.uuid === activeViewUuid) ?? null;
  const filterCount = activeView?.config.filters?.conditions?.length ?? 0;
  const groupColumn = groupBy ? cols.find((c) => c.uuid === groupBy) ?? null : null;
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
    filters: activeView?.config.filters ?? { conjunction: "and", conditions: [] },
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

  function groupHeader(row: ListItem) {
    if (!groupColumn) return null;
    return (
      <span className="font-semibold">
        {groupColumn.display_name}: {renderCell(groupColumn, row, listUuid, principalUuids, lookupUuids)}
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

      <div className="flex items-center justify-between">
        <Breadcrumbs items={crumbs} />
        <div className="flex gap-2">
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
          {listActions.has("create") && (
            <Button
              label="Add record"
              icon="pi pi-plus"
              onClick={() => setRecordDialog({ open: true, item: null })}
              disabled={cols.length === 0}
            />
          )}
        </div>
      </div>

      {views.data && views.data.length > 0 && (
        <div className="flex items-center gap-2">
          <ViewBar
            listUuid={listUuid}
            views={views.data}
            activeUuid={activeViewUuid}
            onSelect={setActiveViewUuid}
            currentConfig={currentConfig}
          />
          <ViewControls {...viewControls} />
          {/* pl-8 by hand: the icon is positioned over the input, and the theme's
              reset does not give p-input-icon-left the padding to clear it. */}
          <span className="p-input-icon-left ml-auto">
            <i className="pi pi-search" />
            <InputText
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search records…"
              disabled={cols.length === 0}
              className="w-64 pl-8"
            />
          </span>
        </div>
      )}

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

      <DataTable
        value={items.data ?? []}
        loading={columns.isLoading || items.isLoading}
        dataKey="uuid"
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
            // Deleting a column moved into List settings: a trash button on every
            // header crowded out the thing the grid is for.
            header={col.display_name}
            body={(row: ListItem) => renderCell(col, row, listUuid, principalUuids, lookupUuids)}
          />
        ))}
        <DataColumn
          header=""
          style={{ width: "9rem" }}
          body={(row: ListItem) => (
            <div className="flex gap-1">
              {canItem(row.uuid, "edit") && (
                <Button
                  icon="pi pi-pencil"
                  text
                  size="small"
                  aria-label="Edit record"
                  onClick={(e) => {
                    e.stopPropagation(); // don't also toggle row selection
                    setRecordDialog({ open: true, item: row });
                  }}
                />
              )}
              {canItem(row.uuid, "share") && (
                <Button
                  icon="pi pi-share-alt"
                  text
                  size="small"
                  aria-label="Share record"
                  onClick={(e) => {
                    e.stopPropagation();
                    setSharingItemUuid(row.uuid);
                  }}
                />
              )}
              {canItem(row.uuid, "delete") && (
                <Button
                  icon="pi pi-trash"
                  text
                  severity="danger"
                  size="small"
                  aria-label="Delete record"
                  onClick={(e) => {
                    e.stopPropagation();
                    confirmDeleteItem(row);
                  }}
                />
              )}
            </div>
          )}
        />
      </DataTable>

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
        visible={recordDialog.open}
        onHide={() => setRecordDialog({ open: false, item: null })}
      />
      <ImportExportDialog
        listUuid={listUuid}
        listName={list.data?.name ?? "records"}
        columns={cols}
        canImport={listActions.has("create") && listActions.has("edit")}
        visible={showTransfer}
        onHide={() => setShowTransfer(false)}
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
