import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "primereact/button";
import { Message } from "primereact/message";
import { confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { useToast } from "../../lib/toast";
import type { Metric } from "../../lib/types";
import { MetricDialog } from "./MetricDialog";
import { metricsKey } from "./metricKeys";

interface Props {
  listUuid: string;
  metrics: Metric[];
  canConfigure: boolean;
}

/** The list's metrics: add, modify, delete. Beside Columns and Views in list
 *  settings, because a metric is part of how the list is set up rather than
 *  something you change while reading it. */
export function MetricsPanel({ listUuid, metrics, canConfigure }: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [editing, setEditing] = useState<{ open: boolean; metric: Metric | null }>({
    open: false,
    metric: null,
  });

  const remove = useMutation({
    mutationFn: (uuid: string) => api.del(`/lists/${listUuid}/metrics/${uuid}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: metricsKey(listUuid) });
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      toast.success("Metric deleted");
    },
    onError: (err) =>
      toast.error(
        "Could not delete metric",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  function confirmDelete(metric: Metric) {
    confirmDialog({
      message: `Delete metric “${metric.display_name}”? It holds no data of its own — the records it counts are untouched.`,
      header: "Confirm",
      icon: "pi pi-exclamation-triangle",
      accept: () => remove.mutate(metric.uuid),
    });
  }

  return (
    <div className="flex flex-col gap-3">
      {metrics.length === 0 && (
        <Message
          severity="info"
          text={
            canConfigure
              ? "No metrics yet. Add one to show a total, average or count above the records."
              : "This list has no metrics yet."
          }
        />
      )}

      {metrics.map((metric) => (
        <div
          key={metric.uuid}
          className="flex items-center gap-3 border-b pb-2"
          style={{ borderColor: "var(--border)" }}
        >
          <div className="flex-1 min-w-0">
            <div className="truncate">{metric.display_name}</div>
            <small style={{ color: "var(--text-muted)", fontFamily: "monospace" }}>
              {metric.config.formula}
            </small>
          </div>
          {canConfigure && (
            <>
              <Button
                icon="pi pi-pencil"
                text
                size="small"
                aria-label={`Edit metric ${metric.display_name}`}
                onClick={() => setEditing({ open: true, metric })}
              />
              <Button
                icon="pi pi-trash"
                text
                severity="danger"
                size="small"
                aria-label={`Delete metric ${metric.display_name}`}
                onClick={() => confirmDelete(metric)}
              />
            </>
          )}
        </div>
      ))}

      {canConfigure && (
        <div>
          <Button
            label="Add metric"
            icon="pi pi-plus"
            outlined
            size="small"
            onClick={() => setEditing({ open: true, metric: null })}
          />
        </div>
      )}

      <MetricDialog
        listUuid={listUuid}
        metric={editing.metric}
        visible={editing.open}
        onHide={() => setEditing({ open: false, metric: null })}
      />
    </div>
  );
}
