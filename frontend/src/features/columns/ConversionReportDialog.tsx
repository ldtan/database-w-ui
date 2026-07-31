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

/** Step two of a conversion: read what happened, then apply it or throw it away.
 *
 *  The conversion has already run at this point — into a hidden column that no
 *  view, filter, form or formula can see — so this screen is the whole reason the
 *  feature works the way it does. Until "Apply" the list is exactly as it was, and
 *  "Discard" costs nothing because nothing ever saw the result.
 *
 *  Closing the dialog any other way discards too. Leaving a conversion in progress
 *  would block the next one on this column with a 409 that names a scratch column
 *  the person never knew existed. */
export function ConversionReportDialog({
  listUuid,
  column,
  newType,
  report,
  onDone,
}: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const base = `/lists/${listUuid}/columns/${column.uuid}/convert/${report.target_uuid}`;
  const typeLabel = TYPE_LABELS.get(newType) ?? newType;

  const apply = useMutation({
    mutationFn: () => api.post<Column>(base, {}),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["columns", listUuid] });
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      // The cutover rewrites every record's value for this column, and the labels
      // a lookup/user cell renders are keyed on the values it holds.
      queryClient.invalidateQueries({ queryKey: ["lookup-labels"] });
      queryClient.invalidateQueries({ queryKey: ["principal-labels"] });
      toast.success(
        "Column converted",
        `“${column.display_name}” is now ${typeLabel}.`,
      );
      onDone();
    },
  });

  const discard = useMutation({
    mutationFn: () => api.del(base),
    onSuccess: onDone,
    // A failed discard leaves a hidden column behind, which the person cannot see
    // or clean up — so say so rather than closing quietly.
    onError: (err) =>
      toast.error(
        "Could not discard the conversion",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  const busy = apply.isPending || discard.isPending;

  function confirmApply() {
    confirmDialog({
      header: `Convert to ${typeLabel}?`,
      icon: "pi pi-exclamation-triangle",
      message:
        report.failed > 0
          ? `${report.failed.toLocaleString()} value(s) could not be converted and will be left empty. The originals are kept in a deleted column named “${column.name}__before_conversion”, which you can restore from Deleted columns.`
          : `“${column.display_name}” becomes ${typeLabel}. The original values are kept in a deleted column named “${column.name}__before_conversion”, which you can restore from Deleted columns.`,
      acceptLabel: "Convert",
      acceptClassName: report.failed > 0 ? "p-button-danger" : undefined,
      rejectLabel: "Cancel",
      accept: () => apply.mutate(),
    });
  }

  return (
    <AppDialog
      header={`Convert “${column.display_name}” to ${typeLabel}`}
      visible
      onHide={() => {
        if (!busy) discard.mutate();
      }}
      style={{ width: 560 }}
      footer={
        <>
          <Button
            label="Discard"
            text
            onClick={() => discard.mutate()}
            loading={discard.isPending}
            disabled={apply.isPending}
          />
          <Button
            label="Apply"
            icon="pi pi-check"
            onClick={confirmApply}
            loading={apply.isPending}
            disabled={discard.isPending}
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
            text="Every value converted. Nothing has changed yet — apply it to make it so."
          />
        ) : (
          <Message
            severity="warn"
            content={
              <span className="text-sm">
                Values that could not be converted will be <strong>empty</strong>{" "}
                in the new column. Discard, fix them, and convert again if that is
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
