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

import { api, ApiError, type Page } from "../../lib/api";
import { useToast } from "../../lib/toast";
import { useDebounced } from "../../lib/useDebounced";
import { useLiveList } from "../../lib/useLiveList";
import { SMALL_SCREEN, WIDE_SCREEN, useMediaQuery } from "../../lib/useMediaQuery";
import type { Crumb } from "../../components/Breadcrumbs";
import { EmptyState } from "../../components/EmptyState";
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
import { DeletedRecordsDialog } from "../records/DeletedRecordsDialog";
import { fillFromQr, parseQrPayload } from "../records/qrFill";
import {
  cellEditKind,
  cellEditKinds,
  cellPatch,
  type CellDraft,
} from "../records/cellEdit";
import { InlineCellEditor } from "../records/inlineEdit";
import { recordPagesKey } from "../records/recordKeys";
import { resolveFields } from "../records/recordValues";
import { renderCell } from "../records/renderCell";
import { ImportExportDialog } from "../transfer/ImportExportDialog";
import { collectLookupUuids } from "../lookups/LookupControls";
import { collectPrincipalUuids } from "../principals/PrincipalControls";
import { SortControl } from "../views/SortControl";
import { ViewBar } from "../views/ViewBar";
import { ViewControls } from "../views/ViewControls";
import { FilterDialog } from "../views/FilterDialog";
import { recordParams } from "../views/recordQuery";
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
  const [sharingItemUuid, setSharingItemUuid] = useState<string | null>(null);
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
  // something saved: the app has no per-list UI preferences to hang it on, and
  // reaching for localStorage or the view config for one boolean would be the
  // first of those rather than a use of one.
  const [showMetrics, setShowMetrics] = useState(false);
  // "Edit cells": the grid's values become editable in place. Page state for the
  // same reason `showMetrics` is — it is how you are working right now, not a
  // property of the list or of the view, and it deliberately does not survive a
  // reload: editing rows by accident is worse than turning the mode on again.
  const [inlineEdit, setInlineEdit] = useState(false);
  // What the open cell editor holds. A ref rather than state: it changes on every
  // keystroke, and re-rendering a hundred-row grid for each one would be a real
  // cost for a value nothing on screen reads until the edit is committed.
  const cellDraft = useRef<CellDraft | null>(null);
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
  // Spread first so the query's own keys/fn win; today it supplies a polling
  // `refetchInterval`, later an SSE subscription (see useLiveList).
  const live = useLiveList(listUuid);
  const items = useQuery({
    ...live,
    // Everything that decides *which* records come back is part of the key, because
    // every one of them is applied server-side: the page, the order, the view and
    // the search each produce a different set of rows, not a rearrangement of one
    // the browser already has.
    queryKey: [
      ...recordPagesKey(listUuid),
      activeViewUuid,
      q,
      first,
      pageSize,
      sortField,
      sortOrder,
      groupBy,
      filters,
    ],
    queryFn: () =>
      api.getPage<ListItem>(
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
  const rows = items.data?.items ?? [];
  const total = items.data?.total ?? 0;
  // The rows the grid is *currently* showing, for the cell editor to open from.
  // PrimeReact hands its `editor` a snapshot of the row rather than the row, so
  // an editor opened after a poll would otherwise be seeded from what the record
  // held when its cell first rendered — see `InlineCellEditor`'s `row`.
  const rowByUuid = useMemo(
    () => new Map(rows.map((row) => [row.uuid, row])),
    [rows],
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

  // Apply the active view's saved config.
  useEffect(() => {
    const view = views.data?.find((v) => v.uuid === activeViewUuid);
    if (!view) return;
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
  // One cell, committed on its own. `PATCH /items/{uuid}` merges the keys it is
  // given onto the stored record, so the body is the one column that changed (plus
  // any lookup nested under it, which `cellPatch` clears) rather than the record.
  const saveCell = useMutation({
    mutationFn: (vars: { uuid: string; patch: Record<string, unknown> }) =>
      api.patch<ListItem>(`/lists/${listUuid}/items/${vars.uuid}`, {
        data: vars.patch,
      }),
    // Show the value the moment it is committed. Without this the cell falls back
    // to what it held until the refetch lands — a visible flick back to the old
    // value on every edit, which reads as the save having failed.
    //
    // Keyed on the *pages* rather than on `["items", listUuid]`, which is the
    // namespace the metric values share (see `recordKeys`): matching that would
    // hand this page-shaped updater a `MetricValues`, and `page.items.map` on an
    // object with no `items` throws — inside `onMutate`, which runs before
    // `mutationFn`, so the PATCH would never even be sent.
    onMutate: (vars) => {
      queryClient.setQueriesData<Page<ListItem>>(
        { queryKey: recordPagesKey(listUuid) },
        (page) =>
          page && {
            ...page,
            items: page.items.map((item) =>
              item.uuid === vars.uuid
                ? { ...item, data: { ...item.data, ...vars.patch } }
                : item,
            ),
          },
      );
    },
    onError: (err) =>
      toast.error(
        "Could not save that value",
        err instanceof ApiError ? err.message : undefined,
      ),
    // Both outcomes land here, and for the same reason: what is on screen is a
    // guess. A save that worked may have recomputed a calculated column, restamped
    // an `onupdate` one, or moved the record out of the filter it no longer
    // matches; a save that failed left an optimistic value the server never
    // accepted. Refetching settles it either way, so the rejected edit reverts
    // itself and the toast explains why.
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] }),
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
    mutationFn: () =>
      api.post<{ deleted: number }>(`/lists/${listUuid}/items/bulk-delete`, {
        uuids: deletableSelection.map((item) => item.uuid),
      }),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      setSelection([]);
      toast.success("Records deleted", `${res.deleted} record(s)`);
    },
    onError: (err) =>
      toast.error(
        "Could not delete records",
        err instanceof ApiError ? err.message : undefined,
      ),
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
  // so that the query key covers the whole page rather than a single row.
  const principalUuids = useMemo(
    () => collectPrincipalUuids(cols, rows),
    [cols, rows],
  );
  const lookupUuids = useMemo(
    () => collectLookupUuids(cols, rows),
    [cols, rows],
  );

  const formList = forms.data ?? [];
  const sharingItem =
    rows.find((i) => i.uuid === sharingItemUuid) ?? null;
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
  // column except the calculated ones, which store nothing.
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
   * A cell edit, on its way to the server.
   *
   * PrimeReact calls this when the cell leaves edit mode by any route that means
   * "keep it" — Enter, Tab, a click elsewhere — and never on Escape, which is what
   * makes Escape a cancel. The value comes from the draft the editor reported
   * rather than from the event, for the reason `InlineCellEditor` explains, and
   * so does the value it is measured against — `row` is here to answer *which
   * cell this is*, which is the one question the event's row can be trusted on.
   */
  function commitCell(column: Column, row: ListItem) {
    const draft = cellDraft.current;
    cellDraft.current = null;
    // Not this cell's draft: the editor was never opened, or what is in hand
    // belongs to a cell edited before it.
    if (
      !draft ||
      draft.rowUuid !== row.uuid ||
      draft.columnUuid !== column.uuid
    ) {
      return;
    }
    const patch = cellPatch(cols, column, draft);
    if (patch) saveCell.mutate({ uuid: draft.rowUuid, patch });
  }

  // A QR code always creates, so it is always the "create" form it fills — which
  // is what decides the fields it may write, and their names. Resolving them here
  // (rather than reading `cols`) is what stops a code from filling a column the
  // form excludes or marks read-only.
  const createForm = formList.find((f) => f.type === "create") ?? null;
  const createFields = useMemo(
    () => resolveFields(cols, createForm),
    [cols, createForm],
  );

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
  // Materialize the full effective order, swap two entries, and store it — so
  // column_order is always a complete ordering after any manual reorder.
  function moveColumn(index: number, delta: number) {
    const target = index + delta;
    if (target < 0 || target >= orderedCols.length) return;
    const next = orderedCols.map((c) => c.uuid);
    [next[index], next[target]] = [next[target], next[index]];
    setColumnOrder(next);
  }

  // One object, handed to both the toolbar and List settings → Views, so the two
  // drive the same state rather than each keeping their own.
  const viewControls = {
    columns: orderedCols,
    hiddenColumns,
    onToggleColumn: toggleColumn,
    onMoveColumn: moveColumn,
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
                // The bin is a recovery surface gated on list-level delete (a
                // deleted record has no per-item permission to check).
                ...(listActions.has("delete")
                  ? [
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
            someone with a record on this page they may actually write to. It
            survives its own condition while on, so turning it off never depends
            on a filter that has hidden every editable row. */}
        {!small &&
          (inlineEdit || rows.some((row) => canItem(row.uuid, "edit"))) && (
            <Button
              label="Edit cells"
              icon="pi pi-pencil"
              text
              className={inlineEdit ? "is-on" : undefined}
              aria-pressed={inlineEdit}
              onClick={() => setInlineEdit((on) => !on)}
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

      {cols.length === 0 && !columns.isLoading && (
        <Message
          severity="info"
          text={
            listActions.has("configure")
              ? "This list has no columns yet. Add them under Settings → Columns, then add records."
              : "This list has no columns yet."
          }
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
          loading={columns.isLoading || items.isLoading}
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
          onShare={(item) => setSharingItemUuid(item.uuid)}
          onDelete={confirmDeleteItem}
        />
      ) : (
        <DataTable
          value={rows}
          loading={columns.isLoading || items.isLoading}
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
          // Set only while the mode is on: with no edit mode a column's `editor`
          // is inert, so this is the single switch that turns a grid of values
          // into a grid of cells. "cell", not "row" — one value is committed at a
          // time, which is what a partial PATCH per cell already means.
          editMode={inlineEdit ? "cell" : undefined}
          // PrimeReact calls this before applying its own selection logic, and
          // skips it entirely when the click landed on something clickable — so
          // the checkbox and the action buttons keep their own behaviour without
          // needing a guard here (RowActions stops propagation regardless).
          onRowClick={(e) => rowOpener(e.data as ListItem)?.()}
          rowClassName={(row: ListItem) =>
            rowOpener(row) ? "grid-row-opens" : ""
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
          emptyMessage={
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
            const kind = inlineEdit
              ? cellEditKind(editKinds, col.uuid)
              : "locked";
            return (
              <DataColumn
                key={col.uuid}
                field={`data.${col.uuid}`}
                // A calculated column stores nothing — its value is computed per row
                // as the records are read — so there is no column in the database to
                // order the whole list by, and a header that sorted only the page on
                // screen would be the bug this page just lost. Not offered, rather
                // than offered and quietly wrong.
                sortable={!groupColumn && col.type !== "calculated"}
                // Deleting a column moved into List settings: a trash button on every
                // header crowded out the thing the grid is for.
                header={col.display_name}
                // What a click here will do, said before it is clicked. A cell that
                // will not open — a read-only field, a calculated column, a record
                // this caller may read but not write — stays flat, and that
                // flatness is the whole answer to "why won't this one edit?".
                bodyClassName={(row: ListItem) =>
                  kind === "locked" || !canItem(row.uuid, "edit")
                    ? ""
                    : kind === "inline"
                      ? "cell-editable"
                      : "cell-opens-record"
                }
                body={(row: ListItem) => {
                  const cell = renderCell(
                    col,
                    row,
                    listUuid,
                    principalUuids,
                    lookupUuids,
                  );
                  // Rich text and multiline have no cell-sized editor, so the
                  // click goes where their editor lives instead of doing nothing.
                  if (kind !== "dialog" || !canItem(row.uuid, "edit")) return cell;
                  return (
                    <span
                      title={`Edit ${col.display_name} in the record's own form`}
                      onClick={() => setRecordDialog({ open: true, item: row })}
                    >
                      {cell}
                    </span>
                  );
                }}
                editor={
                  kind === "inline"
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
                  canItem((e.rowData as ListItem).uuid, "edit")
                    ? undefined
                    : false
                }
                onCellEditComplete={(e) =>
                  commitCell(col, e.rowData as ListItem)
                }
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
            body={(row: ListItem) => (
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
                    onClick: () => setSharingItemUuid(row.uuid),
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
            )}
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
          {deletableSelection.length > 0 && (
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
      </Suspense>
      <ImportExportDialog
        listUuid={listUuid}
        listName={list.data?.name ?? "records"}
        columns={cols}
        canImport={listActions.has("create") && listActions.has("edit")}
        visible={showTransfer}
        onHide={() => setShowTransfer(false)}
      />
      <DeletedRecordsDialog
        listUuid={listUuid}
        columns={cols}
        visible={showDeleted}
        onHide={() => setShowDeleted(false)}
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
      {sharingItem && (
        <ShareDialog
          visible={!!sharingItem}
          onHide={() => setSharingItemUuid(null)}
          resourceType="item"
          resourceUuid={sharingItem.uuid}
          resourceLabel="Record"
          inherit={{
            value: sharingItem.permissions_inherit,
            onChange: (value) =>
              setItemInherit.mutate({ uuid: sharingItem.uuid, value }),
            busy: setItemInherit.isPending,
          }}
        />
      )}
    </div>
  );
}
