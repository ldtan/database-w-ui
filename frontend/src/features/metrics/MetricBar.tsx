import { useQuery } from "@tanstack/react-query";
import { Skeleton } from "primereact/skeleton";

import { api } from "../../lib/api";
import { METRIC_REFRESH_SECONDS } from "../../lib/config";
import { useLiveTable } from "../../lib/useLiveTable";
import type { FilterGroup, Metric, MetricValue, MetricValues } from "../../lib/types";
import { recordScopeParams } from "../views/recordQuery";
import { metricsKey, metricValuesKey, metricValuesPrefix } from "./metricKeys";

interface Props {
  tableUuid: string;
  metrics: Metric[];
  /** The view, search and filter the grid is showing, so the cards describe the
   *  same records. All three are part of the query key: a different question is a
   *  different answer, not a stale one. */
  viewUuid: string | null;
  q: string;
  filters: FilterGroup;
  /** Whether the strip is revealed. Off by default (see TableDetailPage), and it
   *  gates the query as well as the markup — a metric is a scan of the whole
   *  table, re-run on the live interval, so a hidden strip must cost nothing
   *  rather than merely look like it does. */
  visible: boolean;
}

/** A number as its metric asks for it: fixed decimals, then the prefix/suffix. */
function format(value: MetricValue["value"], metric: Metric): string {
  if (value === null || value === undefined) return "—";
  const { precision, prefix, suffix } = metric.config;
  const body =
    typeof value === "number" && precision !== null && precision !== undefined
      ? value.toFixed(precision)
      : typeof value === "number"
        ? // Long floats are an artefact of dividing, not information: an average
          // of 7.333333333333333 says nothing the first few digits do not.
          String(Math.round(value * 1e6) / 1e6)
        : String(value);
  return `${prefix ?? ""}${body}${suffix ?? ""}`;
}

/**
 * The strip of aggregate cards above the grid.
 *
 * Every card comes from one scan of the table, so they always agree with each
 * other — and the scan runs through the same permission filter and view filters
 * the grid does, so they agree with what is on screen rather than with the
 * table's true total. Two people looking at the same card can therefore see
 * different numbers, which is correct: a total is a read of the records, and you
 * cannot total what you cannot see.
 */
export function MetricBar({
  tableUuid,
  metrics,
  viewUuid,
  q,
  filters,
  visible,
}: Props) {
  // Keyed on the metric-values *prefix* rather than this card strip's exact key: a
  // live event names the table alone, and every scope's totals went stale together.
  const live = useLiveTable(tableUuid, {
    queryKey: metricValuesPrefix(tableUuid),
    minSeconds: METRIC_REFRESH_SECONDS,
  });
  const values = useQuery({
    // Cards follow the records — same guardrails, same invalidation — but on a
    // slower clock. A metric is an aggregate over the whole table where a page of
    // rows is one indexed query, so matching intervals meant the dearest query on
    // the screen ran as often as the cheapest. The backend compiles the common
    // formulas to SQL, which makes most of them cheap, but not the ones that fall
    // back to a record-by-record scan — and the frontend cannot tell which a given
    // card is. Your *own* edits still show immediately either way: the key is
    // nested under the items key, so the invalidation the grid already fires
    // refreshes the cards too, and the interval only carries other people's
    // changes.
    ...live,
    queryKey: metricValuesKey(tableUuid, viewUuid, q, filters),
    queryFn: () =>
      // The same builder the grid's own query uses, so "which records" cannot be
      // answered one way for the rows and another for the totals above them.
      api.get<MetricValues>(
        `/tables/${tableUuid}/metrics/values?${recordScopeParams({ viewUuid, q, filters })}`,
      ),
    enabled: visible && metrics.length > 0,
  });

  if (!visible || metrics.length === 0) return null;

  const byUuid = new Map((values.data?.values ?? []).map((v) => [v.uuid, v]));
  const truncated = values.data?.truncated ?? false;

  return (
    <div className="flex flex-col gap-2">
      <div className="metric-bar">
        {metrics.map((metric) => {
          const answer = byUuid.get(metric.uuid);
          return (
            <div
              key={metric.uuid}
              className="metric-card"
              title={metric.description ?? metric.config.formula}
            >
              <div className="metric-card-label">{metric.display_name}</div>
              {values.isPending ? (
                <Skeleton width="4rem" height="1.5rem" />
              ) : values.isError ? (
                // The request failed, so there is no answer for any card. Without
                // this the card fell through to `format(null)` and drew "—", which
                // this strip otherwise uses to mean "no matching records" — the one
                // reading that makes a failure look like a fact about the data. The
                // branch below already refuses to let a broken formula read as 0;
                // this is the same care applied one level up.
                <div
                  className="metric-card-error"
                  title="The totals could not be loaded."
                >
                  <i className="pi pi-exclamation-triangle" aria-hidden="true" />
                  <span>Unavailable</span>
                </div>
              ) : answer?.error ? (
                // A formula whose column was renamed reads as broken, never as 0.
                <div className="metric-card-error" title={answer.error}>
                  <i className="pi pi-exclamation-triangle" aria-hidden="true" />
                  <span>{answer.error}</span>
                </div>
              ) : (
                <div className="metric-card-value">
                  {format(answer?.value ?? null, metric)}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {truncated && (
        <small style={{ color: "var(--text-muted)" }}>
          Over the first {values.data?.row_count.toLocaleString()} records — this
          table is too large to total in full.
        </small>
      )}
    </div>
  );
}

/** The table's metrics. Shared by the bar and the settings panel so both read
 *  one cache entry rather than each fetching the same definitions. */
export function useMetrics(tableUuid: string) {
  return useQuery({
    queryKey: metricsKey(tableUuid),
    queryFn: () => api.get<Metric[]>(`/tables/${tableUuid}/metrics/`),
  });
}
