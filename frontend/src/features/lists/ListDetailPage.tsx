import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DataTable, type DataTableSortEvent } from "primereact/datatable";
import { Column as DataColumn } from "primereact/column";
import { Button } from "primereact/button";
import { Dropdown } from "primereact/dropdown";
import { Checkbox } from "primereact/checkbox";
import { OverlayPanel } from "primereact/overlaypanel";
import { Message } from "primereact/message";
import { ConfirmDialog, confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { linkLabel, safeHref } from "../../lib/links";
import { displayTemporal, isTemporal } from "../../lib/temporal";
import { useToast } from "../../lib/toast";
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
import { AddColumnDialog } from "../columns/AddColumnDialog";
import { RecordFormDialog } from "../records/RecordFormDialog";
import { FormsPanel } from "../forms/FormsPanel";
import { ImportExportDialog } from "../transfer/ImportExportDialog";
import { LookupCell } from "../lookups/LookupControls";
import { PrincipalCell } from "../principals/PrincipalControls";
import { ViewBar } from "../views/ViewBar";
import { FilterDialog } from "../views/FilterDialog";
import { ShareDialog } from "../permissions/ShareDialog";
import {
  useEffectiveActions,
  useEffectiveActionsMap,
} from "../permissions/useEffectiveActions";

type SortOrder = 1 | 0 | -1 | null | undefined;

function renderCell(column: Column, row: ListItem) {
  const value = row.data?.[column.uuid];
  if (column.type === "lookup") {
    return <LookupCell column={column} value={value} />;
  }
  if (column.type === "user" || column.type === "group") {
    return <PrincipalCell column={column} value={value} />;
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
  const [showAddColumn, setShowAddColumn] = useState(false);
  const [showForms, setShowForms] = useState(false);
  const [showTransfer, setShowTransfer] = useState(false);
  const [showShareList, setShowShareList] = useState(false);
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
    queryKey: ["items", listUuid, activeViewUuid],
    queryFn: () =>
      api.get<ListItem[]>(
        `/lists/${listUuid}/items/${
          activeViewUuid ? `?view_uuid=${activeViewUuid}` : ""
        }`,
      ),
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
  const deleteColumn = useMutation({
    mutationFn: (uuid: string) => api.del(`/lists/${listUuid}/columns/${uuid}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["columns", listUuid] });
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      toast.success("Column deleted");
    },
    onError: (err) =>
      toast.error(
        "Could not delete column",
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

  const columnsPanel = useRef<OverlayPanel>(null);
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

  function groupHeader(row: ListItem) {
    if (!groupColumn) return null;
    return (
      <span className="font-semibold">
        {groupColumn.display_name}: {renderCell(groupColumn, row)}
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
  function confirmDeleteColumn(col: Column) {
    confirmDialog({
      message: `Delete column "${col.display_name}"? Its values stay in existing records but are hidden.`,
      header: "Confirm",
      icon: "pi pi-exclamation-triangle",
      accept: () => deleteColumn.mutate(col.uuid),
    });
  }

  return (
    <div className="flex flex-col gap-4">
      <ConfirmDialog />

      <div className="flex items-center justify-between">
        <Breadcrumbs items={crumbs} />
        <div className="flex gap-2">
          {listActions.has("configure") && (
            <Button
              label="Add column"
              icon="pi pi-plus"
              outlined
              onClick={() => setShowAddColumn(true)}
            />
          )}
          <Button
            label="Forms"
            icon="pi pi-file-edit"
            outlined
            onClick={() => setShowForms(true)}
            disabled={formList.length === 0}
          />
          <Button
            label="Import / Export"
            icon="pi pi-file-import"
            outlined
            onClick={() => setShowTransfer(true)}
            disabled={cols.length === 0}
          />
          {listActions.has("share") && (
            <Button
              label="Share"
              icon="pi pi-share-alt"
              outlined
              onClick={() => setShowShareList(true)}
              disabled={!list.data}
            />
          )}
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
          {/* Filters are stored on the view, so editing them is a view change. */}
          {!!activeViewUuid && canView(activeViewUuid, "configure") && (
            <Button
              label={filterCount > 0 ? `Filter (${filterCount})` : "Filter"}
              icon="pi pi-filter"
              text
              onClick={() => setShowFilter(true)}
              disabled={cols.length === 0}
            />
          )}
          <Dropdown
            value={groupBy}
            options={cols.map((c) => ({ label: c.display_name, value: c.uuid }))}
            onChange={(e) => setGroupBy(e.value)}
            placeholder="Group by…"
            showClear
            disabled={cols.length === 0}
            className="w-48"
          />
          <Button
            label={
              hiddenColumns.length > 0
                ? `Columns (${visibleCols.length}/${cols.length})`
                : "Columns"
            }
            icon="pi pi-eye"
            text
            onClick={(e) => columnsPanel.current?.toggle(e)}
            disabled={cols.length === 0}
          />
          <OverlayPanel ref={columnsPanel}>
            <div className="flex flex-col gap-1">
              {orderedCols.map((col, i) => (
                <div key={col.uuid} className="flex items-center gap-2">
                  <Button
                    icon="pi pi-chevron-up"
                    text
                    size="small"
                    aria-label="Move up"
                    disabled={i === 0}
                    onClick={() => moveColumn(i, -1)}
                  />
                  <Button
                    icon="pi pi-chevron-down"
                    text
                    size="small"
                    aria-label="Move down"
                    disabled={i === orderedCols.length - 1}
                    onClick={() => moveColumn(i, 1)}
                  />
                  <Checkbox
                    inputId={`vis-${col.uuid}`}
                    checked={!hiddenColumns.includes(col.uuid)}
                    onChange={(e) => toggleColumn(col.uuid, !!e.checked)}
                  />
                  <label htmlFor={`vis-${col.uuid}`} className="flex-1">
                    {col.display_name}
                  </label>
                </div>
              ))}
            </div>
          </OverlayPanel>
        </div>
      )}

      {cols.length === 0 && !columns.isLoading && (
        <Message
          severity="info"
          text={
            listActions.has("configure")
              ? "This list has no columns yet. Use “Add column” to define fields, then add records."
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
            header={
              <div className="flex items-center gap-2">
                <span>{col.display_name}</span>
                {listActions.has("configure") && (
                  <Button
                    icon="pi pi-trash"
                    text
                    severity="danger"
                    size="small"
                    aria-label={`Delete column ${col.display_name}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      confirmDeleteColumn(col);
                    }}
                  />
                )}
              </div>
            }
            body={(row: ListItem) => renderCell(col, row)}
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

      <AddColumnDialog
        listUuid={listUuid}
        workspaceUuid={list.data?.workspace.uuid ?? null}
        visible={showAddColumn}
        onHide={() => setShowAddColumn(false)}
      />
      <RecordFormDialog
        listUuid={listUuid}
        columns={cols}
        form={recordForm}
        item={recordDialog.item}
        visible={recordDialog.open}
        onHide={() => setRecordDialog({ open: false, item: null })}
      />
      <FormsPanel
        listUuid={listUuid}
        columns={cols}
        forms={formList}
        visible={showForms}
        onHide={() => setShowForms(false)}
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
      {showShareList && list.data && (
        <ShareDialog
          visible={showShareList}
          onHide={() => setShowShareList(false)}
          resourceType="list"
          resourceUuid={list.data.uuid}
          resourceLabel={list.data.display_name}
          inherit={{
            value: list.data.permissions_inherit,
            onChange: (value) => setListInherit.mutate(value),
            busy: setListInherit.isPending,
          }}
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
