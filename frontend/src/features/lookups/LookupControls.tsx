import { useMemo, useState } from "react";
import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { api } from "../../lib/api";
import { ReferencePicker } from "../references/ReferencePicker";
import { useEffectiveActions } from "../permissions/useEffectiveActions";
import type { Column, ListItem } from "../../lib/types";
import { CreateTargetRowDialog } from "./CreateTargetRowDialog";

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

/** Resolve a column's page of uuids to labels, memoised.
 *
 *  The memo is the point. `toNames` builds a Map over the whole response — up to
 *  `RESOLVE_CHUNK` (500) entries — and this is called by *every cell of the column*,
 *  on every render. The grid runs with `cellMemo` off (see ListDetailPage), so a
 *  single `setState` anywhere on the page re-renders every cell and each one rebuilt
 *  that Map from scratch: a hundred rows across three lookup columns is 150,000 Map
 *  insertions for an answer that has not changed.
 *
 *  `usePrincipalLabels` has done exactly this since it was written; the lookup side
 *  was the copy that never got it. Keeping the two in step matters — they back the
 *  two halves of the same grid and the same page passes both the same uuid array. */
function useLookupLabels(
  listUuid: string,
  columnUuid: string,
  pageUuids: string[],
): LookupLabels {
  const result = useQuery(labelQuery(listUuid, columnUuid, pageUuids));
  return useMemo(
    () => toLabels(pageUuids, result),
    // `result` itself is a new object every render, so the deps are the three
    // things `toLabels` actually reads. `pageUuids` is stable per page: the caller
    // builds it once in a memo and hands the same array to every cell.
    [pageUuids, result.data, result.isPending, result.isFetching],
  );
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
  const resolved = useLookupLabels(listUuid, column.uuid, pageUuids);
  const uuids = Array.isArray(value)
    ? (value as string[])
    : value
      ? [String(value)]
      : [];
  if (uuids.length === 0) return <span className="text-faint">—</span>;
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
  ariaLabel,
  style,
  parentValues,
  parentLabel,
  allowCreate,
}: {
  listUuid: string;
  column: Column;
  value: unknown;
  multiple: boolean;
  disabled?: boolean;
  onChange: (v: unknown) => void;
  inputId?: string;
  ariaLabel?: string;
  style?: React.CSSProperties;
  // Nested lookup, decided by the *caller*: undefined means "not nested in this
  // context" — the grid, filter editors and label resolution stay unfiltered.
  // An empty array means the parent is unset, so there is nothing to offer yet.
  parentValues?: string[];
  parentLabel?: string;
  // Whether *this context* may create a missing target row. Decided by the
  // caller, like `parentValues`: filling in a record can, choosing a filter
  // value cannot — there is no record for the new row to belong to.
  allowCreate?: boolean;
}) {
  const queryClient = useQueryClient();
  const uuids = useMemo(
    () => (multiple ? ((value as string[]) ?? []) : value ? [String(value)] : []),
    [value, multiple],
  );
  // The current value is a row uuid; its label has to be fetched to show it, as it
  // is not necessarily among whatever the last search returned — and, on a nested
  // lookup, a stale value may not be offered by any filtered search at all.
  const result = useQuery(labelQuery(listUuid, column.uuid, uuids));
  const resolved = toLabels(uuids, result);

  const awaitingParent = parentValues !== undefined && parentValues.length === 0;
  const parentQuery = (parentValues ?? [])
    .map((u) => `parent_value=${u}&`)
    .join("");

  const targetListUuid = column.config.target_list_uuid;
  // The gate, and the reason this feature discloses nothing new: `create`
  // confers `view`, so anyone this returns true for could already open the
  // target list and read its columns. Resolved per caller rather than from the
  // column's config, because whoever configured the lookup is routinely not
  // whoever fills the form — and the target's own POST enforces it again.
  const targetActions = useEffectiveActions("list", targetListUuid);
  const canCreate =
    !!allowCreate &&
    !!column.config.allow_create &&
    !!targetListUuid &&
    !disabled &&
    !awaitingParent &&
    targetActions.has("create");

  // The text the dialog was opened with. Null when it is shut — the dialog is
  // keyed off it, so each attempt starts from a clean form rather than the last
  // one's leftovers.
  const [creating, setCreating] = useState<string | null>(null);

  return (
    <>
      <ReferencePicker
        uuids={uuids}
        multiple={multiple}
        labelFor={(uuid) => nameFor(resolved, uuid)}
        search={async (term) => {
          const q = term.trim() ? `q=${encodeURIComponent(term.trim())}&` : "";
          const rows = await api.get<LookupOption[]>(
            optionsUrl(
              listUuid,
              column.uuid,
              `${q}${parentQuery}limit=${SUGGESTION_LIMIT}`,
            ),
          );
          return rows.map((r) => ({
            label: r.label || fallback(r.uuid),
            value: r.uuid,
          }));
        }}
        onChange={onChange}
        placeholder={
          awaitingParent ? `Select ${parentLabel ?? "the parent"} first` : "Search rows…"
        }
        disabled={disabled || awaitingParent}
        inputId={inputId}
        ariaLabel={ariaLabel}
        style={style}
        create={
          canCreate
            ? { label: (term) => `Add “${term}”…`, onCreate: setCreating }
            : undefined
        }
      />
      {creating !== null && targetListUuid && (
        <CreateTargetRowDialog
          targetListUuid={targetListUuid}
          term={creating}
          displayColumnUuid={column.config.display_column_uuid}
          filterColumnUuid={column.config.target_filter_column_uuid}
          parentValues={parentValues}
          visible
          onHide={() => setCreating(null)}
          onCreated={(uuid) => {
            onChange(multiple ? [...uuids, uuid] : uuid);
            // The new row's label is resolved by a *different* query than the
            // one that just refetched — this column's, keyed by the uuid set it
            // holds. Without this the cell shows a row stub until something else
            // happens to change that set.
            queryClient.invalidateQueries({
              queryKey: ["lookup-labels", listUuid, column.uuid],
            });
            setCreating(null);
          }}
        />
      )}
    </>
  );
}

export function LookupField({
  listUuid,
  column,
  value,
  disabled,
  onChange,
  parentValues,
  parentLabel,
  allowCreate = true,
}: {
  listUuid: string;
  column: Column;
  value: unknown;
  disabled: boolean;
  onChange: (v: unknown) => void;
  /** See LookupPicker: undefined = not nested here, [] = parent unset. */
  parentValues?: string[];
  parentLabel?: string;
  /** Off for a surface that cannot host a dialog over itself. The column's own
   *  `allow_create` and the writer's access still both have to agree. */
  allowCreate?: boolean;
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
      parentValues={parentValues}
      parentLabel={parentLabel}
      allowCreate={allowCreate}
    />
  );
}

/** The value picker for a filter condition: always single-valued, and never
 *  offers to create a row — a filter is a question about rows that exist, and
 *  there is no record here for a new one to belong to. */
export function LookupFilterValue({
  listUuid,
  column,
  value,
  onChange,
  inputId,
  ariaLabel,
}: {
  listUuid: string;
  column: Column;
  value: unknown;
  onChange: (v: unknown) => void;
  /** The filter editor's rows are three unlabelled controls repeated; this is how
   *  the value one gets a name. See FilterConditionsEditor. */
  inputId?: string;
  ariaLabel?: string;
}) {
  return (
    <LookupPicker
      listUuid={listUuid}
      column={column}
      value={value}
      multiple={false}
      onChange={onChange}
      style={{ width: 160 }}
      inputId={inputId}
      ariaLabel={ariaLabel}
    />
  );
}
