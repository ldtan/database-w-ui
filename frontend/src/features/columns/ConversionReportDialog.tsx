import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "primereact/button";
import { Message } from "primereact/message";
import { confirmDialog } from "primereact/confirmdialog";

import { AppDialog } from "../../components/AppDialog";
import { api, ApiError } from "../../lib/api";
import { COLUMN_TYPE_OPTIONS } from "../../lib/columnTypes";
import { useToast } from "../../lib/toast";
import type { Column, ColumnType, ConversionReport } from "../../lib/types";

const TYPE_LABELS = new Map(COLUMN_TYPE_OPTIONS.map((o) => [o.value, o.label]));

interface Props {
  listUuid: string;
  column: Column;
  newType: ColumnType;
  report: ConversionReport;
  onDone: () => void;
}

/** Step two: read what would happen, then ask for it or walk away.
 *
 *  **No work has run at this point.** The report is a dry run — the server converted
 *  every cell in memory to count them and wrote nothing, created no column and
 *  reserved no name. So cancelling is free in the strongest sense: there is no
 *  request to make, nothing to fail while making it, and nothing left behind to
 *  block the next attempt. Pressing Apply is what does the whole job, in one
 *  transaction.
 *
 *  That is also why the mutation re-sends the entire request rather than posting to
 *  some handle the report handed out. The two calls are deliberately unlinked: a
 *  server-side staging area is exactly what used to let a record written between
 *  them be reverted or blanked.
 *
 *  Two endings, and the wording has to be honest about which: a **conversion**
 *  replaces the column and puts the old values in the bin, while a **duplicate**
 *  adds a column and displaces nothing. `report.duplicate` says which.
 *
 *  The counts can go stale while this sits open — somebody else may be editing —
 *  and that is accepted rather than papered over. The apply re-derives everything
 *  from the records as they are, and refuses with a 409 if they moved underneath it,
 *  which surfaces here as an ordinary error message. */
export function ConversionReportDialog({
  listUuid,
  column,
  newType,
  report,
  onDone,
}: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const typeLabel = TYPE_LABELS.get(newType) ?? newType;
  const duplicate = report.duplicate;

  const apply = useMutation({
    // The apply carries the whole request again, because there is nothing on the
    // server tying it to the preview — no staged column, no handle, nothing that
    // could hold a value written since. The `config` is the one the preview
    // *resolved*, not the one the dialog sent: for text -> choice the options are
    // minted from the source's distinct values, and letting the server re-derive
    // them here would answer a slightly different question than the one on screen.
    mutationFn: () =>
      api.post<Column>(
        `/lists/${listUuid}/columns/${column.uuid}/convert/apply`,
        {
          type: report.new_type,
          config: report.config,
          name: report.name,
          display_name: report.display_name,
        },
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["columns", listUuid] });
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      // The cutover rewrites every record's value for this column, and the labels
      // a lookup/user cell renders are keyed on the values it holds.
      queryClient.invalidateQueries({ queryKey: ["lookup-labels"] });
      queryClient.invalidateQueries({ queryKey: ["principal-labels"] });
      toast.success(
        duplicate ? "Column duplicated" : "Column converted",
        duplicate
          ? `Copied from “${column.display_name}”.`
          : `“${column.display_name}” is now ${typeLabel}.`,
      );
      onDone();
    },
  });

  // Nothing to discard. The preview created no column and wrote no value, so
  // walking away is closing the dialog — there is no request to make and nothing
  // that could fail while making it. This used to be a `DELETE` whose failure left
  // a hidden column the person could neither see nor clean up.
  const busy = apply.isPending;

  // A duplicate displaces nothing, so it does not need the same warning — it adds
  // a column and leaves everything else alone. Only the conversion is destructive.
  const blank =
    report.failed > 0
      ? `${report.failed.toLocaleString()} value(s) could not be converted and will be left empty. `
      : "";

  function confirmApply() {
    if (duplicate && report.failed === 0) {
      apply.mutate();
      return;
    }
    confirmDialog({
      header: duplicate ? "Add the copy?" : `Convert to ${typeLabel}?`,
      icon: "pi pi-exclamation-triangle",
      message: duplicate
        ? `${blank}“${column.display_name}” itself is not changed.`
        : `${blank || `“${column.display_name}” becomes ${typeLabel}. `}The original values are kept in a deleted column named “${column.name}__before_conversion”, which you can restore from Deleted columns.`,
      acceptLabel: duplicate ? "Add column" : "Convert",
      acceptClassName: !duplicate || report.failed > 0 ? "p-button-danger" : undefined,
      rejectLabel: "Cancel",
      accept: () => apply.mutate(),
    });
  }

  return (
    <AppDialog
      header={
        duplicate
          ? `Duplicate “${column.display_name}” as ${typeLabel}`
          : `Convert “${column.display_name}” to ${typeLabel}`
      }
      visible
      onHide={() => {
        if (!busy) onDone();
      }}
      style={{ width: 560 }}
      footer={
        <>
          <Button label="Cancel" text onClick={onDone} disabled={busy} />
          <Button
            label={duplicate ? "Add column" : "Apply"}
            icon="pi pi-check"
            onClick={confirmApply}
            loading={apply.isPending}
          />
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {apply.error instanceof ApiError && (
          <Message severity="error" text={apply.error.message} />
        )}

        <div className="flex gap-6">
          <div>
            <div className="text-2xl">{report.converted.toLocaleString()}</div>
            <small style={{ color: "var(--text-muted)" }}>converted</small>
          </div>
          <div>
            <div
              className="text-2xl"
              style={{ color: report.failed ? "var(--danger)" : undefined }}
            >
              {report.failed.toLocaleString()}
            </div>
            <small style={{ color: "var(--text-muted)" }}>
              could not be converted
            </small>
          </div>
        </div>

        {report.failed === 0 ? (
          <Message
            severity="success"
            text={
              duplicate
                ? "Every value copied. Nothing has changed yet — add the column to keep it."
                : "Every value converted. Nothing has changed yet — apply it to make it so."
            }
          />
        ) : (
          <Message
            severity="warn"
            content={
              <span className="text-sm">
                Values that could not be converted will be <strong>empty</strong>{" "}
                in the new column. Cancel, fix them, and convert again if that is
                not what you want.
              </span>
            }
          />
        )}

        {report.errors.length > 0 && (
          <div className="flex flex-col gap-2">
            <div className="font-medium">What failed</div>
            <div
              className="flex flex-col gap-1 overflow-auto"
              style={{ maxHeight: 220 }}
            >
              {report.errors.map((failure) => (
                <div
                  key={failure.record_uuid}
                  className="text-sm border-b pb-1"
                  style={{ borderColor: "var(--border)" }}
                >
                  {failure.message}
                </div>
              ))}
            </div>
            {report.errors_truncated && (
              <small style={{ color: "var(--text-muted)" }}>
                Showing the first {report.errors.length.toLocaleString()} of{" "}
                {report.failed.toLocaleString()}.
              </small>
            )}
          </div>
        )}
      </div>
    </AppDialog>
  );
}
