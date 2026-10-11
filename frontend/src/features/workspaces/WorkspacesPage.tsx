import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "primereact/button";
import { Message } from "primereact/message";
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
import type { TableRead, Workspace } from "../../lib/types";
import { isValidName, slugify } from "../../lib/slug";
import { useDebounced } from "../../lib/useDebounced";
import { useToast } from "../../lib/toast";
import { ShareDialog } from "../permissions/ShareDialog";
import { useEffectiveActionsMap } from "../permissions/useEffectiveActions";
import { DeletedWorkspacesDialog } from "./DeletedWorkspacesDialog";
import { TransferWorkspaceDialog } from "./TransferWorkspaceDialog";
import { ErrorMessage, errorText } from "../../components/ErrorMessage";

// Search and paging both happen server-side, so a term finds a match no matter how
// far down the table it sits — a client-side filter over one fetched page would
// report "no match" for anything past the limit.
const ROWS = 12;

/** One workspace card. Shared by both grids on this page: a workspace someone
 *  gave you looks and behaves like your own, and its controls are gated by its
 *  effective actions rather than by ownership — so a view-only share simply shows
 *  fewer of them. */
function WorkspaceCard({
  workspace: ws,
  can,
  owned,
  tour,
  onShare,
  onRename,
  onTransfer,
  onDelete,
}: {
  workspace: Workspace;
  can: (uuid: string, action: "share" | "configure" | "delete") => boolean;
  /** Whether the caller owns this one. Transfer is the one control effective
   *  actions cannot answer for: it is owner-only server-side, and no grant —
   *  not even `manage` — qualifies, so it is gated on ownership instead. The
   *  two grids know this without asking, being split on `shared` already. */
  owned: boolean;
  tour?: boolean;
  onShare: (ws: Workspace) => void;
  onRename: (ws: Workspace) => void;
  onTransfer: (ws: Workspace) => void;
  onDelete: (ws: Workspace) => void;
}) {
  return (
    <div
      data-tour={tour ? "workspace-card" : undefined}
      className="surface-card link-card p-3 flex items-start justify-between gap-2"
    >
      <Link
        to={`/workspaces/${ws.uuid}`}
        className="no-underline link-card-target flex items-start gap-3 min-w-0"
      >
        <span
          className={`entity-icon${ws.is_system ? " is-muted" : ""}`}
          aria-hidden
        >
          <i className={ws.is_system ? "pi pi-inbox" : "pi pi-th-large"} />
        </span>
        <span className="flex flex-col min-w-0">
          <span className="font-medium truncate">{ws.display_name}</span>
          <span className="text-sm text-muted truncate">
            {ws.is_system ? "Tables transferred to you" : ws.name}
          </span>
        </span>
      </Link>
      {/* The Incoming inbox is immutable — no rename/delete/share; the recipient
          acts on the tables inside it, not on the box. */}
      <div className="link-card-above">
        <RowActions
          maxVisible={1}
          actions={[
            {
              key: "share",
              icon: "pi pi-share-alt",
              label: "Share workspace",
              visible: !ws.is_system && can(ws.uuid, "share"),
              onClick: () => onShare(ws),
            },
            {
              key: "edit",
              icon: "pi pi-pencil",
              label: "Rename workspace",
              visible: !ws.is_system && can(ws.uuid, "configure"),
              onClick: () => onRename(ws),
            },
            {
              key: "transfer",
              icon: "pi pi-send",
              label: "Transfer workspace",
              visible: !ws.is_system && owned,
              onClick: () => onTransfer(ws),
            },
            {
              key: "delete",
              icon: "pi pi-trash",
              label: "Delete workspace",
              severity: "danger",
              visible: !ws.is_system && can(ws.uuid, "delete"),
              onClick: () => onDelete(ws),
            },
          ]}
        />
      </div>
    </div>
  );
}

