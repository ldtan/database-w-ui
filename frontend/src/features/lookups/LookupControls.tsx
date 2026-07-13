import { useMemo } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../lib/api";
import { ReferencePicker } from "../references/ReferencePicker";
import type { Column, ListItem } from "../../lib/types";

// One row of a lookup's target list. The server derives `label` from the lookup's
// display column (the same `display_column_key` export and import use), so a label
// means the same thing in the grid, the picker and a CSV.
interface LookupOption {
  uuid: string;
  label: string | null;
  // The row is in the bin. It still resolves — the relationship is real and the
  // record still points at it — but it is shown as gone, and no picker offers it.
  deleted?: boolean;
}

export interface LookupLabels {
  names: Map<string, string>;
  // "Not resolved yet" must not look like "row is gone": an in-flight uuid would
  // otherwise render as a stub id on every page load.
  isResolving: boolean;
}
const RESOLVE_CHUNK = 500; // the endpoint's cap on `uuid` params
const SUGGESTION_LIMIT = 25;

/** A row with no label has nothing else to identify it by. */
function fallback(uuid: string): string {
  return `Row ${uuid.slice(0, 8)}`;
}

function nameFor(labels: LookupLabels, uuid: string): string {
  return labels.names.get(uuid) ?? (labels.isResolving ? "…" : fallback(uuid));
}

function optionsUrl(
  listUuid: string,
  columnUuid: string,
  query: string,
): string {
  return `/lists/${listUuid}/columns/${columnUuid}/options?${query}`;
}

function toNames(rows: LookupOption[] | undefined): Map<string, string> {
  const names = new Map<string, string>();
  for (const row of rows ?? []) {
    const label = row.label || fallback(row.uuid);
    names.set(row.uuid, row.deleted ? `${label} (deleted)` : label);
  }
  return names;
}

/** column uuid -> every target-row uuid that column holds across `rows`, deduped.
 *
 *  Sorted so the set is a stable query key however the rows are ordered; every
 *  cell of a column is handed the same array and therefore shares one request. */
export function collectLookupUuids(
  columns: Column[],
  rows: ListItem[],
): Map<string, string[]> {
  const found = new Map<string, string[]>();
  for (const column of columns) {
    if (column.type !== "lookup") continue;
    const uuids = new Set<string>();
    for (const row of rows) {
      const value = row.data?.[column.uuid];
      const list = Array.isArray(value) ? value : value ? [value] : [];
      for (const uuid of list) uuids.add(String(uuid));
    }
    found.set(column.uuid, [...uuids].sort().slice(0, RESOLVE_CHUNK));
  }
  return found;
}

// One query definition, shared by every cell of a column and by the picker. They
// pass the same uuid set, so react-query serves them all from one request.
function labelQuery(listUuid: string, columnUuid: string, uuids: string[]) {
  return {
    queryKey: ["lookup-labels", listUuid, columnUuid, uuids],
    queryFn: () =>
      api.get<LookupOption[]>(
        optionsUrl(
          listUuid,
          columnUuid,
          `${uuids.map((u) => `uuid=${u}`).join("&")}&limit=${uuids.length}`,
        ),
      ),
    enabled: uuids.length > 0,
    // The key is the uuid set, so it changes whenever the records do — including
    // the moment a view swap empties the grid mid-refetch. Without this, every
    // such blip drops the labels and the cells fall back to row stubs; keeping the
    // previous ones means a label, once resolved, stays put.
    placeholderData: keepPreviousData,
    // A 403 means the caller cannot view the target list; the cell degrades to the
    // row stub rather than retrying a request that will never succeed.
    retry: false,
  };
}

function toLabels(
  uuids: string[],
  result:
    | { data?: LookupOption[]; isPending: boolean; isFetching: boolean }
    | undefined,
): LookupLabels {
  return {
    names: toNames(result?.data),
    // isFetching, not isPending: with placeholderData the status is already
    // "success" while the new uuid set is still in flight, so a row linked a
    // moment ago would render as a stub id rather than "…". A label we already
    // hold wins over this flag (see nameFor), so "…" only shows for uuids we
    // genuinely cannot name yet.
    isResolving: uuids.length > 0 && !!(result?.isPending || result?.isFetching),
  };
}

export function LookupCell({
  listUuid,
  column,
  value,
  pageUuids,
}: {
  listUuid: string;
  column: Column;
  value: unknown;
  // Every row uuid this column holds across the page. The cell subscribes to the
  // query itself rather than taking a finished map as a prop — see the note on
  // PrincipalCell: DataTable only re-renders when its `value` changes, so a map
  // passed down would freeze and the cell would sit on "…" after an edit. All the
  // column's cells share one query key, so it stays one request per column.
  pageUuids: string[];
}) {
  const result = useQuery(labelQuery(listUuid, column.uuid, pageUuids));
  const resolved = toLabels(pageUuids, result);
  const uuids = Array.isArray(value)
    ? (value as string[])
    : value
      ? [String(value)]
      : [];
  if (uuids.length === 0) return <span className="text-gray-300">—</span>;
  return <span>{uuids.map((u) => nameFor(resolved, u)).join(", ")}</span>;
}

/** Search-as-you-type picker over the target list's rows. Matching is server-side,
 *  so it works with a target of any size — fetching every row does not. */
function LookupPicker({
  listUuid,
  column,
  value,
  multiple,
  disabled,
  onChange,
  inputId,
  style,
}: {
  listUuid: string;
  column: Column;
  value: unknown;
  multiple: boolean;
  disabled?: boolean;
  onChange: (v: unknown) => void;
  inputId?: string;
  style?: React.CSSProperties;
}) {
  const uuids = useMemo(
    () => (multiple ? ((value as string[]) ?? []) : value ? [String(value)] : []),
    [value, multiple],
  );
  // The current value is a row uuid; its label has to be fetched to show it, as it
  // is not necessarily among whatever the last search returned.
  const result = useQuery(labelQuery(listUuid, column.uuid, uuids));
  const resolved = toLabels(uuids, result);

  return (
    <ReferencePicker
      uuids={uuids}
      multiple={multiple}
      labelFor={(uuid) => nameFor(resolved, uuid)}
      search={async (term) => {
        const q = term.trim() ? `q=${encodeURIComponent(term.trim())}&` : "";
        const rows = await api.get<LookupOption[]>(
          optionsUrl(listUuid, column.uuid, `${q}limit=${SUGGESTION_LIMIT}`),
        );
        return rows.map((r) => ({
          label: r.label || fallback(r.uuid),
          value: r.uuid,
        }));
      }}
      onChange={onChange}
      placeholder="Search rows…"
      disabled={disabled}
      inputId={inputId}
      style={style}
    />
  );
}

export function LookupField({
  listUuid,
  column,
  value,
  disabled,
  onChange,
}: {
  listUuid: string;
  column: Column;
  value: unknown;
  disabled: boolean;
  onChange: (v: unknown) => void;
}) {
  return (
    <LookupPicker
      listUuid={listUuid}
      column={column}
      value={value}
      multiple={!!column.config.multiple}
      disabled={disabled}
      onChange={onChange}
      inputId={`field-${column.uuid}`}
    />
  );
}

/** The value picker for a filter condition: always single-valued. */
export function LookupFilterValue({
  listUuid,
  column,
  value,
  onChange,
}: {
  listUuid: string;
  column: Column;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  return (
    <LookupPicker
      listUuid={listUuid}
      column={column}
      value={value}
      multiple={false}
      onChange={onChange}
      style={{ width: 160 }}
    />
  );
}
