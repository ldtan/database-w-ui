import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DataTable, type DataTableSortEvent } from "primereact/datatable";
import { Column as DataColumn } from "primereact/column";
import { Button } from "primereact/button";
import { ConfirmDialog, confirmDialog } from "primereact/confirmdialog";

import { api } from "../../lib/api";
import type { Column, Form, ListItem, View, ViewConfig } from "../../lib/types";
import { AddColumnDialog } from "../columns/AddColumnDialog";
import { RecordFormDialog } from "../records/RecordFormDialog";
import { FormsPanel } from "../forms/FormsPanel";
import { ViewBar } from "../views/ViewBar";
import { FilterDialog } from "../views/FilterDialog";

type SortOrder = 1 | 0 | -1 | null | undefined;

function renderCell(column: Column, row: ListItem) {
  const value = row.data?.[column.uuid];
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
  const formList = forms.data ?? [];
  // Editing uses the "update" form; creating uses the "create" form.
  const recordForm =
    formList.find((f) => f.type === (recordDialog.item ? "update" : "create")) ??
    null;
  const activeView = views.data?.find((v) => v.uuid === activeViewUuid) ?? null;
  const filterCount = activeView?.config.filters?.conditions?.length ?? 0;
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
  };

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
        sortField={sortField}
        sortOrder={sortOrder}
        onSort={(e: DataTableSortEvent) => {
          setSortField(e.sortField);
          setSortOrder(e.sortOrder);
        }}
        emptyMessage="No records yet."
      >
        {cols.map((col) => (
          <DataColumn
            key={col.uuid}
            field={`data.${col.uuid}`}
            sortable
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
