import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { DataTable } from "primereact/datatable";
import { Column as DataColumn } from "primereact/column";
import { Button } from "primereact/button";

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
  const [showAddColumn, setShowAddColumn] = useState(false);
  const [showAddRecord, setShowAddRecord] = useState(false);

  const columns = useQuery({
    queryKey: ["columns", listUuid],
    queryFn: () => api.get<Column[]>(`/lists/${listUuid}/columns/`),
  });
  const items = useQuery({
    queryKey: ["items", listUuid],
    queryFn: () => api.get<ListItem[]>(`/lists/${listUuid}/items/`),
  });

  const cols = columns.data ?? [];

  return (
    <div className="flex flex-col gap-4">
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
            onClick={() => setShowAddRecord(true)}
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
            header={col.display_name}
            sortable
            body={(row: ListItem) => renderCell(col, row)}
          />
        ))}
      </DataTable>

      <AddColumnDialog
        listUuid={listUuid}
        visible={showAddColumn}
        onHide={() => setShowAddColumn(false)}
      />
      <RecordFormDialog
        listUuid={listUuid}
        columns={cols}
        visible={showAddRecord}
        onHide={() => setShowAddRecord(false)}
      />
    </div>
  );
}
