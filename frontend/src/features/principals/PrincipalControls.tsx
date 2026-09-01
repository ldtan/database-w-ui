import { useMemo } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../lib/api";
import { ReferencePicker } from "../references/ReferencePicker";
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
//
// A group has no such constraint, so it gets its `display_name` — which is why a
// cell reads "Registrar's Office" while an export of the same cell writes
// `registrar_office`. The screen is for people and the file has to round-trip.
function labelOf(kind: PrincipalType, row: ApiUser | ApiUserGroup): string {
  const name =
    kind === "user"
      ? (row as ApiUser).username
      : (row as ApiUserGroup).display_name;
  // A deleted account still names the person who owned the record. Saying so beats
  // showing a uuid, and beats pretending the cell was always empty.
  return row.is_deleted ? `${name} (deleted)` : name;
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
    // isFetching, not isPending: with placeholderData the status is already
    // "success" while the new uuid set is still in flight, so isPending would be
    // false and a just-assigned account would render as "unknown user" until the
    // response landed. A name we already hold wins over this flag anyway (see
    // nameFor), so it only ever shows "…" for uuids we genuinely cannot name yet.
    return {
      names,
      isResolving: keys.length > 0 && (query.isPending || query.isFetching),
    };
  }, [query.data, query.isPending, query.isFetching, keys.length, kind]);
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
  pageUuids,
}: {
  column: Column;
  value: unknown;
  // Every principal uuid on this page, for this kind of column. The cell resolves
  // through it rather than being handed a finished map, so that it *subscribes* to
  // the query: PrimeReact's DataTable only re-renders when its `value` changes, so
  // a map passed down as a prop would freeze at whatever was resolved when the rows
  // last changed — cells would sit on "…" forever after an edit. Every cell shares
  // one query key, so this is still a single request for the page, not one per cell.
  pageUuids: string[];
}) {
  const kind = column.type as PrincipalType;
  const labels = usePrincipalLabels(kind, pageUuids);
  const uuids = Array.isArray(value)
    ? (value as string[])
    : value
      ? [String(value)]
      : [];
  if (uuids.length === 0) return <span className="text-faint">—</span>;
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
  ariaLabel,
  style,
}: {
  kind: PrincipalType;
  value: unknown;
  multiple: boolean;
  disabled?: boolean;
  onChange: (v: unknown) => void;
  inputId?: string;
  ariaLabel?: string;
  style?: React.CSSProperties;
}) {
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

  return (
    <ReferencePicker
      uuids={uuids}
      multiple={multiple}
      labelFor={(uuid) => nameFor(kind, labels, uuid)}
      search={async (term) => {
        const q = term.trim() ? `q=${encodeURIComponent(term.trim())}&` : "";
        const rows = await api.get<(ApiUser | ApiUserGroup)[]>(
          `${PATHS[kind]}?${q}limit=${SUGGESTION_LIMIT}`,
        );
        return toOptions(kind, rows);
      }}
      onChange={onChange}
      placeholder={`Search ${kind}s…`}
      disabled={disabled}
      inputId={inputId}
      ariaLabel={ariaLabel}
      style={style}
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
  inputId,
  ariaLabel,
}: {
  column: Column;
  value: unknown;
  onChange: (v: unknown) => void;
  /** See LookupFilterValue — the filter editor names its controls. */
  inputId?: string;
  ariaLabel?: string;
}) {
  return (
    <PrincipalPicker
      kind={column.type as PrincipalType}
      value={value}
      multiple={false}
      onChange={onChange}
      style={{ width: 160 }}
      inputId={inputId}
      ariaLabel={ariaLabel}
    />
  );
}
