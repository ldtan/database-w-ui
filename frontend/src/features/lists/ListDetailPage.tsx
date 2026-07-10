import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DataTable } from "primereact/datatable";
import { Column as DataColumn } from "primereact/column";
import { Button } from "primereact/button";
import { ConfirmDialog, confirmDialog } from "primereact/confirmdialog";

import { api } from "../../lib/api";
import type { Column, ListItem } from "../../lib/types";
import { AddColumnDialog } from "../columns/AddColumnDialog";
import { RecordFormDialog } from "../records/RecordFormDialog";

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
  const [recordDialog, setRecordDialog] = useState<{
    open: boolean;
    item: ListItem | null;
  }>({ open: false, item: null });

  const columns = useQuery({
    queryKey: ["columns", listUuid],
    queryFn: () => api.get<Column[]>(`/lists/${listUuid}/columns/`),
  });
  const items = useQuery({
    queryKey: ["items", listUuid],
    queryFn: () => api.get<ListItem[]>(`/lists/${listUuid}/items/`),
  });

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
            label="Add record"
            icon="pi pi-plus"
            onClick={() => setRecordDialog({ open: true, item: null })}
            disabled={cols.length === 0}
          />
        </div>
      </div>

      {cols.length === 0 && !columns.isLoading && (
        <p>This list has no columns yet. Add one to get started.</p>
      )}

      <DataTable
        value={items.data ?? []}
        loading={columns.isLoading || items.isLoading}
        dataKey="uuid"
        stripedRows
        paginator
        rows={25}
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
        item={recordDialog.item}
        visible={recordDialog.open}
        onHide={() => setRecordDialog({ open: false, item: null })}
      />
    </div>
  );
}
