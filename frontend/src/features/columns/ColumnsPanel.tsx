import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "primereact/button";
import { Message } from "primereact/message";
import { confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { COLUMN_TYPE_OPTIONS } from "../../lib/columnTypes";
import { useToast } from "../../lib/toast";
import type { Column } from "../../lib/types";
import { ColumnDialog } from "./ColumnDialog";

interface Props {
  listUuid: string;
  workspaceUuid: string | null;
  columns: Column[];
  canConfigure: boolean;
}

const TYPE_LABELS = new Map(COLUMN_TYPE_OPTIONS.map((o) => [o.value, o.label]));

/** The list's fields: add, modify, delete. Lives in List settings rather than the
 *  grid header, where a delete button per column crowded out the data. */
export function ColumnsPanel({
  listUuid,
  workspaceUuid,
  columns,
  canConfigure,
}: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [editing, setEditing] = useState<{ open: boolean; column: Column | null }>({
    open: false,
    column: null,
  });

  const remove = useMutation({
    mutationFn: (uuid: string) => api.del(`/lists/${listUuid}/columns/${uuid}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["columns", listUuid] });
      toast.success("Column deleted");
    },
    onError: (err) =>
      toast.error(
        "Could not delete column",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  function confirmDelete(column: Column) {
    confirmDialog({
      message: `Delete column “${column.display_name}”? Its values stay in existing records but are hidden.`,
      header: "Confirm",
      icon: "pi pi-exclamation-triangle",
      accept: () => remove.mutate(column.uuid),
    });
  }

  return (
    <div className="flex flex-col gap-3">
      {columns.length === 0 && (
        <Message
          severity="info"
          text={
            canConfigure
              ? "No columns yet. Add one to define what a record holds."
              : "This list has no columns yet."
          }
        />
      )}

      {columns.map((column) => (
        <div
          key={column.uuid}
          className="flex items-center gap-3 border-b pb-2"
          style={{ borderColor: "var(--border)" }}
        >
          <div className="flex-1 min-w-0">
            <div className="truncate">{column.display_name}</div>
            <small style={{ color: "var(--text-muted)" }}>
              {TYPE_LABELS.get(column.type) ?? column.type}
              {column.config?.required ? " · required" : ""}
            </small>
          </div>
          {canConfigure && (
            <>
              <Button
                icon="pi pi-pencil"
                text
                size="small"
                aria-label={`Edit column ${column.display_name}`}
                onClick={() => setEditing({ open: true, column })}
              />
              <Button
                icon="pi pi-trash"
                text
                severity="danger"
                size="small"
                aria-label={`Delete column ${column.display_name}`}
                onClick={() => confirmDelete(column)}
              />
            </>
          )}
        </div>
      ))}

      {canConfigure && (
        <div>
          <Button
            label="Add column"
            icon="pi pi-plus"
            outlined
            size="small"
            onClick={() => setEditing({ open: true, column: null })}
          />
        </div>
      )}

      <ColumnDialog
        listUuid={listUuid}
        workspaceUuid={workspaceUuid}
        column={editing.column}
        visible={editing.open}
        onHide={() => setEditing({ open: false, column: null })}
      />
    </div>
  );
}