/**
 * The Workspaces page: your workspaces, plus everything shared with you.
 *
 * "Shared with me" holds two kinds of thing, fetched by two different questions.
 * A workspace shared whole appears as a workspace, and its tables are reached by
 * opening it. A table shared *without* its workspace has no workspace you can open
 * — it hangs off nothing — so it is listed here directly; without this section it
 * would exist and be unreachable. Those loose tables show the table's own slug
 * rather than "in «Workspace»", because naming a workspace you were deliberately
 * not given would leak it.
 *
 * Asking only "which tables do I not own?" conflates the two, which is what put
 * every table of a shared workspace on this page twice — once inside the workspace
 * and once loose down here.
 *
 * A shared workspace may be view-only, so each row's controls are gated by that
 * workspace's own effective actions rather than by ownership.
 */
export function WorkspacesPage() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["workspaces"] });

  const [wsSearch, setWsSearch] = useState("");
  const [sharedSearch, setSharedSearch] = useState("");
  const wsQ = useDebounced(wsSearch);
  const sharedQ = useDebounced(sharedSearch);
  const [wsFirst, setWsFirst] = useState(0);
  const [sharedFirst, setSharedFirst] = useState(0);
  const [wsRows, setWsRows] = useState(ROWS);
  const [sharedRows, setSharedRows] = useState(ROWS);
  // A new search re-ranks everything, so the old page number is meaningless;
  // without this you can land on an empty page and think there are no matches.
  useEffect(() => setWsFirst(0), [wsQ]);
  useEffect(() => setSharedFirst(0), [sharedQ]);

  const workspaces = useQuery({
    queryKey: ["workspaces", "owned", wsQ, wsFirst, wsRows],
    queryFn: () =>
      api.getPage<Workspace>(
        `/workspaces/?shared=false&skip=${wsFirst}&limit=${wsRows}` +
          `&q=${encodeURIComponent(wsQ.trim())}`,
      ),
    placeholderData: (prev) => prev, // don't blank the table while paging
  });

  // Workspaces shared with you whole. Unpaginated: people are given a handful of
  // workspaces, not hundreds, and a second pager inside the section below would
  // cost more attention than it saves. The cap is high enough to say so honestly.
  const sharedWorkspaces = useQuery({
    queryKey: ["workspaces", "shared", sharedQ],
    queryFn: () =>
      api.getPage<Workspace>(
        `/workspaces/?shared=true&limit=100` +
          `&q=${encodeURIComponent(sharedQ.trim())}`,
      ),
    placeholderData: (prev) => prev,
  });

  // A table can be shared *without* its workspace, which then stays invisible — so
  // it has no workspace to browse into and would be unreachable without this
  // section. Tables that came in via a shared workspace are deliberately absent:
  // they are reachable through that workspace's own card above, and listing them
  // here as well showed every one of them twice.
  const shared = useQuery({
    queryKey: ["tables", "shared", "unreachable", sharedQ, sharedFirst, sharedRows],
    queryFn: () =>
      api.getPage<TableRead>(
        `/tables/?shared=true&workspace_hidden=true` +
          `&skip=${sharedFirst}&limit=${sharedRows}` +
          `&q=${encodeURIComponent(sharedQ.trim())}`,
      ),
    placeholderData: (prev) => prev,
  });

  const shownWorkspaces = workspaces.data?.items ?? [];
  const wsTotal = workspaces.data?.total ?? 0;
  const shownSharedWorkspaces = sharedWorkspaces.data?.items ?? [];
  const sharedWsTotal = sharedWorkspaces.data?.total ?? 0;
  const shownShared = shared.data?.items ?? [];
  const sharedTableTotal = shared.data?.total ?? 0;
  // The badge counts everything in the section, workspaces and loose tables alike.
  const sharedTotal = sharedWsTotal + sharedTableTotal;

  // A shared workspace may be view-only, so gate its controls per row — for both
  // grids at once, so the two cards render from one batched permission read.
  const can = useEffectiveActionsMap(
    "workspace",
    [...shownWorkspaces, ...shownSharedWorkspaces].map((ws) => ws.uuid),
  );

  const [creating, setCreating] = useState(false);
  const [showDeleted, setShowDeleted] = useState(false);
  const create = useMutation({
    mutationFn: (name: string) =>
      api.post<Workspace>("/workspaces/", {
        name: slugify(name),
        display_name: name,
      }),
    onSuccess: (ws) => {
      setCreating(false);
      invalidate();
      toast.success("Workspace created", ws.display_name);
    },
  });

  const [sharing, setSharing] = useState<Workspace | null>(null);
  const [transferring, setTransferring] = useState<Workspace | null>(null);
  const [renaming, setRenaming] = useState<Workspace | null>(null);
  const [names, setNames] = useState({ displayName: "", name: "" });
  const rename = useMutation({
    mutationFn: () =>
      api.patch<Workspace>(`/workspaces/${renaming!.uuid}`, {
        name: names.name,
        display_name: names.displayName,
      }),
    onSuccess: () => {
      setRenaming(null);
      invalidate();
      toast.success("Workspace renamed");
    },
  });

  const remove = useMutation({
    mutationFn: (uuid: string) => api.del(`/workspaces/${uuid}`),
    onSuccess: () => {
      invalidate();
      toast.success("Workspace deleted");
    },
    onError: (err) =>
      toast.error(
        "Could not delete workspace",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  function startRename(ws: Workspace) {
    setRenaming(ws);
    setNames({ displayName: ws.display_name, name: ws.name });
  }

  function confirmDelete(ws: Workspace) {
    confirmDialog({
      message: `Delete workspace "${ws.display_name}" and all its tables?`,
      header: "Confirm",
      icon: "pi pi-exclamation-triangle",
      accept: () => remove.mutate(ws.uuid),
    });
  }

  return (
    <div className="flex flex-col gap-5" style={{ maxWidth: 1100 }}>
      <ConfirmDialog />
      <PageHeader
        crumbs={[{ label: "Workspaces" }]}
        title="Workspaces"
        subtitle="A workspace holds a set of tables — and decides who can reach them."
        actions={
          <>
            <Button
              label="Deleted"
              icon="pi pi-trash"
              text
              onClick={() => setShowDeleted(true)}
            />
            <Button
              data-tour="new-workspace"
              label="New workspace"
              icon="pi pi-plus"
              onClick={() => setCreating(true)}
            />
          </>
        }
      />

      <SearchInput
        value={wsSearch}
        onChange={setWsSearch}
        placeholder="Search workspaces…"
      />

      {workspaces.isLoading ? (
        <SkeletonCards count={6} />
      ) : shownWorkspaces.length === 0 ? (
        <EmptyState
          icon={wsSearch ? "pi pi-search" : "pi pi-th-large"}
          title={wsSearch ? "No matches" : "No workspaces yet"}
          text={
            wsSearch
              ? `Nothing here is called “${wsSearch}”.`
              : "A workspace is a folder for your tables. Create one to start putting records somewhere."
          }
          action={
            wsSearch ? (
              <Button
                label="Clear search"
                icon="pi pi-times"
                text
                onClick={() => setWsSearch("")}
              />
            ) : (
              <Button
                label="New workspace"
                icon="pi pi-plus"
                onClick={() => setCreating(true)}
              />
            )
          }
        />
      ) : (
        <div className="card-grid">
          {/* The tour points at one card rather than the grid: a spotlight the
              size of forty of them lights the page rather than a thing on it. */}
          {shownWorkspaces.map((ws, i) => (
            <WorkspaceCard
              key={ws.uuid}
              workspace={ws}
              can={can}
              owned
              tour={i === 0}
              onShare={setSharing}
              onRename={startRename}
              onTransfer={setTransferring}
              onDelete={confirmDelete}
            />
          ))}
        </div>
      )}

      {/* Only once there is more than one page of them: a pager under four cards
          is a control that can do nothing. */}
      {wsTotal > wsRows && (
        <Paginator
          first={wsFirst}
          rows={wsRows}
          totalRecords={wsTotal}
          rowsPerPageOptions={[12, 24, 48, 96]}
          onPageChange={(e) => {
            setWsFirst(e.first);
            setWsRows(e.rows);
          }}
          template="CurrentPageReport FirstPageLink PrevPageLink PageLinks NextPageLink LastPageLink RowsPerPageDropdown"
          currentPageReportTemplate="{first}–{last} of {totalRecords}"
        />
      )}

      {(sharedTotal > 0 || sharedSearch) && (
        <section className="flex flex-col gap-3 mt-2">
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-semibold">Shared with me</h2>
              <span className="badge">{sharedTotal}</span>
            </div>
            <p className="text-sm" style={{ color: "var(--text-muted)" }}>
              Workspaces other people shared with you, and tables they gave you on
              their own — without the workspace around them.
            </p>
          </div>
          <SearchInput
            value={sharedSearch}
            onChange={setSharedSearch}
            placeholder="Search shared workspaces and tables…"
          />
          {!shared.isLoading &&
            !sharedWorkspaces.isLoading &&
            shownShared.length === 0 &&
            shownSharedWorkspaces.length === 0 && (
              <Message
                severity="info"
                text="Nothing shared with you matches that search."
              />
            )}
          {shownSharedWorkspaces.length > 0 && (
            <div className="card-grid">
              {shownSharedWorkspaces.map((ws) => (
                <WorkspaceCard
                  key={ws.uuid}
                  workspace={ws}
                  can={can}
                  // Shared with the caller, so never theirs to hand on.
                  owned={false}
                  onShare={setSharing}
                  onRename={startRename}
                  onTransfer={setTransferring}
                  onDelete={confirmDelete}
                />
              ))}
            </div>
          )}
          <div className="card-grid">
            {shownShared.map((table) => (
              <div key={table.uuid} className="surface-card link-card p-3">
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
                    {/* Same-named tables are common here, so disambiguate by owner.
                        Never by workspace: these are exactly the tables whose
                        workspace the caller cannot view, so the backend withholds
                        its name — printing it would leak a workspace they were
                        deliberately not given. The table's own slug stands in. */}
                    <span className="text-sm text-muted truncate">
                      {table.owner_username
                        ? `owned by ${table.owner_username}`
                        : table.name}
                    </span>
                  </span>
                </Link>
              </div>
            ))}
          </div>
          {/* Pages the loose tables only — the shared workspaces above are
              unpaginated, so counting them here would page past the end. */}
          {sharedTableTotal > sharedRows && (
            <Paginator
              first={sharedFirst}
              rows={sharedRows}
              totalRecords={sharedTableTotal}
              rowsPerPageOptions={[12, 24, 48, 96]}
              onPageChange={(e) => {
                setSharedFirst(e.first);
                setSharedRows(e.rows);
              }}
              template="CurrentPageReport FirstPageLink PrevPageLink PageLinks NextPageLink LastPageLink RowsPerPageDropdown"
              currentPageReportTemplate="{first}–{last} of {totalRecords}"
            />
          )}
        </section>
      )}

      <CreateNameDialog
        visible={creating}
        onHide={() => setCreating(false)}
        header="New workspace"
        label="Workspace name"
        placeholder="Marketing"
        hint="You can rename it later; its short name is derived from this."
        loading={create.isPending}
        error={errorText(create.error)}
        onSubmit={(name) => create.mutate(name)}
      />

      {sharing && (
        <ShareDialog
          visible={!!sharing}
          onHide={() => setSharing(null)}
          resourceType="workspace"
          resourceUuid={sharing.uuid}
          resourceLabel={sharing.display_name}
        />
      )}

      <AppDialog
        header="Rename workspace"
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
            idPrefix="workspace"
            value={names}
            onChange={setNames}
            linked={false}
            autoFocus
          />
        </div>
      </AppDialog>

      <TransferWorkspaceDialog
        workspace={transferring}
        onHide={() => setTransferring(null)}
        onDone={() => {
          setTransferring(null);
          invalidate();
        }}
      />

      <DeletedWorkspacesDialog
        visible={showDeleted}
        onHide={() => setShowDeleted(false)}
      />
    </div>
  );
}
