import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "primereact/button";
import { Message } from "primereact/message";
import { confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import {
  COLUMN_CONVERSION_ENABLED,
  COLUMN_TYPE_OPTIONS,
} from "../../lib/columnTypes";
import { useToast } from "../../lib/toast";
import type { Column, ColumnType, ConversionReport } from "../../lib/types";
import { DeletedConfigDialog } from "../lists/DeletedConfigDialog";
import { ColumnDialog } from "./ColumnDialog";
import { ConversionReportDialog } from "./ConversionReportDialog";

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
  // Either ending is two dialogs: pick the new type, then read the report and
  // decide. `converting` drives the first, `review` the second — and the second
  // needs the type that was chosen, which the report itself does not carry.
  // All three are inert while COLUMN_CONVERSION_ENABLED is false, and kept wired
  // up so turning the flag back on is the whole change.
  const [converting, setConverting] = useState<Column | null>(null);
  const [duplicating, setDuplicating] = useState<Column | null>(null);
  const [review, setReview] = useState<{
    column: Column;
    newType: ColumnType;
    report: ConversionReport;
  } | null>(null);
  const [showDeleted, setShowDeleted] = useState(false);

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
            {/* Said here as well as on the grid, because this is where somebody who
                just changed a formula is standing, and where they would otherwise
                change it again on top of a recompute that never finished. */}
            {column.recompute_pending && (
              <small
                className="block"
                style={{ color: "var(--warn, #b45309)" }}
                title="A recompute is running, or one did not finish. Reload in a moment; if it stays, the stored values need repairing."
              >
                <i className="pi pi-exclamation-triangle" /> values catching up
              </small>
            )}
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
              {/* Both are withdrawn, not merely hidden — see
                  COLUMN_CONVERSION_ENABLED. They are separate from Edit because they
                  are a different kind of change: they rewrite values rather than
                  settings. A calculated column's values belong to its formula, so
                  there is no cell for either to act on. */}
              {COLUMN_CONVERSION_ENABLED && column.type !== "calculated" && (
                <>
                  <Button
                    icon="pi pi-sync"
                    text
                    size="small"
                    aria-label={`Convert column ${column.display_name} to another type`}
                    tooltip="Change type"
                    onClick={() => setConverting(column)}
                  />
                  {/* Same engine, opposite ending: this one adds a column and leaves
                      the original alone. A calculated column is copied by writing its
                      formula again, not by copying cells. */}
                  <Button
                    icon="pi pi-copy"
                    text
                    size="small"
                    aria-label={`Duplicate column ${column.display_name}`}
                    tooltip="Duplicate"
                    onClick={() => setDuplicating(column)}
                  />
                </>
              )}
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

      {/* The bin sits with this panel's own actions rather than on a screen of its
          own: a deleted column is looked for where the live ones are listed. It is
          offered whenever the panel is editable at all, because the route is gated
          on the list's `configure` — exactly like adding and deleting here. */}
      {canConfigure && (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            label="Add column"
            icon="pi pi-plus"
            outlined
            size="small"
            onClick={() => setEditing({ open: true, column: null })}
          />
          <Button
            label="Deleted"
            icon="pi pi-trash"
            text
            size="small"
            onClick={() => setShowDeleted(true)}
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
      {/* Keyed on the column so the form resets between conversions rather than
          carrying the previous column's target type into the next one. */}
      {converting && (
        <ColumnDialog
          key={`convert:${converting.uuid}`}
          listUuid={listUuid}
          workspaceUuid={workspaceUuid}
          column={converting}
          visible
          mode="convert"
          onHide={() => setConverting(null)}
          onConverted={(report) =>
            setReview({
              column: converting,
              // The dialog does not report back the type it sent, so read it off
              // the hidden column the conversion just built.
              newType: report.new_type,
              report,
            })
          }
        />
      )}
      {duplicating && (
        <ColumnDialog
          key={`duplicate:${duplicating.uuid}`}
          listUuid={listUuid}
          workspaceUuid={workspaceUuid}
          column={duplicating}
          visible
          mode="duplicate"
          onHide={() => setDuplicating(null)}
          onConverted={(report) =>
            setReview({
              column: duplicating,
              newType: report.new_type,
              report,
            })
          }
        />
      )}
      {review && (
        <ConversionReportDialog
          listUuid={listUuid}
          column={review.column}
          newType={review.newType}
          report={review.report}
          onDone={() => setReview(null)}
        />
      )}
      <DeletedConfigDialog
        listUuid={listUuid}
        kind="columns"
        visible={showDeleted}
        onHide={() => setShowDeleted(false)}
      />
    </div>
  );
}
