import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Card } from "primereact/card";
import { InputText } from "primereact/inputtext";
import { IconField } from "primereact/iconfield";
import { InputIcon } from "primereact/inputicon";
import { Button } from "primereact/button";
import { Message } from "primereact/message";
import { Paginator } from "primereact/paginator";
import { AppDialog } from "../../components/AppDialog";
import { ConfirmDialog, confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { Breadcrumbs } from "../../components/Breadcrumbs";
import { NameFields } from "../../components/NameFields";
import { RowActions } from "../../components/RowActions";
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
const ROWS = 10;

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

  const [displayName, setDisplayName] = useState("");
  const [showDeleted, setShowDeleted] = useState(false);
  const create = useMutation({
    mutationFn: (name: string) =>
      api.post<Workspace>("/workspaces/", {
        name: slugify(name),
        display_name: name,
      }),
    onSuccess: (ws) => {
      setDisplayName("");
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

  function onCreate(event: FormEvent) {
    event.preventDefault();
    if (displayName.trim()) create.mutate(displayName.trim());
  }
  function confirmDelete(ws: Workspace) {
    confirmDialog({
      message: `Delete workspace "${ws.display_name}" and all its lists?`,
      header: "Confirm",
      icon: "pi pi-exclamation-triangle",
      accept: () => remove.mutate(ws.uuid),
    });
  }

  return (
    <div className="flex flex-col gap-4" style={{ maxWidth: 720 }}>
      <ConfirmDialog />
      <Breadcrumbs items={[{ label: "Workspaces" }]} />
      <div className="flex items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">Workspaces</h1>
        <Button
          label="Deleted"
          icon="pi pi-trash"
          text
          onClick={() => setShowDeleted(true)}
        />
      </div>

      <form onSubmit={onCreate} className="flex gap-2">
        <InputText
          placeholder="New workspace name"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          className="flex-1"
        />
        <Button type="submit" label="Create" icon="pi pi-plus" loading={create.isPending} />
      </form>
      {create.error instanceof ApiError && (
        <Message severity="error" text={create.error.message} />
      )}

      <IconField iconPosition="left">
        <InputIcon className="pi pi-search" />
        <InputText
          value={wsSearch}
          onChange={(e) => setWsSearch(e.target.value)}
          placeholder="Search workspaces…"
          className="w-full"
        />
      </IconField>

      {workspaces.isLoading && <p>Loading…</p>}
      {!workspaces.isLoading && shownWorkspaces.length === 0 && (
        <p>{wsSearch ? "No workspaces match that search." : "No workspaces yet."}</p>
      )}

      <div className="flex flex-col gap-2">
        {shownWorkspaces.map((ws) => (
          <Card key={ws.uuid} className="link-card">
            <div className="flex items-center justify-between">
              <Link
                to={`/workspaces/${ws.uuid}`}
                className="no-underline link-card-target"
              >
                <div className="font-medium">{ws.display_name}</div>
                <div className="text-sm text-muted">
                  {ws.is_system
                    ? "Lists other people have transferred to you"
                    : ws.name}
                </div>
              </Link>
              {/* The Incoming inbox is immutable — no rename/delete/share; the
                  recipient acts on the lists inside it, not on the box. */}
              <div className="link-card-above">
                <RowActions
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
          </Card>
        ))}
      </div>

      {wsTotal > 0 && (
        <Paginator
          first={wsFirst}
          rows={wsRows}
          totalRecords={wsTotal}
          rowsPerPageOptions={[10, 25, 50, 100]}
          onPageChange={(e) => {
            setWsFirst(e.first);
            setWsRows(e.rows);
          }}
          template="CurrentPageReport FirstPageLink PrevPageLink PageLinks NextPageLink LastPageLink RowsPerPageDropdown"
          currentPageReportTemplate="{first}–{last} of {totalRecords}"
        />
      )}

      {(sharedTotal > 0 || sharedSearch) && (
        <section className="flex flex-col gap-2 mt-2">
          <h2 className="text-lg font-medium">Shared with me</h2>
          <IconField iconPosition="left">
            <InputIcon className="pi pi-search" />
            <InputText
              value={sharedSearch}
              onChange={(e) => setSharedSearch(e.target.value)}
              placeholder="Search shared lists…"
              className="w-full"
            />
          </IconField>
          {!shared.isLoading && shownShared.length === 0 && (
            <Message severity="info" text="No shared lists match that search." />
          )}
          {shownShared.map((list) => (
            <Card key={list.uuid} className="link-card">
              <Link
                to={`/lists/${list.uuid}`}
                className="no-underline link-card-target"
              >
                <div className="font-medium">{list.display_name}</div>
                {/* Same-named lists are common here, so disambiguate by owner and —
                    only when the caller can see it — the workspace. The backend
                    sends workspace_display_name only for a viewable workspace, so
                    this never names one the caller was denied; otherwise it falls
                    back to the list's own slug. */}
                <div className="text-sm text-muted">
                  {list.owner_username
                    ? `owned by ${list.owner_username}`
                    : list.name}
                  {list.workspace_display_name &&
                    ` · in ${list.workspace_display_name}`}
                </div>
              </Link>
            </Card>
          ))}
          {sharedTotal > 0 && (
            <Paginator
              first={sharedFirst}
              rows={sharedRows}
              totalRecords={sharedTotal}
              rowsPerPageOptions={[10, 25, 50, 100]}
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
        style={{ width: 380 }}
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
          <div className="flex justify-end gap-2">
            <Button label="Cancel" text onClick={() => setRenaming(null)} />
            <Button
              label="Save"
              onClick={() => rename.mutate()}
              loading={rename.isPending}
              disabled={!names.displayName.trim() || !isValidName(names.name)}
            />
          </div>
        </div>
      </AppDialog>

      <DeletedWorkspacesDialog
        visible={showDeleted}
        onHide={() => setShowDeleted(false)}
      />
    </div>
  );
}
