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
import type { ListRead, Workspace } from "../../lib/types";
import { isValidName, slugify } from "../../lib/slug";
import { useDebounced } from "../../lib/useDebounced";
import { useToast } from "../../lib/toast";
import { ShareDialog } from "../permissions/ShareDialog";
import { useEffectiveActionsMap } from "../permissions/useEffectiveActions";
import { DeletedWorkspacesDialog } from "./DeletedWorkspacesDialog";

// Search and paging both happen server-side, so a term finds a match no matter how
// far down the list it sits — a client-side filter over one fetched page would
// report "no match" for anything past the limit.
const ROWS = 12;

/**
 * The Workspaces page: your workspaces, plus the lists shared with you directly.
 *
 * That second section is not a convenience. A list can be granted without its
 * workspace, in which case the workspace is invisible to you and the list hangs off
 * nothing — without "Shared with me" it would exist and be unreachable. It shows
 * the list's own slug rather than "in «Workspace»", because naming a workspace you
 * were deliberately not given would leak it.
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
    queryKey: ["workspaces", "search", wsQ, wsFirst, wsRows],
    queryFn: () =>
      api.getPage<Workspace>(
        `/workspaces/?skip=${wsFirst}&limit=${wsRows}` +
          `&q=${encodeURIComponent(wsQ.trim())}`,
      ),
    placeholderData: (prev) => prev, // don't blank the list while paging
  });
  // A shared workspace may be view-only, so gate its controls per row.
  const can = useEffectiveActionsMap(
    "workspace",
    (workspaces.data?.items ?? []).map((ws) => ws.uuid),
  );

  // A list can be shared without its workspace, which then stays invisible — so
  // these lists have no workspace to browse into and would otherwise be
  // unreachable.
  const shared = useQuery({
    queryKey: ["lists", "shared", sharedQ, sharedFirst, sharedRows],
    queryFn: () =>
      api.getPage<ListRead>(
        `/lists/?shared=true&skip=${sharedFirst}&limit=${sharedRows}` +
          `&q=${encodeURIComponent(sharedQ.trim())}`,
      ),
    placeholderData: (prev) => prev,
  });

  const shownWorkspaces = workspaces.data?.items ?? [];
  const wsTotal = workspaces.data?.total ?? 0;
  const shownShared = shared.data?.items ?? [];
  const sharedTotal = shared.data?.total ?? 0;

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

  function confirmDelete(ws: Workspace) {
    confirmDialog({
      message: `Delete workspace "${ws.display_name}" and all its lists?`,
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
        subtitle="A workspace holds a set of lists — and decides who can reach them."
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
              : "A workspace is a folder for your lists. Create one to start putting records somewhere."
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
            <div
              key={ws.uuid}
              data-tour={i === 0 ? "workspace-card" : undefined}
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
                  <i
                    className={ws.is_system ? "pi pi-inbox" : "pi pi-th-large"}
                  />
                </span>
                <span className="flex flex-col min-w-0">
                  <span className="font-medium truncate">
                    {ws.display_name}
                  </span>
                  <span className="text-sm text-muted truncate">
                    {ws.is_system
                      ? "Lists transferred to you"
                      : ws.name}
                  </span>
                </span>
              </Link>
              {/* The Incoming inbox is immutable — no rename/delete/share; the
                  recipient acts on the lists inside it, not on the box. */}
              <div className="link-card-above">
                <RowActions
                  maxVisible={1}
                  actions={[
                    {
                      key: "share",
                      icon: "pi pi-share-alt",
                      label: "Share workspace",
                      visible: !ws.is_system && can(ws.uuid, "share"),
                      onClick: () => setSharing(ws),
                    },
                    {
                      key: "edit",
                      icon: "pi pi-pencil",
                      label: "Rename workspace",
                      visible: !ws.is_system && can(ws.uuid, "configure"),
                      onClick: () => {
                        setRenaming(ws);
                        setNames({
                          displayName: ws.display_name,
                          name: ws.name,
                        });
                      },
                    },
                    {
                      key: "delete",
                      icon: "pi pi-trash",
                      label: "Delete workspace",
                      severity: "danger",
                      visible: !ws.is_system && can(ws.uuid, "delete"),
                      onClick: () => confirmDelete(ws),
                    },
                  ]}
                />
              </div>
            </div>
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
              Lists someone gave you directly, without the workspace around them.
            </p>
          </div>
          <SearchInput
            value={sharedSearch}
            onChange={setSharedSearch}
            placeholder="Search shared lists…"
          />
          {!shared.isLoading && shownShared.length === 0 && (
            <Message severity="info" text="No shared lists match that search." />
          )}
          <div className="card-grid">
            {shownShared.map((list) => (
              <div key={list.uuid} className="surface-card link-card p-3">
                <Link
                  to={`/lists/${list.uuid}`}
                  className="no-underline link-card-target flex items-start gap-3 min-w-0"
                >
                  <span className="entity-icon" aria-hidden>
                    <i className="pi pi-table" />
                  </span>
                  <span className="flex flex-col min-w-0">
                    <span className="font-medium truncate">
                      {list.display_name}
                    </span>
                    {/* Same-named lists are common here, so disambiguate by owner
                        and — only when the caller can see it — the workspace. The
                        backend sends workspace_display_name only for a viewable
                        workspace, so this never names one the caller was denied;
                        otherwise it falls back to the list's own slug. */}
                    <span className="text-sm text-muted truncate">
                      {list.owner_username
                        ? `owned by ${list.owner_username}`
                        : list.name}
                      {list.workspace_display_name &&
                        ` · in ${list.workspace_display_name}`}
                    </span>
                  </span>
                </Link>
              </div>
            ))}
          </div>
          {sharedTotal > sharedRows && (
            <Paginator
              first={sharedFirst}
              rows={sharedRows}
              totalRecords={sharedTotal}
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
        error={create.error instanceof ApiError ? create.error.message : null}
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
          {rename.error instanceof ApiError && (
            <Message severity="error" text={rename.error.message} />
          )}
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

      <DeletedWorkspacesDialog
        visible={showDeleted}
        onHide={() => setShowDeleted(false)}
      />
    </div>
  );
}
