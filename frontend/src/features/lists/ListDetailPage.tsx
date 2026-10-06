import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DataTable, type DataTableSortEvent } from "primereact/datatable";
import { Column as DataColumn } from "primereact/column";
import { Button } from "primereact/button";
import { Menu } from "primereact/menu";
import { SplitButton } from "primereact/splitbutton";
import { Message } from "primereact/message";
import { ConfirmDialog, confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { COUNT_REFRESH_SECONDS } from "../../lib/config";
import { BULK_MAX } from "../../lib/limits";
import { useToast } from "../../lib/toast";
import { useDebounced } from "../../lib/useDebounced";
import { useLiveList } from "../../lib/useLiveList";
import { SMALL_SCREEN, WIDE_SCREEN, useMediaQuery } from "../../lib/useMediaQuery";
import type { Crumb } from "../../components/Breadcrumbs";
import { EmptyState } from "../../components/EmptyState";
import { QueryError } from "../../components/QueryError";
import { PageHeader } from "../../components/PageHeader";
import { RowActions } from "../../components/RowActions";
import { SearchInput } from "../../components/SearchInput";
import type {
  Column,
  FilterGroup,
  Form,
  ListItem,
  ListRead,
  View,
  ViewConfig,
  Workspace,
} from "../../lib/types";
import { ListSettingsDialog } from "./ListSettingsDialog";
import { MetricBar, useMetrics } from "../metrics/MetricBar";
import { RecordCards } from "../records/RecordCards";
import { RecordFormDialog } from "../records/RecordFormDialog";
import { RecordViewDialog } from "../records/RecordViewDialog";
import { ArchiveHint, isWholeListScope } from "../records/ArchiveHint";
import { BinnedRecordsDialog } from "../records/BinnedRecordsDialog";
import { fillFromQr, parseQrPayload } from "../records/qrFill";
import {
  cellEditKinds,
  cellPatch,
  type CellDraft,
  type CellEditKind,
} from "../records/cellEdit";
import { InlineCellEditor } from "../records/inlineEdit";
import { isNewRow, landedOnPage, newRowItem } from "../records/newRow";
import { recordCountKey, recordCountsKey, recordPagesKey } from "../records/recordKeys";
import { buildRecordData, deserialize, resolveFields } from "../records/recordValues";
import { renderCell } from "../records/renderCell";
import { ImportExportDialog } from "../transfer/ImportExportDialog";
import { collectLookupUuids } from "../lookups/LookupControls";
import { collectPrincipalUuids } from "../principals/PrincipalControls";
import { SortControl } from "../views/SortControl";
import { ViewBar } from "../views/ViewBar";
import { ViewControls } from "../views/ViewControls";
import { FilterDialog } from "../views/FilterDialog";
import { recordParams, recordScopeParams } from "../views/recordQuery";
import { moveTo } from "../views/reorder";
import { ShareDialog } from "../permissions/ShareDialog";
import {
  useEffectiveActions,
  useEffectiveActionsMap,
} from "../permissions/useEffectiveActions";

type SortOrder = 1 | 0 | -1 | null | undefined;

/** No conditions. Sent as-is rather than omitted, which is what tells the server
 *  "no filter" instead of "whatever the view saved" — the two differ for a view
 *  that filters, and being able to look past one is the point. */
const EMPTY_FILTER: FilterGroup = { conjunction: "and", conditions: [] };

/** How long the grid waits after the last cell commit before asking the server
 *  what else moved. Long enough that tabbing across a row is one burst, short
 *  enough that a single edit settles while you are still looking at it. */
const SETTLE_DELAY_MS = 800;

/** One page with one record replaced, or the page untouched if it is not on it. */
function withItem(
  rows: ListItem[],
  uuid: string,
  next: (item: ListItem) => ListItem,
): ListItem[] {
  return rows.map((item) => (item.uuid === uuid ? next(item) : item));
}

// Split out of the bundle: the decoder it pulls in is ~110 kB gzipped — a quarter
// of the whole app — for a dialog most sessions never open. It arrives when the
// scanner does. (`qrFill`, which is small and pure, stays in the main bundle.)
const QrScanDialog = lazy(() =>
  import("../records/QrScanDialog").then((m) => ({ default: m.QrScanDialog })),
);
// Lazy for the same reason: it renders the scanner, so importing it eagerly would
// drag the decoder back into the main bundle by the back door.
const QrSubmitSession = lazy(() =>
  import("../records/QrSubmitSession").then((m) => ({
    default: m.QrSubmitSession,
  })),
);
// Generating codes is the other half of the same story and splits for the same reason:
// the *encoder* is its own ~10 kB, which `lib/qr.ts` already imports dynamically — but
// loading this dialog eagerly would still pull the picker and its query in for every
// session that never prints a badge.
const QrCodeDialog = lazy(() =>
  import("../records/QrCodeDialog").then((m) => ({ default: m.QrCodeDialog })),
);

/**
 * A list's records, and everything you do to them.
 *
 * The page owns the *view state* — sort, grouping, hidden columns, column order,
 * page size, the search term — and hands it to whatever renders the records. It is
 * seeded from the active view's saved config and diverges from it as you work; the
 * "Save view" button in the toolbar writes it back, which is why nothing here
 * persists on its own.
 *
 * Two renderers, one state: `DataTable` on a desktop, `RecordCards` below 640px
 * (see `useMediaQuery`). They share the same selection, the same page, the same
 * per-record permissions and the same `renderCell` — a value or an order that
 * differed between them would be a bug, not a design.
 *
 * Filtering, searching, sorting and paging are *all* server-side (`?view_uuid=`,
 * `?q=`, `?sort_by=`, `?skip=`/`?limit=`), so the records arriving here are exactly
 * the page to show, in the order to show it. Each of those was once done in the
 * browser over whatever had been fetched, which quietly capped a list at the
 * endpoint's default 100 rows and sorted only the rows that happened to be inside
 * it. Grouping pins the sort to the group column, because the server orders by it
 * first to keep a group's rows adjacent.
 *
 * That is also why a record *added* from the grid may vanish the moment it is
 * saved. The draft row (see `newRow.ts`) posts the whole record at once and then
 * refetches; where it lands is the server's answer, not this page's, and page 1 of
 * an ascending sort is the wrong end of the list for a record made just now. The
 * toast says so, because a row that disappears without explanation reads as a bug.
 */
export function ListDetailPage() {
  const { listUuid = "" } = useParams();
  const queryClient = useQueryClient();
  const toast = useToast();
  // A table of a dozen columns is unreadable on a phone; below 640px the records
  // render as cards instead. Same data, same actions, same selection — see
  // RecordCards.
  const small = useMediaQuery(SMALL_SCREEN);
  // Below this the row's actions collapse to a single "⋮" — see WIDE_SCREEN for
  // why the number is its own and not the sidebar's.
  const wideActions = useMediaQuery(WIDE_SCREEN);
  // Read by the actions column *and* by the table's minimum width, which counts
  // that column: let the two drift apart and the grid either overflows by a hair
  // or leaves a gap at the end of every row.
  const actionsWidth = wideActions ? 7 : 4;
  const [showSettings, setShowSettings] = useState(false);
  const [showTransfer, setShowTransfer] = useState(false);
  const [showDeleted, setShowDeleted] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  // The record whose sharing is open. Held as the *item*, not as a uuid to be
  // looked up in the current page: `rows` is one server-side page, refreshed by
  // the live stream and by `scheduleSettle`, so a row that stops matching the
  // active filter — because somebody else edited it, or because you just changed
  // its inheritance — drops off the page and the dialog vanished mid-sentence,
  // taking a half-typed grant with it. `recordDialog` and `viewItem` already hold
  // their subject this way; this was the one that re-derived it.
  const [sharingItem, setSharingItem] = useState<ListItem | null>(null);
  const [recordDialog, setRecordDialog] = useState<{
    open: boolean;
    item: ListItem | null;
    // Both set only by the QR scanner: what it read, and what it could not.
    prefill?: Record<string, unknown> | null;
    notes?: string[];
  }>({ open: false, item: null });
  // The record the read-only detail dialog is showing, if any.
  const [viewItem, setViewItem] = useState<ListItem | null>(null);
  const [selection, setSelection] = useState<ListItem[]>([]);
  // Which QR flow is running, if any: fill the form, or submit it outright.
  const [qrMode, setQrMode] = useState<"fill" | "submit" | null>(null);
  const [qrBusy, setQrBusy] = useState(false);
  const [qrError, setQrError] = useState<string | null>(null);
  // What a submitting session sets on every scan. Held here rather than inside the
  // session so that closing the scanner and opening it again keeps the event you
  // are taking attendance for. Deliberately not persisted any further than that:
  // it is the answer to "what is happening right now", not a setting.
  const [qrPresets, setQrPresets] = useState<Record<string, unknown>>({});
  // The records a QR dialog is open over: one from a row action, or the selection from
  // the bar. One piece of state for both, because the dialog is one dialog — a sheet of
  // forty and a sheet of one differ only in layout. Empty means closed.
  const [qrItems, setQrItems] = useState<ListItem[]>([]);

  const [activeViewUuid, setActiveViewUuid] = useState<string | null>(null);
  const [sortField, setSortField] = useState<string | undefined>(undefined);
  const [sortOrder, setSortOrder] = useState<SortOrder>(undefined);
  const [pageSize, setPageSize] = useState(25);
  // The offset of the page on screen. Server-side, so this is the `skip` sent with
  // the query rather than an index into an array the browser already holds.
  const [first, setFirst] = useState(0);
  const [showFilter, setShowFilter] = useState(false);
  const [search, setSearch] = useState("");
  // Debounced so a query goes out per pause, not per keystroke.
  const q = useDebounced(search);
  const [groupBy, setGroupBy] = useState<string | null>(null);
  // The filter in effect right now. Seeded from the view and sent with the read,
  // like the sort and the grouping beside it — filtering used to be the one control
  // here that wrote straight to the view, so asking a question changed the list for
  // everyone and needed `configure` to ask.
  const [filters, setFilters] = useState<FilterGroup>(EMPTY_FILTER);
  // Starts hidden, and is page state like the rest of this bar rather than
  // something saved: whether the strip is showing is how you are working right now,
  // not a property of the list.
  //
  // `lists.settings` does now hold one saved per-list thing — `settings.qr`, the fields
  // a printed QR code carries — so the old form of this note ("the app has no per-list
  // UI preferences to hang it on") is no longer true as stated. The distinction that
  // matters is still the one it was reaching for: that key describes a *printed
  // artifact*, the same kind of thing a view or a form is, where this boolean describes
  // a person's current screen. Reaching for the blob to remember a toggle would still
  // be the first of those.
  const [showMetrics, setShowMetrics] = useState(false);
  // "Edit cells": the grid's values become editable in place, and a row can be
  // added at the foot of it. Page state for the same reason `showMetrics` is — it
  // is how you are working right now, not a property of the list or of the view,
  // and it deliberately does not survive a reload: editing rows by accident is
  // worse than turning the mode on again.
  const [inlineEdit, setInlineEdit] = useState(false);
  // What the open cell editor holds. A ref rather than state: it changes on every
  // keystroke, and re-rendering a hundred-row grid for each one would be a real
  // cost for a value nothing on screen reads until the edit is committed.
  const cellDraft = useRef<CellDraft | null>(null);
  // The record being typed at the foot of the grid, or null when there is none.
  // Held in *stored* form — the shape a record's `data` has — so the draft row
  // renders and edits through exactly the same code every other row does.
  const [newRow, setNewRow] = useState<Record<string, unknown> | null>(null);
  // The same value, readable synchronously. "Add record" has to flush whatever
  // cell editor is still open and then post the result, and a `setState` between
  // those two would not have landed by the time the post reads it.
  const newRowRef = useRef<Record<string, unknown> | null>(null);
  const writeNewRow = (next: Record<string, unknown> | null) => {
    newRowRef.current = next;
    setNewRow(next);
  };
  const [hiddenColumns, setHiddenColumns] = useState<string[]>([]);
  const [columnOrder, setColumnOrder] = useState<string[]>([]);

  const list = useQuery({
    queryKey: ["list", listUuid],
    queryFn: () => api.get<ListRead>(`/lists/${listUuid}`),
  });
  const columns = useQuery({
    queryKey: ["columns", listUuid],
    queryFn: () => api.get<Column[]>(`/lists/${listUuid}/columns/`),
  });
  // Live records: keep this list current as other people add/edit/remove rows.
  // Spread first so the query's own keys/fn win. It subscribes to the list's event
  // stream and invalidates the key it is given, returning a polling
  // `refetchInterval` only if that stream cannot be held open (see useLiveList).
  const live = useLiveList(listUuid, { queryKey: recordPagesKey(listUuid) });
  // The same seam on a slower clock for the total, which is a dearer question than
  // the page it describes — see COUNT_REFRESH_SECONDS. A floor, so it can only ever
  // slow the count down relative to the records, never speed it up. Keyed on the
  // count *prefix*, not this page's key: an event names the list and nothing else,
  // and every scope's total went stale at the same moment.
  const liveCount = useLiveList(listUuid, {
    queryKey: recordCountsKey(listUuid),
    minSeconds: COUNT_REFRESH_SECONDS,
  });
  // Everything that decides *which* records come back is part of the key, because
  // every one of them is applied server-side: the page, the order, the view and
  // the search each produce a different set of rows, not a rearrangement of one
  // the browser already has.
  //
  // Named rather than written inline because adding a record reads it back: the
  // only way to learn whether the new row belongs on the page in front of you is to
  // look at the page the refetch brought back, and that means naming it exactly.
  const itemsKey = [
    ...recordPagesKey(listUuid),
    activeViewUuid,
    q,
    first,
    pageSize,
    sortField,
    sortOrder,
    groupBy,
    filters,
  ];
  const items = useQuery({
    ...live,
    queryKey: itemsKey,
    queryFn: () =>
      api.get<ListItem[]>(
        `/lists/${listUuid}/items/?${recordParams({
          viewUuid: activeViewUuid,
          q,
          filters,
          groupBy,
          sortField,
          sortOrder,
          skip: first,
          limit: pageSize,
        })}`,
      ),
    // Paging replaces the page rather than blanking the grid and dropping its
    // height to nothing while the next one is in flight.
    placeholderData: (prev) => prev,
  });
  // The paginator's total, asked separately because it is the same number for
  // every page of one query. Keyed on the view, the search and the filter alone
  // (see `recordCountKey`), so turning a page, sorting or grouping all read the
  // cached answer instead of making the server recount every matching record.
  // Live like the pages are, since a record someone else adds changes it — but on
  // its own floor, because it costs far more than a page and re-asking it is what
  // an idle tab was mostly doing.
  const count = useQuery({
    ...liveCount,
    queryKey: recordCountKey(listUuid, activeViewUuid, q, filters),
    queryFn: () =>
      api.get<{ total: number }>(
        `/lists/${listUuid}/items/count?${recordScopeParams({
          viewUuid: activeViewUuid,
          q,
          filters,
        })}`,
      ),
    placeholderData: (prev) => prev,
  });
  const rows = items.data ?? [];
  const total = count.data?.total ?? 0;
  // The records, plus the one being typed. Everything that *draws* a row reads
  // this — the grid, the cells, the reference uuids they resolve — because a draft
  // that rendered differently from a record would be a second grid rather than a
  // row at the end of one. Everything that *asks the server about* a row reads
  // `rows`: the draft's uuid names nothing, so it must never reach a request.
  const gridRows = useMemo(
    () => (newRow ? [...rows, newRowItem(newRow)] : rows),
    [rows, newRow],
  );
  // The rows the grid is *currently* showing, for the cell editor to open from.
  // PrimeReact hands its `editor` a snapshot of the row rather than the row, so
  // an editor opened after a poll would otherwise be seeded from what the record
  // held when its cell first rendered — see `InlineCellEditor`'s `row`. The draft
  // is in here for the same reason, one step sharper: its values live nowhere else.
  const rowByUuid = useMemo(
    () => new Map(gridRows.map((row) => [row.uuid, row])),
    [gridRows],
  );
  const views = useQuery({
    queryKey: ["views", listUuid],
    queryFn: () => api.get<View[]>(`/lists/${listUuid}/views/`),
  });
  const forms = useQuery({
    queryKey: ["forms", listUuid],
    queryFn: () => api.get<Form[]>(`/lists/${listUuid}/forms/`),
  });
  // The definitions only. Their *values* are fetched by MetricBar, keyed on the
  // view and search, because those are what the numbers are about.
  const metrics = useMetrics(listUuid);
  const metricList = metrics.data ?? [];

  // A list can be shared without its workspace, in which case this 403s and the
  // workspace simply drops out of the breadcrumb trail — there is nothing the
  // caller could navigate to anyway.
  const workspaceUuid = list.data?.workspace.uuid;
  const workspace = useQuery({
    queryKey: ["workspace", workspaceUuid],
    queryFn: () => api.get<Workspace>(`/workspaces/${workspaceUuid}`),
    enabled: !!workspaceUuid,
    retry: false,
  });
  const crumbs: Crumb[] = [
    { label: "Workspaces", to: "/workspaces" },
    ...(workspace.data
      ? [
          {
            label: workspace.data.display_name,
            to: `/workspaces/${workspace.data.uuid}`,
          },
        ]
      : []),
    { label: list.data?.display_name ?? "List" },
  ];

  // Records carry their own grants, so row-level controls are gated separately
  // from the list as a whole. View-level permissions are not fetched here any
  // more: the only thing this page gated on them was the filter button, and
  // filtering no longer touches the view. `ViewBar` still resolves them for
  // "Save view" and "Share view", which is where a view's permissions belong.
  const listActions = useEffectiveActions("list", listUuid);
  const canItem = useEffectiveActionsMap(
    "item",
    rows.map((item) => item.uuid),
    listActions,
  );

  // Default to the first view once loaded.
  useEffect(() => {
    if (!activeViewUuid && views.data && views.data.length > 0) {
      setActiveViewUuid(views.data[0].uuid);
    }
  }, [views.data, activeViewUuid]);

  // Apply the active view's saved config — on a *switch*, and only then.
  //
  // This used to depend on `views.data` as well, which is a new array identity after
  // any `invalidateQueries(["views", listUuid])`. Renaming the active view from
  // Settings → Views does exactly that, and so does `ViewBar`'s own `invalidate()`
  // after saving one. The effect then re-ran and reset every control on the toolbar
  // — filters, sort, hidden columns, column order, page size, grouping — throwing
  // away whatever the user had set up but not yet saved, which is precisely the
  // state "Save view" lights up to invite them to keep. The grid then refetched
  // unfiltered, so the work vanished and the rows changed under them with nothing
  // said.
  //
  // The seed belongs to a *change of view*, so the ref is what decides: re-seed when
  // the uuid this last ran for is not the active one. `views.data` is still read (a
  // switch can land before the list arrives, and the guard below returns until it
  // does) but no longer triggers.
  const seededViewUuid = useRef<string | null>(null);
  useEffect(() => {
    const view = views.data?.find((v) => v.uuid === activeViewUuid);
    if (!view) return;
    if (seededViewUuid.current === view.uuid) return;
    seededViewUuid.current = view.uuid;
    const sort = view.config.sorts?.[0];
    setSortField(sort ? `data.${sort.column_uuid}` : undefined);
    setSortOrder(sort ? (sort.direction === "asc" ? 1 : -1) : undefined);
    setPageSize(view.config.page_size ?? 25);
    setGroupBy(view.config.group_by?.column_uuid ?? null);
    setFilters(view.config.filters ?? EMPTY_FILTER);
    setHiddenColumns(view.config.hidden_columns ?? []);
    setColumnOrder(view.config.column_order ?? []);
    setSelection([]); // selection doesn't carry across views
  }, [activeViewUuid, views.data]);

  // The view, the search, the grouping and the filter are the *question*; changing
  // any of them asks a different one, and its page 3 has nothing to do with the old
  // one's. A mere refetch of the same question must not move you, which is why this
  // is keyed on those and not on the records that come back.
  useEffect(() => {
    setFirst(0);
  }, [activeViewUuid, q, groupBy, filters]);

  // Deleting the last records of the last page would otherwise leave `first` past
  // the end — an empty grid with the records still there, one page back. Land on
  // the new last page instead. Guarded on a loaded, non-empty result so it cannot
  // fight the reset above while a query is in flight.
  useEffect(() => {
    if (total > 0 && first >= total) {
      setFirst(Math.floor((total - 1) / pageSize) * pageSize);
    }
  }, [total, first, pageSize]);

  const deleteItem = useMutation({
    mutationFn: (uuid: string) => api.del(`/lists/${listUuid}/items/${uuid}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      toast.success("Record deleted");
    },
    onError: (err) =>
      toast.error(
        "Could not delete record",
        err instanceof ApiError ? err.message : undefined,
      ),
  });
  // A row of cells tabbed across is one edit gesture, not ten. Each write settles
  // its own record from the response, so what is left for a refetch is everything
  // a record cannot answer for on its own: whether it still matches the active
  // filter, where it now sorts, and what the metric cards above it add up to.
  // Those want one answer after the burst, not one per cell.
  const settleTimer = useRef<number | null>(null);
  const scheduleSettle = () => {
    if (settleTimer.current !== null) window.clearTimeout(settleTimer.current);
    settleTimer.current = window.setTimeout(() => {
      settleTimer.current = null;
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
    }, SETTLE_DELAY_MS);
  };
  useEffect(
    () => () => {
      if (settleTimer.current !== null) window.clearTimeout(settleTimer.current);
    },
    [],
  );
  // One cell, committed on its own. `PATCH /items/{uuid}` merges the keys it is
  // given onto the stored record, so the body is the one column that changed (plus
  // any lookup nested under it, which `cellPatch` clears) rather than the record.
  const saveCell = useMutation({
    mutationFn: (vars: { uuid: string; patch: Record<string, unknown> }) =>
      api.patch<ListItem>(`/lists/${listUuid}/items/${vars.uuid}`, {
        data: vars.patch,
      }),
    // Show the value the moment it is committed. Without this the cell falls back
    // to what it held until the write lands — a visible flick back to the old
    // value on every edit, which reads as the save having failed.
    //
    // Keyed on the *pages* rather than on `["items", listUuid]`, which is the
    // namespace the metric values share (see `recordKeys`): matching that would
    // hand this page-shaped updater a `MetricValues`, and `page.items.map` on an
    // object with no `items` throws — inside `onMutate`, which runs before
    // `mutationFn`, so the PATCH would never even be sent.
    onMutate: async (vars) => {
      // Any refetch already in flight has to be called off first. `useLiveList`
      // puts a `refetchInterval` on these pages, so a poll issued before this
      // edit can answer after the optimistic write and put the pre-edit value
      // back on screen — the very flick this optimistic write exists to prevent,
      // in a narrower window. It corrects itself when `onSuccess` lands, which is
      // why this was survivable rather than urgent; cancelling makes it not
      // happen. Awaiting is safe here in a way a throw would not be: `onMutate`
      // runs before `mutationFn`, so anything that rejects in it costs the PATCH.
      await queryClient.cancelQueries({ queryKey: recordPagesKey(listUuid) });
      // Snapshotted before the write so that a refusal can be taken back without
      // asking the server anything. A refetch cannot do that job: offline there
      // is nothing to refetch, and the value the server rejected would sit on
      // screen until the connection came back.
      const previous = queryClient.getQueriesData<ListItem[]>({
        queryKey: recordPagesKey(listUuid),
      });
      queryClient.setQueriesData<ListItem[]>(
        { queryKey: recordPagesKey(listUuid) },
        (rows) =>
          rows &&
          withItem(rows, vars.uuid, (item) => ({
            ...item,
            data: { ...item.data, ...vars.patch },
          })),
      );
      return { previous };
    },
    // The record the PATCH already answered with: the server's own rendering,
    // calculated columns recomputed and `onupdate` columns stamped. Refetching the
    // page to ask again would be a slower way to learn less than we already hold.
    onSuccess: (saved) => {
      queryClient.setQueriesData<ListItem[]>(
        { queryKey: recordPagesKey(listUuid) },
        (rows) => rows && withItem(rows, saved.uuid, () => saved),
      );
    },
    onError: (err, _vars, context) => {
      for (const [key, page] of context?.previous ?? []) {
        queryClient.setQueryData(key, page);
      }
      toast.error(
        "Could not save that value",
        err instanceof ApiError ? err.message : undefined,
      );
    },
    // What is left after the row has settled itself: whether it still belongs on
    // this page at all, and the totals above it. Debounced, because that question
    // has one answer per burst of edits rather than one per cell.
    onSettled: scheduleSettle,
  });
  /**
   * The draft row, posted as one record.
   *
   * Deliberately *not* optimistic, which is the one place this differs from
   * `saveCell` above. Where the new record belongs is the server's answer — the
   * filter, the sort, the grouping and the page are all applied there — so writing
   * it into the page cache would be this page guessing, and guessing wrong every
   * time an ascending sort puts a record made just now at the far end of the list.
   * The refetch *is* the feature.
   *
   * The whole `["items", listUuid]` namespace goes, not just the pages: the
   * paginator's total and the metric cards above the grid have all moved too.
   */
  const addRecord = useMutation({
    mutationFn: (data: Record<string, unknown>) =>
      api.post<ListItem>(`/lists/${listUuid}/items/`, { data }),
    onSuccess: async (saved) => {
      // A fresh draft rather than none: adding records is something you do several
      // of, and making the next one cost a trip back to "New record" is what makes
      // a grid feel like a form. The empty row is visibly a draft and its ✕ is
      // right there, so an unwanted one costs a click to be rid of.
      writeNewRow({});
      // Resolves once the refetches it triggered have landed, which is what makes
      // reading the page back on the next line mean anything.
      await queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      toast.success(
        "Record added",
        landedOnPage(queryClient.getQueryData<ListItem[]>(itemsKey), saved.uuid)
          ? undefined
          : "Not on this page — the current sort or filter puts it elsewhere.",
      );
    },
    // The draft stays on screen holding everything that was typed. That is the
    // whole reason adding is an explicit ✓ rather than something that happens when
    // you look away: a required column left empty, or a value that clashes with a
    // unique one, is fixable where you are standing.
    onError: (err) =>
      toast.error(
        "Could not add record",
        err instanceof ApiError ? err.message : undefined,
      ),
  });
  const setListInherit = useMutation({
    mutationFn: (value: boolean) =>
      api.patch(`/lists/${listUuid}`, { permissions_inherit: value }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["list", listUuid] });
      queryClient.invalidateQueries({ queryKey: ["effective"] });
    },
    onError: (err) =>
      toast.error(
        "Could not update inheritance",
        err instanceof ApiError ? err.message : undefined,
      ),
  });
  const setItemInherit = useMutation({
    mutationFn: (vars: { uuid: string; value: boolean }) =>
      api.patch(`/lists/${listUuid}/items/${vars.uuid}`, {
        permissions_inherit: vars.value,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      queryClient.invalidateQueries({ queryKey: ["effective"] });
    },
    onError: (err) =>
      toast.error(
        "Could not update inheritance",
        err instanceof ApiError ? err.message : undefined,
      ),
  });
  // A selection can mix rows the caller may delete with rows they may not (the
  // server ignores the latter anyway), so only send — and only count — the ones
  // that will actually go.
  const deletableSelection = selection.filter((item) =>
    canItem(item.uuid, "delete"),
  );
  const bulkDelete = useMutation({
    mutationFn: async () => {
      // The endpoint takes 500 uuids at a time. A selection carries across pages,
      // so six select-alls at a page size of 100 already exceed it — sending the
      // lot would come back a validation error naming a limit the person never
      // saw. Chunked instead, and sequentially: the cap is there to bound what one
      // request costs the server, which several at once would defeat.
      const uuids = deletableSelection.map((item) => item.uuid);
      let deleted = 0;
      for (let i = 0; i < uuids.length; i += BULK_MAX) {
        const res = await api.post<{ deleted: number }>(
          `/lists/${listUuid}/items/bulk-delete`,
          { uuids: uuids.slice(i, i + BULK_MAX) },
        );
        deleted += res.deleted;
      }
      return { deleted };
    },
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      setSelection([]);
      toast.success("Records deleted", `${res.deleted} record(s)`);
    },
    onError: (err) => {
      // An earlier chunk may already have gone, so the grid is refetched here too
      // rather than left showing rows that are no longer there.
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      toast.error(
        "Could not delete records",
        err instanceof ApiError ? err.message : undefined,
      );
    },
  });

  // Archive takes the same authority as delete — it takes the record away from
  // everyone who can read the list — so it filters the selection the same way and
  // chunks against the same cap, which the server shares between the two endpoints.
  const archiveItem = useMutation({
    mutationFn: (uuid: string) =>
      api.post(`/lists/${listUuid}/items/${uuid}/archive`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      queryClient.invalidateQueries({ queryKey: ["archived", listUuid] });
      toast.success("Record archived");
    },
    onError: (err) =>
      toast.error(
        "Could not archive record",
        err instanceof ApiError ? err.message : undefined,
      ),
  });
  const bulkArchive = useMutation({
    mutationFn: async () => {
      const uuids = deletableSelection.map((item) => item.uuid);
      let archived = 0;
      for (let i = 0; i < uuids.length; i += BULK_MAX) {
        const res = await api.post<{ archived: number }>(
          `/lists/${listUuid}/items/bulk-archive`,
          { uuids: uuids.slice(i, i + BULK_MAX) },
        );
        archived += res.archived;
      }
      return { archived };
    },
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      queryClient.invalidateQueries({ queryKey: ["archived", listUuid] });
      setSelection([]);
      toast.success("Records archived", `${res.archived} record(s)`);
    },
    onError: (err) => {
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      toast.error(
        "Could not archive records",
        err instanceof ApiError ? err.message : undefined,
      );
    },
  });

  const cols = columns.data ?? [];
  // Apply the view's column order first (for the columns it names), then any
  // remaining columns in their default position order (so new columns appear).
  const orderedCols: Column[] = (() => {
    const byUuid = new Map(cols.map((c) => [c.uuid, c]));
    const seen = new Set<string>();
    const result: Column[] = [];
    for (const uuid of columnOrder) {
      const c = byUuid.get(uuid);
      if (c) {
        result.push(c);
        seen.add(uuid);
      }
    }
    for (const c of cols) if (!seen.has(c.uuid)) result.push(c);
    return result;
  })();
  const visibleCols = orderedCols.filter(
    (c) => !hiddenColumns.includes(c.uuid),
  );

  // The reference uuids this page of records holds. The cells resolve them (one
  // shared request per column — see renderCell); the page only has to gather them,
  // so that the query key covers the whole page rather than a single row. Gathered
  // from `gridRows` so a lookup picked in the draft row shows its label rather than
  // the uuid that was stored for it.
  const principalUuids = useMemo(
    () => collectPrincipalUuids(cols, gridRows),
    [cols, gridRows],
  );
  const lookupUuids = useMemo(
    () => collectLookupUuids(cols, gridRows),
    [cols, gridRows],
  );

  const formList = forms.data ?? [];
  // Prefer the live row while it is still on the page, so the inheritance toggle
  // reflects what was just saved; fall back to the held copy once it is not.
  const sharingRow = sharingItem
    ? (rows.find((i) => i.uuid === sharingItem.uuid) ?? sharingItem)
    : null;
  // Editing uses the "update" form; creating uses the "create" form.
  const recordForm =
    formList.find(
      (f) => f.type === (recordDialog.item ? "update" : "create"),
    ) ?? null;

  // Reading one record uses the "view" form. Its absence hides the action rather
  // than falling back to "show every column": GET /forms/ already filters by the
  // caller's `view` on each form, so a view form un-shared from someone must not
  // reappear as an unconfigured one — that would route around the grant.
  const viewForm = formList.find((f) => f.type === "view") ?? null;

  // Editing a cell is editing a record, so the update form decides which columns
  // may be written and which are read-only — the same answer the Edit dialog
  // gets, from the same place. A list with no update form gets the default: every
  // column except the calculated ones, which the formula writes rather than you.
  const updateForm = formList.find((f) => f.type === "update") ?? null;
  const editKinds = useMemo(
    () => cellEditKinds(resolveFields(cols, updateForm)),
    [cols, updateForm],
  );

  /**
   * What clicking a row opens, or null when the caller may do neither.
   *
   * The grid's rows are clickable (see `selectionMode` on the DataTable), and
   * this is the same choice the row's first two actions offer, in the same
   * order — so the row and the buttons at the end of it can never disagree
   * about what "open this record" means. Null also decides whether the row is
   * *drawn* as clickable, so a viewer who can only read a row it cannot open
   * does not get a pointer cursor promising something that will not happen.
   *
   * While "Edit cells" is on, nothing: a click has become the gesture that opens
   * the cell under it, and a row that also opened a dialog would spend the same
   * click twice. The row's own actions still open the record either way, which is
   * what keeps the reading surfaces reachable in edit mode.
   */
  const rowOpener = (row: ListItem) =>
    inlineEdit
      ? null
      : viewForm
        ? () => setViewItem(row)
        : canItem(row.uuid, "edit")
          ? () => setRecordDialog({ open: true, item: row })
          : null;

  /**
   * A cell edit, on its way to the server — or into the record being typed.
   *
   * PrimeReact calls this when the cell leaves edit mode by any route that means
   * "keep it" — Enter, Tab, a click elsewhere — and never on Escape, which is what
   * makes Escape a cancel.
   *
   * Everything it needs is in the draft, and deliberately so. PrimeReact keys its
   * body cells by row *index*, so a poll that reorders or repages the rows leaves
   * an open editor sitting where a different record now is; the event's row is
   * that different record. The draft knows which record and which column it was
   * typed into, and a row moving on screen is no reason to throw away what
   * somebody typed.
   *
   * The draft row goes nowhere: there is no record to PATCH yet, so the same patch
   * `cellPatch` builds is merged into what will be posted when the row is added.
   */
  function commitCell() {
    const draft = cellDraft.current;
    cellDraft.current = null;
    if (!draft) return;
    // The one thing a draft cannot outlive: the column it belongs to being
    // deleted while its editor was open. There is nothing left to patch.
    const column = cols.find((c) => c.uuid === draft.columnUuid);
    if (!column) return;
    const patch = cellPatch(cols, column, draft);
    if (!patch) return;
    if (isNewRow(draft.rowUuid)) {
      writeNewRow({ ...(newRowRef.current ?? {}), ...patch });
      return;
    }
    saveCell.mutate({ uuid: draft.rowUuid, patch });
  }

  // A QR code always creates, so it is always the "create" form it fills — which
  // is what decides the fields it may write, and their names. Resolving them here
  // (rather than reading `cols`) is what stops a code from filling a column the
  // form excludes or marks read-only.
  //
  // The draft row asks the same form, for the same reason: it is a record being
  // created, and a column the create form excludes has no cell to type into.
  const createForm = formList.find((f) => f.type === "create") ?? null;
  const createFields = useMemo(
    () => resolveFields(cols, createForm),
    [cols, createForm],
  );
  const createKinds = useMemo(() => cellEditKinds(createFields), [createFields]);

  /** The draft as the API takes it: create semantics, so an untouched cell is
   *  omitted rather than nulled and the column's default still applies. The values
   *  are already in stored form, which is why `buildRecordData` serves here as it
   *  does for the dialog — its serialize pass has nothing left to do. */
  const newRowData = () =>
    buildRecordData(createFields, newRowRef.current ?? {}, false);

  function submitNewRow() {
    // A cell editor may still be open — clicking ✓ is an outside click, and
    // whether PrimeReact closes it before or after this handler runs is not ours
    // to decide. Flushing it here settles that either way: `commitCell` writes
    // through `newRowRef` synchronously, and a second call is a no-op.
    commitCell();
    const data = newRowData();
    if (Object.keys(data).length === 0) return; // nothing typed; ✓ is disabled anyway
    addRecord.mutate(data);
  }

  /**
   * The draft, handed to the dialog that has room for it.
   *
   * Two things need this. A rich-text or multiline column has no cell-sized editor
   * (`cellEditKinds` calls those "dialog"), and a **required** column that the
   * create form excludes, or that is hidden from the grid, has no cell at all — so
   * ✓ would refuse the record with no way to answer it from here. Either way the
   * escape is the same: the form, already holding what was typed.
   *
   * The record **moves** rather than being copied: the draft row goes, and the
   * dialog owns what was typed from here on. Leaving it in both places would leave
   * two surfaces each able to create the same record, and Cancel in a dialog
   * already means "not this record" everywhere else in the app.
   *
   * `prefill` is deserialized, because that is what the dialog's fields expect; the
   * draft holds stored values so that the *grid* can render it.
   */
  function openNewRowInForm() {
    commitCell();
    const stored = newRowRef.current ?? {};
    const prefill: Record<string, unknown> = {};
    for (const column of cols) {
      const raw = stored[column.uuid];
      if (raw !== undefined && raw !== null) {
        prefill[column.uuid] = deserialize(column, raw);
      }
    }
    writeNewRow(null);
    setRecordDialog({ open: true, item: null, prefill });
  }

  /** Give up on the row being typed. Anything in it asks first — it is the one
   *  gesture here that destroys work, and it sits a few pixels from ✓. */
  function discardNewRow() {
    if (Object.keys(newRowData()).length === 0) {
      writeNewRow(null);
      return;
    }
    confirmDialog({
      message: "Discard the record you were adding?",
      header: "Discard record",
      icon: "pi pi-exclamation-triangle",
      accept: () => writeNewRow(null),
    });
  }

  /**
   * The draft row's own actions: send it, throw it away, or move it somewhere with
   * more room.
   *
   * ✓ and ✕ stay inline at every width, unlike a record's, which collapse into a
   * "⋮" on a narrow grid — they are the only way to finish the gesture, and a
   * commit behind a menu is not one. ✓ is dead until something has been typed,
   * because a record of nothing but defaults is not what the click meant.
   */
  function newRowActions() {
    return (
      <RowActions
        size="small"
        maxVisible={2}
        actions={[
          {
            key: "add",
            icon: "pi pi-check",
            label: "Add record",
            disabled:
              addRecord.isPending || Object.keys(newRowData()).length === 0,
            onClick: submitNewRow,
          },
          {
            key: "discard",
            icon: "pi pi-times",
            label: "Discard new record",
            onClick: discardNewRow,
          },
          {
            key: "form",
            icon: "pi pi-external-link",
            label: "Open in the record form",
            onClick: openNewRowInForm,
          },
        ]}
      />
    );
  }

  /** Leaving "Edit cells" takes the draft with it, so it asks on the way out for
   *  the same reason ✕ does. */
  function toggleInlineEdit() {
    if (inlineEdit && newRow !== null) {
      const drop = () => {
        writeNewRow(null);
        setInlineEdit(false);
      };
      if (Object.keys(newRowData()).length === 0) {
        drop();
        return;
      }
      confirmDialog({
        message: "Discard the record you were adding?",
        header: "Discard record",
        icon: "pi pi-exclamation-triangle",
        accept: drop,
      });
      return;
    }
    setInlineEdit((on) => !on);
  }

  function openQr(mode: "fill" | "submit") {
    setQrError(null);
    setQrMode(mode);
  }

  /**
   * A scanned code, into the Add-record form.
   *
   * This is the "fill" flow only — one code, checked by a person before it is
   * saved. Submitting outright is a session rather than a scan (presets, a queue,
   * a tally), and lives in `QrSubmitSession`.
   */
  async function handleQrScan(text: string) {
    setQrBusy(true);
    setQrError(null);
    try {
      const payload = parseQrPayload(text);
      const fill = await fillFromQr(listUuid, createFields, payload);
      if (fill.filled.length === 0 && fill.issues.length === 0) {
        setQrError("This QR code has no fields for this list.");
        return;
      }
      setRecordDialog({
        open: true,
        item: null,
        prefill: fill.values,
        notes: fill.issues.map((issue) => `${issue.key}: ${issue.message}`),
      });
      setQrMode(null);
    } catch (err) {
      setQrError(
        err instanceof Error ? err.message : "Could not read that QR code.",
      );
    } finally {
      setQrBusy(false);
    }
  }

  const moreMenu = useRef<Menu>(null);

  const activeView = views.data?.find((v) => v.uuid === activeViewUuid) ?? null;
  const filterCount = filters.conditions.length;

  /**
   * What stands in for the rows when there are none — computed once, rendered by
   * *both* layouts.
   *
   * "Records have two renderers, and they must never disagree" (known-gaps): the
   * grid distinguished a search with no hits, a filter hiding everything, and a
   * genuinely empty list, and offered a way out of the first — while `RecordCards`,
   * which is the whole phone experience, said "No records yet" to all three and to
   * a failed fetch besides. Rather than copy the ladder into the cards and let the
   * copies drift the way `sortRows` once did, the page decides once and hands the
   * same node to both. Anything added here reaches the phone for free.
   */
  const emptyContent = items.isError ? (
    <QueryError
      what="records"
      error={items.error}
      onRetry={() => void items.refetch()}
    />
  ) : (
    <EmptyState
      icon={q.trim() ? "pi pi-search" : "pi pi-inbox"}
      title={q.trim() ? "No matching records" : "No records yet"}
      text={
        q.trim()
          ? `Nothing in this list matches “${q.trim()}”.`
          : filterCount > 0
            ? "This view's filter is hiding everything in the list."
            : listActions.has("create")
              ? "Add your first record with the button above."
              : undefined
      }
      action={
        q.trim() ? (
          <Button
            label="Clear search"
            icon="pi pi-times"
            text
            onClick={() => setSearch("")}
          />
        ) : undefined
      }
    />
  );
  const groupColumn = groupBy
    ? (cols.find((c) => c.uuid === groupBy) ?? null)
    : null;
  const currentConfig: ViewConfig = {
    sorts: sortField
      ? [
          {
            column_uuid: sortField.replace(/^data\./, ""),
            direction: sortOrder === 1 ? "asc" : "desc",
          },
        ]
      : [],
    page_size: pageSize,
    filters,
    group_by: groupBy ? { column_uuid: groupBy } : null,
    hidden_columns: hiddenColumns,
    column_order: columnOrder,
  };

  /**
   * Has the page drifted from what the active view has stored?
   *
   * Both sides go through the same defaults before they are compared, because a
   * view saved before `group_by` existed has no key for it while the page always
   * has `null` — and an absent field and a field set to its default are the same
   * arrangement.
   *
   * Filters are in here now. They used to be excluded because the dialog wrote them
   * straight to the view, so the two could not differ by construction; now that a
   * filter is a question you ask rather than a change you make, "Save view" is what
   * turns it into one, and it has to light up when you have asked something new.
   *
   * **Every nested object is rebuilt field by field, never compared as it arrived.**
   * `JSON.stringify` is order-sensitive and the two sides do not agree on an order:
   * a stored config comes back from JSONB with its keys in whatever order Postgres
   * holds them (`{direction, column_uuid}`), while the page builds its own
   * (`{column_uuid, direction}`). Comparing those directly reported a change on
   * every render, which pinned "Save view" to the toolbar of any view with a saved
   * sort — the exact always-on button the dirty flag was added to get rid of.
   */
  function fingerprint(config: ViewConfig | null | undefined): string {
    return JSON.stringify({
      sorts: (config?.sorts ?? []).map((s) => [s.column_uuid, s.direction]),
      page_size: config?.page_size ?? 25,
      group_by: config?.group_by?.column_uuid ?? null,
      hidden_columns: config?.hidden_columns ?? [],
      column_order: config?.column_order ?? [],
      filters: {
        conjunction: config?.filters?.conjunction ?? "and",
        conditions: (config?.filters?.conditions ?? []).map((c) => [
          c.column_uuid,
          c.operator,
          c.value ?? null,
        ]),
      },
    });
  }
  const viewDirty =
    !!activeView && fingerprint(currentConfig) !== fingerprint(activeView.config);

  function toggleColumn(uuid: string, visible: boolean) {
    setHiddenColumns((prev) =>
      visible ? prev.filter((u) => u !== uuid) : [...new Set([...prev, uuid])],
    );
  }
  // Materialize the full effective order, move one entry, and store it — so
  // column_order is always a complete ordering after any manual reorder.
  //
  // `moveTo` splices rather than swapping. That is invisible to the chevrons,
  // which only ever move one place, and it is the whole point for a drag, which
  // moves a column past several others at once.
  function reorderColumn(from: number, to: number) {
    setColumnOrder(moveTo(orderedCols.map((c) => c.uuid), from, to));
  }

  // One object, handed to both the toolbar and List settings → Views, so the two
  // drive the same state rather than each keeping their own.
  const viewControls = {
    columns: orderedCols,
    hiddenColumns,
    onToggleColumn: toggleColumn,
    onReorderColumn: reorderColumn,
    groupBy,
    onGroupBy: setGroupBy,
    filterCount,
    onOpenFilter: () => setShowFilter(true),
    // Filtering is now a way of reading, not a change to the view, so anyone who
    // can see the records can narrow them. `configure` still guards *saving* the
    // filter onto the view, which is where it belongs — that is the part that
    // changes what everybody else sees.
    canFilter: true,
  };

  // No client-side sort for either layout any more: the server returns the page
  // already ordered. Sorting here could only reorder the rows on screen relative
  // to the rest of the list, which is the bug this replaced.

  function groupHeader(row: ListItem) {
    if (!groupColumn) return null;
    // The draft belongs to no group: it has no record behind it, and a subheader
    // reading "Class: —" would announce a group that does not exist. Which group it
    // *will* join is the server's answer once it is added, like everything else
    // about where it lands.
    if (isNewRow(row.uuid)) {
      return <span className="font-semibold">New record</span>;
    }
    return (
      <span className="font-semibold">
        {groupColumn.display_name}:{" "}
        {renderCell(groupColumn, row, listUuid, principalUuids, lookupUuids)}
      </span>
    );
  }

  function confirmDeleteItem(row: ListItem) {
    confirmDialog({
      message: "Delete this record?",
      header: "Confirm",
      icon: "pi pi-exclamation-triangle",
      accept: () => deleteItem.mutate(row.uuid),
    });
  }
  function confirmBulkDelete() {
    confirmDialog({
      message: `Delete ${deletableSelection.length} selected record(s)?`,
      header: "Confirm",
      icon: "pi pi-exclamation-triangle",
      accept: () => bulkDelete.mutate(),
    });
  }
  // One row, same question. It was the only destructive row action that fired on
  // the click — delete confirms, bulk delete confirms, bulk archive confirms — and
  // under 1280px `maxVisible` collapses the row actions into a kebab where Archive
  // sits directly above Delete, so the unconfirmed one is the one under the mis-tap.
  // Archiving takes the record away from everyone who can read the list, and the
  // way back is to go looking through the Archived records dialog.
  function confirmArchiveItem(uuid: string) {
    confirmDialog({
      message:
        'Archive this record? It stays in "Archived records" and can be brought ' +
        "back at any time.",
      header: "Archive record",
      icon: "pi pi-box",
      accept: () => archiveItem.mutate(uuid),
    });
  }
  // Archiving is reversible and destroys nothing, so the message says where the
  // records go rather than warning about what is lost.
  function confirmBulkArchive() {
    confirmDialog({
      message: `Archive ${deletableSelection.length} selected record(s)? They stay in "Archived records" and can be brought back at any time.`,
      header: "Archive records",
      icon: "pi pi-box",
      accept: () => bulkArchive.mutate(),
    });
  }

  return (
    <div className="flex flex-col gap-4">
      <ConfirmDialog />

      <PageHeader
        crumbs={crumbs}
        title={list.data?.display_name ?? "List"}
        subtitle={
          cols.length === 0
            ? undefined
            : `${total} record${total === 1 ? "" : "s"} · ${
                cols.length
              } column${cols.length === 1 ? "" : "s"}`
        }
        actions={
          <>
            {/* Columns, permissions and forms all configure the *list*, so they
                live together behind one button; the toolbar is then about the
                data. */}
            <Button
              data-tour="list-settings"
              label="Settings"
              icon="pi pi-cog"
              outlined
              onClick={() => setShowSettings(true)}
              disabled={!list.data}
            />
            {/* Import/export and the bin are occasional and belong to the list
                rather than to what you are looking at, so they fold into an
                overflow rather than standing as two more outlined buttons the
                width of their labels. It sits between the secondary controls
                and the primary one, which stays last. */}
            <Button
              data-tour="list-more"
              icon="pi pi-ellipsis-h"
              text
              rounded
              aria-label="More list actions"
              aria-haspopup
              onClick={(e) => moreMenu.current?.toggle(e)}
            />
            <Menu
              popup
              ref={moreMenu}
              model={[
                {
                  label: "Import / Export",
                  icon: "pi pi-file-import",
                  disabled: cols.length === 0,
                  command: () => setShowTransfer(true),
                },
                // Both holding surfaces are gated on list-level delete: a deleted
                // record has no per-item permission to check, and an archive is
                // filled by whoever may take records out of the list.
                ...(listActions.has("delete")
                  ? [
                      {
                        label: "Archived records",
                        icon: "pi pi-box",
                        disabled: cols.length === 0,
                        command: () => setShowArchived(true),
                      },
                      {
                        label: "Deleted records",
                        icon: "pi pi-trash",
                        disabled: cols.length === 0,
                        command: () => setShowDeleted(true),
                      },
                    ]
                  : []),
              ]}
            />
            {/* The button still adds a record; the dropdown is the same act with
                a QR code doing the typing. */}
            {listActions.has("create") && (
              <SplitButton
                data-tour="add-record"
                label="Add record"
                icon="pi pi-plus"
                onClick={() => setRecordDialog({ open: true, item: null })}
                disabled={cols.length === 0}
                model={[
                  {
                    label: "Fill using QR code",
                    icon: "pi pi-qrcode",
                    command: () => openQr("fill"),
                  },
                  {
                    label: "Submit using QR code",
                    icon: "pi pi-qrcode",
                    command: () => openQr("submit"),
                  },
                ]}
              />
            )}
          </>
        }
      />

      {/* One bar, and everything on it changes what you are looking at rather
          than what the list *is*. None of it depends on the list having views: a
          list whose views were all deleted still has its records, and used to lose
          the search box — and, briefly, the sort control — along with them.
          Filtering, grouping and column visibility joined them here once they
          became this page's state rather than the view's. A list with no views
          could previously not be filtered, grouped or narrowed to a few columns at
          all, and offered no way to make a view either, since the button that adds
          one lives on the bar that was missing. The only thing left that needs a
          view is choosing between them, and that is `ViewBar`'s own business. */}
      <div className="toolbar" data-tour="list-toolbar">
        <ViewBar
          listUuid={listUuid}
          views={views.data ?? []}
          activeUuid={activeViewUuid}
          onSelect={setActiveViewUuid}
          currentConfig={currentConfig}
          dirty={viewDirty}
        />
        {/* Only between two groups that are both there. With no views `ViewBar` is
            a single "New view" button, or nothing at all without `configure`, and
            a divider would be marking a boundary that has one side. */}
        {views.data && views.data.length > 0 && (
          <span className="toolbar-divider" aria-hidden />
        )}
        <ViewControls {...viewControls} />

        {/* Sorting otherwise lives in the grid's column headers, which a card
            list does not have — so on a phone there was no way to sort at all.
            It drives the same sortField/sortOrder the headers do, so the two
            agree and a header click moves this control. Grouping pins the order
            to the group column, which is why it disables. */}
        <SortControl
          columns={orderedCols}
          sortField={sortField}
          sortOrder={sortOrder}
          onChange={(field, order) => {
            setSortField(field);
            setSortOrder(order);
          }}
          disabled={cols.length === 0 || !!groupColumn}
          disabledReason={
            groupColumn
              ? `Records are ordered by ${groupColumn.display_name} while grouped`
              : undefined
          }
        />

        {/* Reading is the default and editing is the mode you ask for, not the
            other way round: a grid whose cells open an editor on every stray
            click is one you cannot skim. Offered only where there is a grid to
            edit — a phone renders cards, which have no cells — and only to
            someone with a record on this page they may actually write to, or the
            right to add one, which is a use for the mode on a list with no rows in
            it yet. It survives its own condition while on, so turning it off never
            depends on a filter that has hidden every editable row. */}
        {!small &&
          (inlineEdit ||
            listActions.has("create") ||
            rows.some((row) => canItem(row.uuid, "edit"))) && (
            <Button
              label="Edit cells"
              icon="pi pi-pencil"
              text
              className={inlineEdit ? "is-on" : undefined}
              aria-pressed={inlineEdit}
              onClick={toggleInlineEdit}
            />
          )}

        {/* Off until asked for. A total is an occasional question, not something
            you need in front of you while reading rows — and revealing it is what
            starts the scan behind it, so a list nobody is totalling pays nothing.
            The badge says how many are waiting, since a hidden strip is otherwise
            indistinguishable from a list with no metrics at all. */}
        {metricList.length > 0 && (
          <Button
            label="Metrics"
            icon="pi pi-chart-bar"
            text
            className={showMetrics ? "is-on" : undefined}
            badge={showMetrics ? undefined : String(metricList.length)}
            aria-pressed={showMetrics}
            onClick={() => setShowMetrics((on) => !on)}
          />
        )}

        {/* Last on the bar and pushed to its end, so on a narrow window it wraps
            onto a line of its own rather than squeezing everything else. */}
        <div className="w-full sm:w-64 sm:ml-auto">
          <SearchInput
            value={search}
            onChange={setSearch}
            placeholder="Search records…"
            disabled={cols.length === 0}
          />
        </div>
      </div>

      {/* The schema failing to load and the list genuinely having no columns are
          the same `cols.length === 0` and must not read the same. A 403 here — the
          shape an item-only grantee gets, and what a revoked share looks like —
          used to render as the cheerful "add them under Settings" invitation. */}
      {columns.isError ? (
        <QueryError
          what="the columns"
          error={columns.error}
          onRetry={() => void columns.refetch()}
        />
      ) : (
        cols.length === 0 &&
        !columns.isLoading && (
          <Message
            severity="info"
            text={
              listActions.has("configure")
                ? "This list has no columns yet. Add them under Settings → Columns, then add records."
                : "This list has no columns yet."
            }
          />
        )
      )}

      {/* Only for someone who can actually archive, matching the gate on the
          archive dialog and the Archive actions. `total` is keyed on the scope, so
          the hint reads it only when the scope is the whole list — see ArchiveHint. */}
      {listActions.has("delete") && (
        <ArchiveHint
          listUuid={listUuid}
          total={count.data?.total}
          scopeIsWholeList={isWholeListScope(
            q,
            filters,
            (activeView?.config?.filters?.conditions?.length ?? 0) > 0,
          )}
          onOpenArchive={() => setShowArchived(true)}
        />
      )}

      {/* Above the records, because a total is what the records add up to.
          Hidden until the toolbar's Metrics button asks for it. */}
      <MetricBar
        listUuid={listUuid}
        metrics={metricList}
        viewUuid={activeViewUuid}
        q={q}
        filters={filters}
        visible={showMetrics}
      />


      {small ? (
        <RecordCards
          listUuid={listUuid}
          items={rows}
          columns={visibleCols}
          loading={columns.isLoading || items.isLoading || count.isLoading}
          empty={emptyContent}
          principalUuids={principalUuids}
          lookupUuids={lookupUuids}
          selection={selection}
          onSelectionChange={setSelection}
          groupColumn={groupColumn}
          first={first}
          pageSize={pageSize}
          total={total}
          onPage={(nextFirst, nextRows) => {
            setFirst(nextFirst);
            setPageSize(nextRows);
          }}
          can={canItem}
          onView={viewForm ? (item) => setViewItem(item) : undefined}
          onEdit={(item) => setRecordDialog({ open: true, item })}
          onShare={(item) => setSharingItem(item)}
          onQr={(item) => setQrItems([item])}
          onDelete={confirmDeleteItem}
        />
      ) : (
        <DataTable
          value={gridRows}
          loading={columns.isLoading || items.isLoading || count.isLoading}
          dataKey="uuid"
          // Bounds the wrapper's height so the header can stick to the top of it
          // and the paginator stays on screen — see `.data-grid` in themes.css.
          className="data-grid"
          // The table's wrapper scrolls (see `.p-datatable > .p-datatable-wrapper`);
          // left alone the table would instead squash itself to whatever width it is
          // given, so a dozen columns become a dozen slivers. A floor per column
          // makes it overflow — and therefore scroll — rather than crush. Below
          // 640px it is not a table at all (see RecordCards); this is what carries a
          // tablet, where a table still makes sense but the columns may not fit.
          //
          // 8rem a column, not 10: the desktop sidebar takes 17rem off the page, and
          // at the old floor a perfectly ordinary six-column list overflowed by an
          // inch — enough to push the row actions off the edge, not enough for
          // anyone to notice the grid had become scrollable. Added to that are the
          // two columns that are not data — the 3rem selection box and the actions,
          // which narrow when they collapse to a kebab — plus a rem of slack.
          tableStyle={{
            minWidth: `${visibleCols.length * 8 + actionsWidth + 4}rem`,
          }}
          // "checkbox", not "multiple": both give the same multi-select through
          // the column of checkboxes, but "multiple" also makes a click anywhere
          // in a row toggle that row's selection, which spends the gesture a row
          // is most often clicked *for*. Confined to the checkbox, the row itself
          // is free to open the record — see `onRowClick` below.
          selectionMode="checkbox"
          selection={selection}
          onSelectionChange={(e) => setSelection(e.value)}
          // The draft row has no record behind it, so it has nothing to select:
          // every gesture a selection leads to — archive, delete — names uuids at
          // the server, and this one names nothing. Also keeps "select all" from
          // silently including it.
          isDataSelectable={(e) => !isNewRow((e.data as ListItem).uuid)}
          // Set only while the mode is on: with no edit mode a column's `editor`
          // is inert, so this is the single switch that turns a grid of values
          // into a grid of cells. "cell", not "row" — one value is committed at a
          // time, which is what a partial PATCH per cell already means.
          editMode={inlineEdit ? "cell" : undefined}
          // PrimeReact memoises every body cell against a fixed prop list
          // (datatable.cjs.js:493 — `rowData`, `field`, `rowIndex`, `editing`, and
          // eight more) that does not include the column, so a cell holds on to the
          // `canItem` closure it first rendered with. When the effective-actions
          // query resolves after the grid has drawn, cells that just became editable
          // never gain the affordance and their `onBeforeCellEditShow` still answers
          // `false` on rows this caller may in fact write.
          //
          // The memo cannot be narrowed into correctness: adding `column` to
          // `cellMemoProps` compares a React element rebuilt every render, which
          // never matches, so the memo would be off in all but name. Off honestly
          // is the smaller lie, and it also covers the case gating on the query
          // would miss — a permission changed through the Share dialog mid-session.
          //
          // **Making `canItem` referentially stable does not change this**, and it
          // is worth saying so because it looks like it should. `useEffectiveActionsMap`
          // now memoises the predicate, so it only changes identity when the answer
          // does — but the memo above compares *props*, not closures, and none of
          // those twelve moves when permissions resolve. The cell would simply not
          // re-render, which is the same staleness by a shorter route.
          //
          // The stable predicate is still worth having, but modestly, and the claim
          // is worth keeping honest: `useEffectiveActionsMap` is called once here,
          // not per cell. A cell only *calls* the predicate it returns, which is a
          // `Map.get`. So what memoising saves is one Map of page-size per render of
          // this component — not one per cell. The real win on this branch is the
          // lookup label memo, which genuinely was per cell.
          cellMemo={false}
          // PrimeReact calls this before applying its own selection logic, and
          // skips it entirely when the click landed on something clickable — so
          // the checkbox and the action buttons keep their own behaviour without
          // needing a guard here (RowActions stops propagation regardless).
          onRowClick={(e) => rowOpener(e.data as ListItem)?.()}
          rowClassName={(row: ListItem) =>
            isNewRow(row.uuid)
              ? "grid-row-new"
              : rowOpener(row)
                ? "grid-row-opens"
                : ""
          }
          stripedRows
          // `lazy`: the grid holds one page, so it must not try to slice or sort
          // what it was given — it reports the page and the order it wants and the
          // server answers with exactly those rows. `totalRecords` is the whole
          // matching set, which is what makes the page links reach past this page.
          lazy
          paginator
          first={first}
          rows={pageSize}
          totalRecords={total}
          rowsPerPageOptions={[10, 25, 50, 100]}
          onPage={(e) => {
            setFirst(e.first);
            setPageSize(e.rows);
          }}
          sortMode="single"
          sortField={groupColumn ? `data.${groupBy}` : sortField}
          sortOrder={groupColumn ? 1 : sortOrder}
          onSort={(e: DataTableSortEvent) => {
            if (groupColumn) return; // sort is pinned to the group column
            setSortField(e.sortField);
            setSortOrder(e.sortOrder);
            // A new order renumbers every row, so "page 3" of the old one means
            // nothing. Back to the first page, as a fresh search does.
            setFirst(0);
          }}
          rowGroupMode={groupColumn ? "subheader" : undefined}
          groupRowsBy={groupColumn ? `data.${groupBy}` : undefined}
          rowGroupHeaderTemplate={groupColumn ? groupHeader : undefined}
          emptyMessage={emptyContent}
          // The way in to a new row, at the end of the rows — where the next one
          // would go. Only while the mode is on, because outside it the grid is for
          // reading and "Add record" in the header is the way to make one; and only
          // when there is no draft already, since the draft row's own ✓ and ✕ are
          // the affordance from then on.
          footer={
            inlineEdit &&
            listActions.has("create") &&
            cols.length > 0 &&
            newRow === null ? (
              <Button
                label="New record"
                icon="pi pi-plus"
                text
                size="small"
                onClick={() => writeNewRow({})}
              />
            ) : undefined
          }
        >
          <DataColumn
            selectionMode="multiple"
            headerStyle={{ width: "3rem" }}
            // Pinned to the left edge. On a list wide enough to scroll sideways
            // the box that selects a row was reachable only at the start of that
            // scroll — the same complaint as the actions, mirrored.
            className="grid-pin-start"
            headerClassName="grid-pin-start"
            exportable={false}
          />
          {visibleCols.map((col) => {
            // Everything is "locked" while the mode is off, which is how a column
            // that is editable in principle still renders as a plain value.
            //
            // Per row rather than per column, because the draft row is a record
            // being *created* and asks the create form, while every row above it is
            // a record being edited and asks the update form. A list may well
            // differ on the two — a column you fill in once and never touch again is
            // exactly the case forms exist to express.
            const kindFor = (row: ListItem): CellEditKind =>
              !inlineEdit
                ? "locked"
                : isNewRow(row.uuid)
                  ? (createKinds.get(col.uuid) ?? "locked")
                  : (editKinds.get(col.uuid) ?? "locked");
            // One answer for the outline, the cell body and the gate on opening an
            // editor. Asked separately they could disagree about the same cell —
            // a cell drawn as editable that refuses to open, or the reverse.
            //
            // The draft row is gated by the mode itself: the footer that offers it
            // appears only with list `create`, and there is no record yet for a
            // per-record grant to speak about.
            const isEditable = (row: ListItem) =>
              kindFor(row) !== "locked" &&
              (isNewRow(row.uuid) || canItem(row.uuid, "edit"));
            return (
              <DataColumn
                key={col.uuid}
                field={`data.${col.uuid}`}
                // Calculated columns sort like any other now: the value is stored in
                // the record, so the server orders the whole list by it rather than
                // there being nothing in the database to order by. (A header that
                // sorted only the page on screen would be the bug this page lost.)
                sortable={!groupColumn}
                // Deleting a column moved into List settings: a trash button on every
                // header crowded out the thing the grid is for.
                header={col.display_name}
                // What a click here will do, said before it is clicked. A cell that
                // will not open — a read-only field, a calculated column, a record
                // this caller may read but not write — stays flat, and that
                // flatness is the whole answer to "why won't this one edit?".
                bodyClassName={(row: ListItem) =>
                  !isEditable(row)
                    ? ""
                    : kindFor(row) === "inline"
                      ? "cell-editable"
                      : "cell-opens-record"
                }
                body={(row: ListItem) => {
                  const kind = kindFor(row);
                  const editing = isEditable(row);
                  const cell = renderCell(
                    col,
                    row,
                    listUuid,
                    principalUuids,
                    lookupUuids,
                    "compact",
                    kind === "inline" && editing,
                  );
                  // Rich text and multiline have no cell-sized editor, so the
                  // click goes where their editor lives instead of doing nothing.
                  // A real button, not a bare span: the column deliberately has no
                  // `editor`, so PrimeReact renders no tab stop for it, and the row
                  // click is off while the mode is on — leaving this the only route
                  // to those columns, and until now a mouse-only one.
                  if (kind !== "dialog" || !editing) return cell;
                  // On the draft row the record does not exist yet, so what opens
                  // is the Add form, holding everything typed into the row so far.
                  const openRecord = () =>
                    isNewRow(row.uuid)
                      ? openNewRowInForm()
                      : setRecordDialog({ open: true, item: row });
                  const hint = `Edit ${col.display_name} in the record's own form`;
                  return (
                    <span
                      role="button"
                      tabIndex={0}
                      aria-label={hint}
                      title={hint}
                      onClick={openRecord}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault(); // Space would scroll the grid
                          openRecord();
                        }
                      }}
                    >
                      {cell}
                    </span>
                  );
                }}
                // Present whenever *either* form opens this column in place —
                // PrimeReact renders no editor at all for a column without one, so
                // this cannot be decided per row. `onBeforeCellEditShow` below is
                // the per-row gate, and it is the one that has to be right.
                editor={
                  editKinds.get(col.uuid) === "inline" ||
                  createKinds.get(col.uuid) === "inline"
                    ? (options) => {
                        // `rowData` is trusted for the record's identity and for
                        // nothing else; the values come from the cache.
                        const snapshot = options.rowData as ListItem;
                        return (
                          <InlineCellEditor
                            listUuid={listUuid}
                            column={col}
                            columns={cols}
                            row={rowByUuid.get(snapshot.uuid) ?? snapshot}
                            onDraft={(draft) => {
                              cellDraft.current = draft;
                            }}
                          />
                        );
                      }
                    : undefined
                }
                // PrimeReact types this as returning nothing, but its cell click
                // handler blocks the editor when it returns exactly `false` — the
                // one hook that decides *per row*, which is the grain permissions
                // come at. Without it a record you may read but not edit would open
                // an editor and refuse the save afterwards.
                onBeforeCellEditShow={(e) =>
                  isEditable(e.rowData as ListItem) ? undefined : false
                }
                onCellEditComplete={() => commitCell()}
                // Escape. The next editor to open reclaims the draft slot anyway,
                // but an abandoned value has no business outliving the cell it was
                // typed into.
                onCellEditCancel={() => {
                  cellDraft.current = null;
                }}
              />
            );
          })}
          <DataColumn
            header=""
            style={{ width: `${actionsWidth}rem` }}
            // Pinned to the right edge, not moved to the left: a column wide
            // enough to scroll past used to carry these buttons off the end, but
            // moving them merely mirrors that — scroll right to read the twelfth
            // column and they would be off the *left*. Pinned, they are reachable
            // at any offset, and they stay where actions sit everywhere else in
            // the app, ahead of neither the checkbox nor the row's first column.
            className="grid-pin-end"
            headerClassName="grid-pin-end"
            // RowActions stops click propagation itself, so an action never also
            // toggles the row's selection.
            body={(row: ListItem) =>
              isNewRow(row.uuid) ? (
                newRowActions()
              ) : (
              <RowActions
                size="small"
                // Reading and editing a record are what a row is for; sharing and
                // deleting it are occasional, so they fold into the overflow rather
                // than widening every row of the grid. Under 1280px even those two
                // fold in: a pinned column costs its width on every screen, and
                // that is the range where the grid has the least to spare.
                maxVisible={wideActions ? 2 : 0}
                actions={[
                  {
                    key: "view",
                    icon: "pi pi-eye",
                    label: "View record",
                    visible: viewForm !== null,
                    onClick: () => setViewItem(row),
                  },
                  {
                    key: "edit",
                    icon: "pi pi-pencil",
                    label: "Edit record",
                    visible: canItem(row.uuid, "edit"),
                    onClick: () => setRecordDialog({ open: true, item: row }),
                  },
                  {
                    key: "share",
                    icon: "pi pi-share-alt",
                    label: "Share record",
                    visible: canItem(row.uuid, "share"),
                    onClick: () => setSharingItem(row),
                  },
                  {
                    key: "qr",
                    icon: "pi pi-qrcode",
                    label: "QR code",
                    // No gate. Generating a code is a *read* of a record, and the row
                    // is only on screen because the caller may read it — so there is
                    // nothing left to check. The server re-applies `item_view_filter`
                    // when it builds the payload regardless, which is what actually
                    // enforces it; `visible` here is about offering an action that
                    // would work, and this one always does.
                    onClick: () => setQrItems([row]),
                  },
                  {
                    key: "archive",
                    icon: "pi pi-box",
                    label: "Archive record",
                    // Same authority as delete, and offered before it: archiving is
                    // the non-destructive way to get a finished record out of the
                    // way, and it should be the easier one to reach.
                    visible: canItem(row.uuid, "delete"),
                    onClick: () => confirmArchiveItem(row.uuid),
                  },
                  {
                    key: "delete",
                    icon: "pi pi-trash",
                    label: "Delete record",
                    severity: "danger",
                    visible: canItem(row.uuid, "delete"),
                    onClick: () => confirmDeleteItem(row),
                  },
                ]}
              />
              )
            }
          />
        </DataTable>
      )}

      {/* After the records rather than before them, and stuck to the bottom of
          the viewport: a selection made at the top of a long grid used to put
          its Delete button a whole screen away from the rows it applied to. */}
      {selection.length > 0 && (
        <div className="selection-bar">
          <span className="text-sm font-medium">
            {selection.length} selected
          </span>
          <span className="toolbar-divider" aria-hidden />
          {/* The first non-destructive action in this bar, and first on purpose: it is
              what somebody selecting forty rows most often means to do with them, and
              it reads better next to "N selected" than between Archive and Delete.
              Ungated for the same reason the row action is — printing is a read. */}
          <Button
            label="QR codes"
            icon="pi pi-qrcode"
            text
            size="small"
            onClick={() => setQrItems(selection)}
          />
          {deletableSelection.length > 0 && (
            <>
              {/* Archive first, and not in danger red: of the two ways to clear a
                  selection off the grid it is the reversible one, and on a long
                  list it is usually the one that was meant. */}
              <Button
                label={
                  deletableSelection.length === selection.length
                    ? "Archive selected"
                    : `Archive ${deletableSelection.length} of ${selection.length}`
                }
                icon="pi pi-box"
                text
                size="small"
                onClick={confirmBulkArchive}
                loading={bulkArchive.isPending}
              />
              <Button
                label={
                  deletableSelection.length === selection.length
                    ? "Delete selected"
                    : `Delete ${deletableSelection.length} of ${selection.length}`
                }
                icon="pi pi-trash"
                severity="danger"
                text
                size="small"
                onClick={confirmBulkDelete}
                loading={bulkDelete.isPending}
              />
            </>
          )}
          <Button
            label="Clear"
            text
            size="small"
            onClick={() => setSelection([])}
          />
        </div>
      )}

      {list.data && (
        <ListSettingsDialog
          listUuid={listUuid}
          listName={list.data.display_name}
          workspaceUuid={list.data.workspace.uuid ?? null}
          columns={cols}
          forms={formList}
          views={views.data ?? []}
          metrics={metricList}
          activeViewUuid={activeViewUuid}
          onSelectView={setActiveViewUuid}
          currentConfig={currentConfig}
          viewControls={viewControls}
          canConfigure={listActions.has("configure")}
          canShare={listActions.has("share")}
          inherit={{
            value: list.data.permissions_inherit,
            onChange: (value) => setListInherit.mutate(value),
            busy: setListInherit.isPending,
          }}
          visible={showSettings}
          onHide={() => setShowSettings(false)}
        />
      )}
      <RecordViewDialog
        listUuid={listUuid}
        columns={cols}
        form={viewForm}
        item={viewItem}
        principalUuids={principalUuids}
        lookupUuids={lookupUuids}
        canEdit={!!viewItem && canItem(viewItem.uuid, "edit")}
        onEdit={() => {
          setRecordDialog({ open: true, item: viewItem });
          setViewItem(null);
        }}
        visible={viewItem !== null}
        onHide={() => setViewItem(null)}
      />
      <RecordFormDialog
        listUuid={listUuid}
        columns={cols}
        form={recordForm}
        item={recordDialog.item}
        prefill={recordDialog.prefill}
        notes={recordDialog.notes}
        visible={recordDialog.open}
        onHide={() => setRecordDialog({ open: false, item: null })}
      />
      <Suspense fallback={null}>
        {qrMode === "fill" && (
          <QrScanDialog
            header="Fill using QR code"
            hint="The code's values fill the Add record form, where you can check them before adding the record."
            busy={qrBusy}
            error={qrError}
            onScan={handleQrScan}
            onHide={() => setQrMode(null)}
          />
        )}
        {qrMode === "submit" && (
          <QrSubmitSession
            listUuid={listUuid}
            columns={cols}
            fields={createFields}
            presets={qrPresets}
            onPresetsChange={setQrPresets}
            onHide={() => setQrMode(null)}
          />
        )}
        {qrItems.length > 0 && (
          <QrCodeDialog
            listUuid={listUuid}
            // The list object carries `settings.qr`, the saved field choice.
            lst={list.data}
            // `cols` (the view's visible columns) would be wrong: what a badge carries is
            // a property of the list, not of whichever view happens to be open, and a
            // saved selection must not quietly lose a field because a column is hidden
            // on screen.
            columns={columns.data ?? []}
            items={qrItems}
            canConfigure={listActions.has("configure")}
            visible
            onHide={() => setQrItems([])}
          />
        )}
      </Suspense>
      <ImportExportDialog
        listUuid={listUuid}
        listName={list.data?.name ?? "records"}
        columns={cols}
        canImport={listActions.has("create") && listActions.has("edit")}
        // What the grid is reading through, so an export can be made to match it.
        // `currentConfig`'s, not the view's: the page's filter and sort are the
        // view's plus whatever has been asked of it since, saved or not.
        currentFilters={filters}
        currentSorts={currentConfig.sorts ?? []}
        currentSearch={search}
        visible={showTransfer}
        onHide={() => setShowTransfer(false)}
      />
      <BinnedRecordsDialog
        kind="deleted"
        listUuid={listUuid}
        columns={cols}
        visible={showDeleted}
        onHide={() => setShowDeleted(false)}
      />
      <BinnedRecordsDialog
        kind="archived"
        listUuid={listUuid}
        columns={cols}
        visible={showArchived}
        onHide={() => setShowArchived(false)}
      />
      {/* No longer gated on there being an active view: a filter is this request's,
          so a list with no views at all can still be narrowed. */}
      <FilterDialog
        listUuid={listUuid}
        columns={cols}
        value={filters}
        onApply={setFilters}
        visible={showFilter}
        onHide={() => setShowFilter(false)}
      />
      {sharingRow && (
        <ShareDialog
          visible={!!sharingRow}
          onHide={() => setSharingItem(null)}
          resourceType="item"
          resourceUuid={sharingRow.uuid}
          resourceLabel="Record"
          inherit={{
            value: sharingRow.permissions_inherit,
            onChange: (value) =>
              setItemInherit.mutate({ uuid: sharingRow.uuid, value }),
            busy: setItemInherit.isPending,
          }}
        />
      )}
    </div>
  );
}
