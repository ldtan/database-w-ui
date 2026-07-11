import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DataTable, type DataTableSortEvent } from "primereact/datatable";
import { Column as DataColumn } from "primereact/column";
import { Button } from "primereact/button";
import { Dropdown } from "primereact/dropdown";
import { Checkbox } from "primereact/checkbox";
import { OverlayPanel } from "primereact/overlaypanel";
import { ConfirmDialog, confirmDialog } from "primereact/confirmdialog";

import { api } from "../../lib/api";
import type {
  Column,
  Form,
  ListItem,
  ListRead,
  View,
  ViewConfig,
} from "../../lib/types";
import { AddColumnDialog } from "../columns/AddColumnDialog";
import { RecordFormDialog } from "../records/RecordFormDialog";
import { FormsPanel } from "../forms/FormsPanel";
import { LookupCell } from "../lookups/LookupControls";
import { ViewBar } from "../views/ViewBar";
import { FilterDialog } from "../views/FilterDialog";

type SortOrder = 1 | 0 | -1 | null | undefined;

function renderCell(column: Column, row: ListItem) {
  const value = row.data?.[column.uuid];
  if (column.type === "lookup") {
    return <LookupCell column={column} value={value} />;
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
  return String(value);
}

export function ListDetailPage() {
  const { listUuid = "" } = useParams();
  const queryClient = useQueryClient();
  const [showAddColumn, setShowAddColumn] = useState(false);
  const [showForms, setShowForms] = useState(false);
  const [recordDialog, setRecordDialog] = useState<{
    open: boolean;
    item: ListItem | null;
  }>({ open: false, item: null });

  const [activeViewUuid, setActiveViewUuid] = useState<string | null>(null);
  const [sortField, setSortField] = useState<string | undefined>(undefined);
  const [sortOrder, setSortOrder] = useState<SortOrder>(undefined);
  const [pageSize, setPageSize] = useState(25);
  const [showFilter, setShowFilter] = useState(false);
  const [groupBy, setGroupBy] = useState<string | null>(null);
  const [hiddenColumns, setHiddenColumns] = useState<string[]>([]);

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
  }, [activeViewUuid, views.data]);

  const deleteItem = useMutation({
    mutationFn: (uuid: string) => api.del(`/lists/${listUuid}/items/${uuid}`),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] }),
  });
  const deleteColumn = useMutation({
    mutationFn: (uuid: string) => api.del(`/lists/${listUuid}/columns/${uuid}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["columns", listUuid] });
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
    },
  });

  const cols = columns.data ?? [];
  const visibleCols = cols.filter((c) => !hiddenColumns.includes(c.uuid));
  const formList = forms.data ?? [];
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
  };

  const columnsPanel = useRef<OverlayPanel>(null);
  function toggleColumn(uuid: string, visible: boolean) {
    setHiddenColumns((prev) =>
      visible ? prev.filter((u) => u !== uuid) : [...new Set([...prev, uuid])],
    );
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
        <Link to="/" className="no-underline text-sm text-gray-500">
          ← Workspaces
        </Link>
        <div className="flex gap-2">
          <Button
            label="Add column"
            icon="pi pi-plus"
            outlined
            onClick={() => setShowAddColumn(true)}
          />
          <Button
            label="Forms"
            icon="pi pi-file-edit"
            outlined
            onClick={() => setShowForms(true)}
            disabled={formList.length === 0}
          />
          <Button
            label="Add record"
            icon="pi pi-plus"
            onClick={() => setRecordDialog({ open: true, item: null })}
            disabled={cols.length === 0}
          />
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
          <Button
            label={filterCount > 0 ? `Filter (${filterCount})` : "Filter"}
            icon="pi pi-filter"
            text
            onClick={() => setShowFilter(true)}
            disabled={!activeViewUuid || cols.length === 0}
          />
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
            <div className="flex flex-col gap-2">
              {cols.map((col) => (
                <div key={col.uuid} className="flex items-center gap-2">
                  <Checkbox
                    inputId={`vis-${col.uuid}`}
                    checked={!hiddenColumns.includes(col.uuid)}
                    onChange={(e) => toggleColumn(col.uuid, !!e.checked)}
                  />
                  <label htmlFor={`vis-${col.uuid}`}>{col.display_name}</label>
                </div>
              ))}
            </div>
          </OverlayPanel>
        </div>
      )}

      {cols.length === 0 && !columns.isLoading && (
        <p>This list has no columns yet. Add one to get started.</p>
      )}

      <DataTable
        value={items.data ?? []}
        loading={columns.isLoading || items.isLoading}
        dataKey="uuid"
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
        {visibleCols.map((col) => (
          <DataColumn
            key={col.uuid}
            field={`data.${col.uuid}`}
            sortable={!groupColumn}
            header={
              <div className="flex items-center gap-2">
                <span>{col.display_name}</span>
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
              </div>
            }
            body={(row: ListItem) => renderCell(col, row)}
          />
        ))}
        <DataColumn
          header=""
          style={{ width: "6rem" }}
          body={(row: ListItem) => (
            <div className="flex gap-1">
              <Button
                icon="pi pi-pencil"
                text
                size="small"
                aria-label="Edit record"
                onClick={() => setRecordDialog({ open: true, item: row })}
              />
              <Button
                icon="pi pi-trash"
                text
                severity="danger"
                size="small"
                aria-label="Delete record"
                onClick={() => confirmDeleteItem(row)}
              />
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
    </div>
  );
}
