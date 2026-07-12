import { useMemo, useState } from "react";
import { keepPreviousData, useQueries, useQuery } from "@tanstack/react-query";
import { AutoComplete } from "primereact/autocomplete";

import { api } from "../../lib/api";
import type { Column, ListItem } from "../../lib/types";

// One row of a lookup's target list. The server derives `label` from the lookup's
// display column (the same `display_column_key` export and import use), so a label
// means the same thing in the grid, the picker and a CSV.
interface LookupOption {
  uuid: string;
  label: string | null;
}

export interface LookupLabels {
  names: Map<string, string>;
  // "Not resolved yet" must not look like "row is gone": an in-flight uuid would
  // otherwise render as a stub id on every page load.
  isResolving: boolean;
}
/** Per lookup column (keyed by its uuid). */
export type LookupLabelMap = Map<string, LookupLabels>;

const RESOLVE_CHUNK = 500; // the endpoint's cap on `uuid` params
const SUGGESTION_LIMIT = 25;

const EMPTY: LookupLabels = { names: new Map(), isResolving: false };

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
  for (const row of rows ?? []) names.set(row.uuid, row.label || fallback(row.uuid));
  return names;
}

/** The uuids each lookup column holds across `rows`, deduped. */
function collectLookupUuids(
  columns: Column[],
  rows: ListItem[],
): [string, string[]][] {
  const found: [string, string[]][] = [];
  for (const column of columns) {
    if (column.type !== "lookup") continue;
    const uuids = new Set<string>();
    for (const row of rows) {
      const value = row.data?.[column.uuid];
      const list = Array.isArray(value) ? value : value ? [value] : [];
      for (const uuid of list) uuids.add(String(uuid));
    }
    found.push([column.uuid, [...uuids].sort().slice(0, RESOLVE_CHUNK)]);
  }
  return found;
}

// One query definition, used two ways: many at once for a grid (useQueries, since
// the number of lookup columns is only known at render), and one at a time for a
// picker resolving its own value.
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
  result: { data?: LookupOption[]; isPending: boolean } | undefined,
): LookupLabels {
  return {
    names: toNames(result?.data),
    isResolving: uuids.length > 0 && !!result?.isPending,
  };
}

/** Resolve the labels a page's lookup cells need: one request per lookup column,
 *  not one per cell, and never the whole target list. */
export function useLookupLabels(
  listUuid: string,
  columns: Column[],
  rows: ListItem[],
): LookupLabelMap {
  const perColumn = useMemo(
    () => collectLookupUuids(columns, rows),
    [columns, rows],
  );

  const results = useQueries({
    queries: perColumn.map(([columnUuid, uuids]) =>
      labelQuery(listUuid, columnUuid, uuids),
    ),
  });

  const map: LookupLabelMap = new Map();
  perColumn.forEach(([columnUuid, uuids], i) => {
    map.set(columnUuid, toLabels(uuids, results[i]));
  });
  return map;
}

export function LookupCell({
  value,
  labels,
}: {
  value: unknown;
  labels: LookupLabels | undefined;
}) {
  const resolved = labels ?? EMPTY;
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
  const [suggestions, setSuggestions] = useState<
    { label: string; value: string }[]
  >([]);

  const uuids = useMemo(
    () => (multiple ? ((value as string[]) ?? []) : value ? [String(value)] : []),
    [value, multiple],
  );
  // The current value is a row uuid; its label has to be fetched to show it, as it
  // is not necessarily among whatever the last search returned.
  const result = useQuery(labelQuery(listUuid, column.uuid, uuids));
  const resolved = toLabels(uuids, result);

  const selected = useMemo(() => {
    const options = uuids.map((u) => ({ label: nameFor(resolved, u), value: u }));
    return multiple ? options : (options[0] ?? null);
  }, [uuids, resolved, multiple]);

  async function search(event: { query: string }) {
    const term = event.query.trim();
    const q = term ? `q=${encodeURIComponent(term)}&` : "";
    const rows = await api.get<LookupOption[]>(
      optionsUrl(listUuid, column.uuid, `${q}limit=${SUGGESTION_LIMIT}`),
    );
    setSuggestions(
      rows.map((r) => ({ label: r.label || fallback(r.uuid), value: r.uuid })),
    );
  }

  return (
    <AutoComplete
      inputId={inputId}
      value={selected}
      suggestions={suggestions}
      completeMethod={search}
      field="label"
      multiple={multiple}
      dropdown
      forceSelection
      disabled={disabled}
      placeholder="Search rows…"
      style={style}
      onChange={(e) => {
        // While typing, AutoComplete reports the raw string; only a picked option
        // (an object) is a real change.
        if (typeof e.value === "string") return;
        if (multiple) {
          onChange((e.value as { value: string }[]).map((o) => o.value));
        } else {
          onChange((e.value as { value: string } | null)?.value ?? null);
        }
      }}
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
