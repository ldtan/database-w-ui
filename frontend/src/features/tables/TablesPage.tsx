import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "primereact/button";
import { Paginator } from "primereact/paginator";
import { AppDialog } from "../../components/AppDialog";
import { ConfirmDialog, confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { CreateNameDialog } from "../../components/CreateNameDialog";
import { EmptyState } from "../../components/EmptyState";
import { NameFields } from "../../components/NameFields";
import { PageHeader } from "../../components/PageHeader";
import { RowActions } from "../../components/RowActions";
import { SearchInput } from "../../components/SearchInput";
import { SkeletonCards } from "../../components/Skeleton";
import { useCurrentUser } from "../../lib/auth";
import type { TableRead, Workspace } from "../../lib/types";
import { isValidName, slugify } from "../../lib/slug";
import { useDebounced } from "../../lib/useDebounced";
import { useToast } from "../../lib/toast";
import { ShareDialog } from "../permissions/ShareDialog";
import {
  useEffectiveActions,
  useEffectiveActionsMap,
} from "../permissions/useEffectiveActions";
import { DeletedTablesDialog } from "./DeletedTablesDialog";
import { MoveOrTransferDialog } from "./MoveOrTransferDialog";
import { CloneTableDialog } from "./CloneTableDialog";
import { ErrorMessage, errorText } from "../../components/ErrorMessage";

// Search and paging both happen server-side, so a term finds a match no matter how
// far down the table it sits.
const ROWS = 12;

/**
 * The tables of one workspace: create, rename, share, delete.
 *
 * Creating is a permission on the *workspace*; everything else is asked per table,
 * because a table can be shared on its own terms and a viewer of the workspace is
 * not necessarily allowed to rename its tables.
 *
 * The create dialog takes only a label and derives the `name` slug from it. The
 * rename dialog then shows both, since a name is what code and API paths use and a
 * bad one has to be fixable — see `NameFields`.
 */
export function TablesPage() {
  const { workspaceUuid = "" } = useParams();
  const queryClient = useQueryClient();
  const toast = useToast();
  const me = useCurrentUser();
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["tables", workspaceUuid] });
    queryClient.invalidateQueries({ queryKey: ["effective"] });
  };

  const [search, setSearch] = useState("");
  const q = useDebounced(search);
  const [first, setFirst] = useState(0);
  const [rows, setRows] = useState(ROWS);
  // A new search re-ranks everything, so the old page number is meaningless.
  useEffect(() => setFirst(0), [q]);

  const tables = useQuery({
    queryKey: ["tables", workspaceUuid, "search", q, first, rows],
    queryFn: () =>
      api.getPage<TableRead>(
        `/tables/?workspace_uuid=${workspaceUuid}&skip=${first}&limit=${rows}` +
          `&q=${encodeURIComponent(q.trim())}`,
      ),
    placeholderData: (prev) => prev, // don't blank the table while paging
  });
  const workspace = useQuery({
    queryKey: ["workspace", workspaceUuid],
    queryFn: () => api.get<Workspace>(`/workspaces/${workspaceUuid}`),
  });
  // Adding a table is a workspace-level change; the rest is gated per table.
  const workspaceActions = useEffectiveActions("workspace", workspaceUuid);
  const shownTables = tables.data?.items ?? [];
  const tableTotal = tables.data?.total ?? 0;
  const can = useEffectiveActionsMap(
    "table",
    shownTables.map((table) => table.uuid),
  );

  const [creating, setCreating] = useState(false);
  const [showDeleted, setShowDeleted] = useState(false);
  const create = useMutation({
    mutationFn: (name: string) =>
      api.post<TableRead>("/tables/", {
        name: slugify(name),
        display_name: name,
        workspace_uuid: workspaceUuid,
      }),
    onSuccess: (table) => {
      setCreating(false);
      invalidate();
      toast.success("Table created", table.display_name);
    },
  });

  const [renaming, setRenaming] = useState<TableRead | null>(null);
  const [moving, setMoving] = useState<TableRead | null>(null);
  const [cloning, setCloning] = useState<TableRead | null>(null);
  const [names, setNames] = useState({ displayName: "", name: "" });
  const rename = useMutation({
    mutationFn: () =>
      api.patch<TableRead>(`/tables/${renaming!.uuid}`, {
        name: names.name,
        display_name: names.displayName,
      }),
    onSuccess: () => {
      setRenaming(null);
      invalidate();
      toast.success("Table renamed");
    },
  });

  const remove = useMutation({
    mutationFn: (uuid: string) => api.del(`/tables/${uuid}`),
    onSuccess: () => {
      invalidate();
      toast.success("Table deleted");
    },
    onError: (err) =>
      toast.error(
        "Could not delete table",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  // Share dialog: track by uuid so the inherit toggle reflects fresh data.
  const [sharingUuid, setSharingUuid] = useState<string | null>(null);
  const sharing = shownTables.find((l) => l.uuid === sharingUuid) ?? null;
  const setInherit = useMutation({
    mutationFn: (vars: { uuid: string; value: boolean }) =>
      api.patch(`/tables/${vars.uuid}`, { permissions_inherit: vars.value }),
    onSuccess: invalidate,
    onError: (err) =>
      toast.error(
        "Could not update inheritance",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  function confirmDelete(table: TableRead) {
    confirmDialog({
      message: `Delete table "${table.display_name}" and its records?`,
      header: "Confirm",
      icon: "pi pi-exclamation-triangle",
      accept: () => remove.mutate(table.uuid),
    });
  }

  const canCreate = workspaceActions.has("configure");

  return (
    <div className="flex flex-col gap-5" style={{ maxWidth: 1100 }}>
      <ConfirmDialog />
      <PageHeader
        crumbs={[
          { label: "Workspaces", to: "/workspaces" },
          { label: workspace.data?.display_name ?? "Tables" },
        ]}
        title={workspace.data?.display_name ?? "Tables"}
        subtitle="Every table stands on its own — its own columns, records and views."
        actions={
          <>
            {workspaceActions.has("delete") && (
              <Button
                data-tour="deleted-tables"
                label="Deleted"
                icon="pi pi-trash"
                text
                onClick={() => setShowDeleted(true)}
              />
            )}
            {canCreate && (
              <Button
                data-tour="new-table"
                label="New table"
                icon="pi pi-plus"
                onClick={() => setCreating(true)}
              />
            )}
          </>
        }
      />

      <SearchInput
        value={search}
        onChange={setSearch}
        placeholder="Search tables…"
      />

      {tables.isLoading ? (
        <SkeletonCards count={6} />
      ) : shownTables.length === 0 ? (
        <EmptyState
          icon={search ? "pi pi-search" : "pi pi-table"}
          title={search ? "No matches" : "No tables yet"}
          text={
            search
              ? `Nothing in this workspace is called “${search}”.`
              : canCreate
                ? "Give it columns, then fill it with records."
                : "Nothing has been added to this workspace yet."
          }
          action={
            search ? (
              <Button
                label="Clear search"
                icon="pi pi-times"
                text
                onClick={() => setSearch("")}
              />
            ) : canCreate ? (
              <Button
                label="New table"
                icon="pi pi-plus"
                onClick={() => setCreating(true)}
              />
            ) : undefined
          }
        />
      ) : (
        <div className="card-grid">
          {/* The tour points at one card rather than the grid: a spotlight the
              size of the whole page lights nothing in particular. */}
          {shownTables.map((table, i) => (
            <div
              key={table.uuid}
              data-tour={i === 0 ? "table-card" : undefined}
              className="surface-card link-card p-3 flex items-start justify-between gap-2"
            >
              <Link
                to={`/tables/${table.uuid}`}
                className="no-underline link-card-target flex items-start gap-3 min-w-0"
              >
                <span className="entity-icon" aria-hidden>
                  <i className="pi pi-table" />
                </span>
                <span className="flex flex-col min-w-0">
                  <span className="font-medium truncate">
                    {table.display_name}
                  </span>
                  <span className="text-sm text-muted truncate">
                    {table.name}
                  </span>
                </span>
              </Link>
              {/* One action inline and the rest behind the overflow: a card is
                  narrow, and five icon buttons would leave no room for the name
                  they belong to. */}
              <div className="link-card-above">
                <RowActions
                  maxVisible={1}
                  actions={[
                    {
                      key: "share",
                      icon: "pi pi-share-alt",
                      label: "Share table",
                      visible: can(table.uuid, "share"),
                      onClick: () => setSharingUuid(table.uuid),
                    },
                    {
                      key: "edit",
                      icon: "pi pi-pencil",
                      label: "Rename table",
                      visible: can(table.uuid, "configure"),
                      onClick: () => {
                        setRenaming(table);
                        setNames({
                          displayName: table.display_name,
                          name: table.name,
                        });
                      },
                    },
                    {
                      key: "move",
                      icon: "pi pi-send",
                      label: "Move or transfer table",
                      visible: can(table.uuid, "configure"),
                      onClick: () => setMoving(table),
                    },
                    {
                      // Cloning writes a new table into *this* workspace, so it is
                      // gated on the workspace's `configure`, not the source table's.
                      key: "duplicate",
                      icon: "pi pi-copy",
                      label: "Duplicate table",
                      visible: canCreate,
                      onClick: () => setCloning(table),
                    },
                    {
                      key: "delete",
                      icon: "pi pi-trash",
                      label: "Delete table",
                      severity: "danger",
                      visible: can(table.uuid, "delete"),
                      onClick: () => confirmDelete(table),
                    },
                  ]}
                />
              </div>
            </div>
          ))}
        </div>
      )}

      {tableTotal > rows && (
        <Paginator
          first={first}
          rows={rows}
          totalRecords={tableTotal}
          rowsPerPageOptions={[12, 24, 48, 96]}
          onPageChange={(e) => {
            setFirst(e.first);
            setRows(e.rows);
          }}
          template="CurrentPageReport FirstPageLink PrevPageLink PageLinks NextPageLink LastPageLink RowsPerPageDropdown"
          currentPageReportTemplate="{first}–{last} of {totalRecords}"
        />
      )}

      <CreateNameDialog
        visible={creating}
        onHide={() => setCreating(false)}
        header="New table"
        label="Table name"
        placeholder="Contacts"
        hint="Add its columns once it exists — under Settings on the table itself."
        loading={create.isPending}
        error={errorText(create.error)}
        onSubmit={(name) => create.mutate(name)}
      />

      {sharing && (
        <ShareDialog
          visible={!!sharing}
          onHide={() => setSharingUuid(null)}
          resourceType="table"
          resourceUuid={sharing.uuid}
          resourceLabel={sharing.display_name}
          inherit={{
            value: sharing.permissions_inherit,
            onChange: (value) =>
              setInherit.mutate({ uuid: sharing.uuid, value }),
            busy: setInherit.isPending,
          }}
        />
      )}

      <AppDialog
        header="Rename table"
        visible={!!renaming}
        onHide={() => setRenaming(null)}
        style={{ width: 420 }}
        footer={
          <>
            <Button label="Cancel" text onClick={() => setRenaming(null)} />
            <Button
              label="Save"
              icon="pi pi-check"
              onClick={() => rename.mutate()}
              loading={rename.isPending}
              disabled={!names.displayName.trim() || !isValidName(names.name)}
            />
          </>
        }
      >
        <div className="flex flex-col gap-4">
          <ErrorMessage error={rename.error} />
          <NameFields
            key={renaming?.uuid ?? "none"}
            idPrefix="table"
            value={names}
            onChange={setNames}
            linked={false}
            autoFocus
          />
        </div>
      </AppDialog>

      <MoveOrTransferDialog
        table={moving}
        currentWorkspaceUuid={workspaceUuid}
        // Only the owner may transfer; a configure-grant holder can still move.
        canTransfer={!!me && moving?.owner_username === me.username}
        onHide={() => setMoving(null)}
        onDone={() => {
          setMoving(null);
          invalidate();
        }}
      />

      <CloneTableDialog
        table={cloning}
        onHide={() => setCloning(null)}
        onDone={() => {
          setCloning(null);
          invalidate();
        }}
      />

      <DeletedTablesDialog
        workspaceUuid={workspaceUuid}
        visible={showDeleted}
        onHide={() => setShowDeleted(false)}
      />
    </div>
  );
}
