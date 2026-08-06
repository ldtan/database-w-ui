import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "primereact/button";
import { Message } from "primereact/message";
import { confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { COLUMN_TYPE_OPTIONS } from "../../lib/columnTypes";
import { useToast } from "../../lib/toast";
import type {
  Column,
  ColumnType,
  ConversionReport,
  RecomputeReport,
} from "../../lib/types";
import { DeletedConfigDialog } from "../lists/DeletedConfigDialog";
import { compareValues } from "../records/sortRows";
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
  // A conversion is two dialogs: pick the new type, then read the report and
  // decide. `converting` drives the first, `review` the second — and the second
  // needs the type that was chosen, which the report itself does not carry.
  const [converting, setConverting] = useState<Column | null>(null);
  const [duplicating, setDuplicating] = useState<Column | null>(null);
  const [review, setReview] = useState<{
    column: Column;
    newType: ColumnType;
    report: ConversionReport;
  } | null>(null);
  const [showDeleted, setShowDeleted] = useState(false);

  // Alphabetical, because this panel is an *inventory* — you come here to find a
  // named field among fifty and change it. The API returns `position, id` order,
  // which is the list's default *display* order and is what the grid and the form
  // editor want; here it degrades to creation order, since nothing in the UI ever
  // writes `position` (known-gaps.md), and hunting through it by eye is the problem.
  // Sorting only what is shown: the grid's order is not this component's business.
  //
  // `compareValues` rather than a bare localeCompare, so "Item 2" precedes
  // "Item 10" here exactly as it does when the records themselves are sorted.
  const sorted = useMemo(
    () =>
      [...columns].sort((a, b) =>
        compareValues(a.display_name, b.display_name, 1),
      ),
    [columns],
  );

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

  // Rebuilding the stored index behind the calculated columns. Deliberately *not*
  // presented as fixing wrong values, because it never is: a calculated cell is
  // recomputed on every read, so the grid and the export are already right. What
  // falls behind is the copy Postgres sorts and filters over, and only a schema
  // change can do it — editing a record re-indexes that record for free.
  const recompute = useMutation({
    mutationFn: () =>
      api.post<RecomputeReport>(`/lists/${listUuid}/columns/recompute`),
    onSuccess: (report) => {
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      if (report.deferred) {
        toast.success(
          "Refreshing in the background",
          `${report.records.toLocaleString()} records. Sorting and filtering will ` +
            "catch up shortly; the values on screen are already correct.",
        );
      } else {
        toast.success(
          "Sorting and filtering are up to date",
          `${(report.changed ?? 0).toLocaleString()} of ` +
            `${report.records.toLocaleString()} records re-indexed.`,
        );
      }
    },
    // 409 is an expected answer, not a fault: someone else's refresh — or the
    // other tab's — is already doing this list. Say so plainly.
    onError: (err) =>
      toast.error(
        err instanceof ApiError && err.status === 409
          ? "A refresh is already running for this list"
          : "Could not refresh",
        err instanceof ApiError && err.status !== 409 ? err.message : undefined,
      ),
  });

  function confirmRecompute() {
    confirmDialog({
      message:
        "Rebuild sorting and filtering for this list's calculated columns? " +
        "The values shown in the grid are always up to date — this brings the " +
        "stored copy that sorting, filtering and search use into line with the " +
        "current formulas. It reads every record, so a large list takes a while.",
      header: "Refresh calculated columns",
      icon: "pi pi-refresh",
      acceptLabel: "Refresh",
      accept: () => recompute.mutate(),
    });
  }

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

      {sorted.map((column) => (
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
              {/* Separate from Edit because it is a different kind of change: a
                  conversion rewrites every record's value and cannot be undone by
                  editing back. A calculated column is out because gaining or losing
                  a formula is a decision about the column, not a cell conversion. */}
              {column.type !== "calculated" && (
                <>
                  <Button
                    icon="pi pi-sync"
                    text
                    size="small"
                    aria-label={`Convert column ${column.display_name} to another type`}
                    tooltip="Change type"
                    onClick={() => setConverting(column)}
                  />
                  {/* Same engine as Change type, opposite ending: this one adds a
                      column and leaves the original alone. Out for a calculated
                      column for the same reason as above. */}
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
          {/* One button for the list, not one per column, because the columns are
              not independent: a formula may read another calculated column, so the
              chain is rebuilt as a whole and re-indexing half of it would mean
              nothing. Offered only when there is a formula to re-index.

              `loading` doubles as the guard against a double click — the server's
              409 is the backstop, not the interaction. */}
          {columns.some((c) => c.type === "calculated") && (
            <Button
              label="Refresh calculated"
              icon="pi pi-refresh"
              text
              size="small"
              loading={recompute.isPending}
              disabled={recompute.isPending}
              onClick={confirmRecompute}
            />
          )}
        </div>
      )}

      <ColumnDialog
        listUuid={listUuid}
        workspaceUuid={workspaceUuid}
        column={editing.column}
        visible={editing.open}
        onHide={() => setEditing({ open: false, column: null })}
        // Offered rather than done, because a formula edit is rarely alone: someone
        // fixing three of them should re-index once at the end, not three times.
        onFormulaChanged={() =>
          confirmDialog({
            message:
              "The new values are already showing. Sorting, filtering and search " +
              "still use the previous formula until the stored copy is rebuilt — " +
              "do that now? It reads every record.",
            header: "Formula changed",
            icon: "pi pi-refresh",
            acceptLabel: "Refresh now",
            rejectLabel: "Later",
            accept: () => recompute.mutate(),
          })
        }
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
