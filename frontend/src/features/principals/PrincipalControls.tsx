import { useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { AutoComplete } from "primereact/autocomplete";

import { api } from "../../lib/api";
import type { ApiUser, ApiUserGroup, Column, ColumnType } from "../../lib/types";

export type PrincipalType = Extract<ColumnType, "user" | "group">;

export interface PrincipalLabels {
  names: Map<string, string>;
  // "Not resolved yet" and "no such account" must not look the same: a uuid whose
  // request is still in flight would otherwise render as "unknown user" on every
  // page load, which claims a deletion that has not happened.
  isResolving: boolean;
}

interface Option {
  label: string;
  value: string;
}

// How many suggestions a search returns. The picker is a typeahead, not a
// directory listing: with thousands of accounts, showing "the first 25 matches,
// keep typing" beats pretending a scroll list is browsable.
const SUGGESTION_LIMIT = 25;
// The batch-resolve endpoint's cap (max_length on the `uuid` query param).
const RESOLVE_CHUNK = 500;

const PATHS: Record<PrincipalType, string> = {
  user: "/auth/users/",
  group: "/auth/groups/",
};

// Users are labelled by **username, never the connected person's name**: person
// records are private (owner or superuser only), while /auth/users is readable by
// any authenticated user so the Share dialog can list principals. Rendering
// "Grace Hopper" in a cell would leak personal data to every viewer of the list.
function labelOf(kind: PrincipalType, row: ApiUser | ApiUserGroup): string {
  return kind === "user"
    ? (row as ApiUser).username
    : (row as ApiUserGroup).name;
}

function toOptions(
  kind: PrincipalType,
  rows: (ApiUser | ApiUserGroup)[],
): Option[] {
  return rows.map((row) => ({ label: labelOf(kind, row), value: row.uuid }));
}

/** Resolve specific uuids to names — one request for the whole set, not one per cell. */
export function usePrincipalLabels(
  kind: PrincipalType,
  uuids: string[],
): PrincipalLabels {
  // Sorted + deduped so two renders holding the same principals share a cache
  // entry regardless of the order the rows happened to come back in.
  const keys = useMemo(
    () => [...new Set(uuids)].sort().slice(0, RESOLVE_CHUNK),
    [uuids],
  );

  const query = useQuery({
    queryKey: ["principal-labels", kind, keys],
    queryFn: async () => {
      const params = keys.map((u) => `uuid=${encodeURIComponent(u)}`).join("&");
      return api.get<(ApiUser | ApiUserGroup)[]>(
        `${PATHS[kind]}?${params}&limit=${keys.length}`,
      );
    },
    enabled: keys.length > 0,
    // The key is the uuid set, so a view swap that briefly empties the grid would
    // otherwise discard the names and re-fetch them. Keep what we resolved.
    placeholderData: keepPreviousData,
  });

  return useMemo(() => {
    const names = new Map<string, string>();
    for (const row of query.data ?? []) names.set(row.uuid, labelOf(kind, row));
    return { names, isResolving: keys.length > 0 && query.isPending };
  }, [query.data, query.isPending, keys.length, kind]);
}

/** One principal's display name: its own name, "…" while resolving, and only then
 *  "unknown", which means the account or group really is gone. */
function nameFor(
  kind: PrincipalType,
  labels: PrincipalLabels,
  uuid: string,
): string {
  return (
    labels.names.get(uuid) ?? (labels.isResolving ? "…" : `unknown ${kind}`)
  );
}

/** Every principal uuid held by `rows`, grouped by the kind of column it came from. */
export function collectPrincipalUuids(
  columns: Column[],
  rows: { data?: Record<string, unknown> }[],
): Record<PrincipalType, string[]> {
  const found: Record<PrincipalType, string[]> = { user: [], group: [] };
  for (const column of columns) {
    if (column.type !== "user" && column.type !== "group") continue;
    for (const row of rows) {
      const value = row.data?.[column.uuid];
      const uuids = Array.isArray(value) ? value : value ? [value] : [];
      for (const uuid of uuids) found[column.type].push(String(uuid));
    }
  }
  return found;
}

export function PrincipalCell({
  column,
  value,
  labels,
}: {
  column: Column;
  value: unknown;
  labels: PrincipalLabels;
}) {
  const kind = column.type as PrincipalType;
  const uuids = Array.isArray(value)
    ? (value as string[])
    : value
      ? [String(value)]
      : [];
  if (uuids.length === 0) return <span className="text-gray-300">—</span>;
  return <span>{uuids.map((u) => nameFor(kind, labels, u)).join(", ")}</span>;
}

/** Search-as-you-type picker. Matching happens server-side, so it works with any
 *  number of accounts — a dropdown of every user does not. */
function PrincipalPicker({
  kind,
  value,
  multiple,
  disabled,
  onChange,
  inputId,
  style,
}: {
  kind: PrincipalType;
  value: unknown;
  multiple: boolean;
  disabled?: boolean;
  onChange: (v: unknown) => void;
  inputId?: string;
  style?: React.CSSProperties;
}) {
  const [suggestions, setSuggestions] = useState<Option[]>([]);

  const uuids = useMemo(
    () =>
      multiple
        ? ((value as string[]) ?? [])
        : value
          ? [String(value)]
          : [],
    [value, multiple],
  );
  // The current value is a uuid; its name has to be fetched to display it, since
  // it is not necessarily in whatever the last search returned.
  const labels = usePrincipalLabels(kind, uuids);
  const selected = useMemo(() => {
    const options = uuids.map((u) => ({
      label: nameFor(kind, labels, u),
      value: u,
    }));
    return multiple ? options : (options[0] ?? null);
  }, [uuids, labels, kind, multiple]);

  async function search(event: { query: string }) {
    const term = event.query.trim();
    const q = term ? `q=${encodeURIComponent(term)}&` : "";
    const rows = await api.get<(ApiUser | ApiUserGroup)[]>(
      `${PATHS[kind]}?${q}limit=${SUGGESTION_LIMIT}`,
    );
    setSuggestions(toOptions(kind, rows));
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
      placeholder={`Search ${kind}s…`}
      style={style}
      onChange={(e) => {
        // While typing, AutoComplete reports the raw string; only a picked
        // option (an object) is a real change.
        if (typeof e.value === "string") return;
        if (multiple) {
          onChange((e.value as Option[]).map((o) => o.value));
        } else {
          onChange((e.value as Option | null)?.value ?? null);
        }
      }}
    />
  );
}

export function PrincipalField({
  column,
  value,
  disabled,
  onChange,
}: {
  column: Column;
  value: unknown;
  disabled: boolean;
  onChange: (v: unknown) => void;
}) {
  return (
    <PrincipalPicker
      kind={column.type as PrincipalType}
      value={value}
      multiple={!!column.config.multiple}
      disabled={disabled}
      onChange={onChange}
      inputId={`field-${column.uuid}`}
    />
  );
}

/** The value picker for a filter condition: always single-valued. */
export function PrincipalFilterValue({
  column,
  value,
  onChange,
}: {
  column: Column;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  return (
    <PrincipalPicker
      kind={column.type as PrincipalType}
      value={value}
      multiple={false}
      onChange={onChange}
      style={{ width: 160 }}
    />
  );
}
