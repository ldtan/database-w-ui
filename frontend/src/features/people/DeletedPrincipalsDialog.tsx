import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { QueryKey } from "@tanstack/react-query";
import { DataTable } from "primereact/datatable";
import { Column as DataColumn } from "primereact/column";
import { Button } from "primereact/button";
import { Message } from "primereact/message";
import { Paginator } from "primereact/paginator";
import { confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { useToast } from "../../lib/toast";
import type { BinnedPrincipal } from "../../lib/types";
import { AppDialog } from "../../components/AppDialog";

const ROWS = 10;

/** The two principals, each with a bin behind the same three routes under
 *  `/auth/{kind}`: `/deleted`, `/{uuid}/restore`, `/{uuid}/permanent`. */
export type PrincipalKind = "users" | "groups";

interface KindSpec {
  header: string;
  /** Lower-case and singular: goes into toasts and error summaries. */
  noun: string;
  blurb: string;
  purgePrompt: string;
  /** What a restore, or a purge, changes elsewhere on screen. */
  touched: QueryKey[];
}

const KINDS: Record<PrincipalKind, KindSpec> = {
  users: {
    header: "Deleted accounts",
    noun: "account",
    blurb:
      "Restoring an account is a real undo: it keeps its uuid, so every cell naming " +
      "it resolves again, and its group memberships were never thrown away.",
    purgePrompt:
      "Permanently delete this account, with its grants, profile and sign-in tokens? " +
      "This cannot be undone.",
    // The listing, the person records shown beside it, and the `user` cells that
    // render a deleted account as "name (deleted)".
    touched: [["users"], ["people"], ["principal-labels"]],
  },
  groups: {
    header: "Deleted groups",
    noun: "group",
    blurb:
      "A restored group starts conferring its grants on its members again the moment " +
      "it comes back — the members were kept, and only hidden while it was gone.",
    purgePrompt:
      "Permanently delete this group and the grants it holds? This cannot be undone.",
    // Restoring a group you are in changes what you may do, so the effective-action
    // cache the pages gate on has to go with it.
    touched: [["groups"], ["principal-labels"], ["effective"]],
  },
};

/**
 * The bin for deleted accounts and groups.
 *
 * One component for both, as the API is one module behind two prefixes. It is the
 * last of the app's bins to get a surface, and the only one that is **superuser-
 * only** rather than gated on the data: accounts and groups sit *under* the
 * permission model — a group is a principal in the grant table — so restoring one
 * hands back every grant it held. The caller checks that before offering this,
 * matching the Add user / New group buttons beside it.
 *
 * Destroying an account **409s while it still owns workspaces**: `owner_id` is a
 * non-null foreign key, so the alternative to refusing would be taking somebody's
 * data down with the account. The message names the workspaces, and it reaches the
 * user unchanged through the error toast — that table is the whole point of the
 * refusal, so it must not be flattened into "could not delete".
 */
export function DeletedPrincipalsDialog({
  kind,
  visible,
  onHide,
}: {
  kind: PrincipalKind;
  visible: boolean;
  onHide: () => void;
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const spec = KINDS[kind];
  const Noun = spec.noun[0].toUpperCase() + spec.noun.slice(1);

  const [first, setFirst] = useState(0);
  const [rows, setRows] = useState(ROWS);
  // Reopening the dialog starts at the top rather than wherever it was left.
  useEffect(() => {
    if (visible) setFirst(0);
  }, [visible]);

  // Under the live key (`["users", …]`), so the deletes already on the People page
  // refresh the bin by prefix match without knowing it exists.
  const binKey: QueryKey = [kind, "deleted", first, rows];

  const deleted = useQuery({
    queryKey: binKey,
    queryFn: () =>
      api.getPage<BinnedPrincipal>(
        `/auth/${kind}/deleted?skip=${first}&limit=${rows}`,
      ),
    enabled: visible,
    placeholderData: (prev) => prev, // don't blank the table while paging
  });

  const items = deleted.data?.items ?? [];
  const total = deleted.data?.total ?? 0;

  const invalidate = () => {
    for (const key of spec.touched) {
      queryClient.invalidateQueries({ queryKey: key });
    }
  };

  const restore = useMutation({
    mutationFn: (uuid: string) => api.post(`/auth/${kind}/${uuid}/restore`),
    onSuccess: () => {
      invalidate();
      toast.success(`${Noun} restored`);
    },
    onError: (err) =>
      toast.error(
        `Could not restore ${spec.noun}`,
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  const purge = useMutation({
    mutationFn: (uuid: string) => api.del(`/auth/${kind}/${uuid}/permanent`),
    onSuccess: () => {
      // Unlike the other bins this changes more than the bin: the cells that read
      // "name (deleted)" have nothing left to resolve, so the labels go too.
      invalidate();
      toast.success(`${Noun} permanently deleted`);
    },
    onError: (err) =>
      toast.error(
        `Could not delete ${spec.noun}`,
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  const confirmPurge = (uuid: string) =>
    confirmDialog({
      message: spec.purgePrompt,
      header: "Delete permanently",
      icon: "pi pi-exclamation-triangle",
      acceptClassName: "p-button-danger",
      accept: () => purge.mutate(uuid),
    });

  return (
    <AppDialog
      header={spec.header}
      visible={visible}
      onHide={onHide}
      style={{ width: 640 }}
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>
          {spec.blurb} They are removed for good once the retention window passes.
        </p>

        {deleted.isLoading && <p>Loading…</p>}
        {!deleted.isLoading && items.length === 0 && (
          <Message severity="info" text="The bin is empty." />
        )}

        {total > 0 && (
          <>
            <Paginator
              first={first}
              rows={rows}
              totalRecords={total}
              rowsPerPageOptions={[10, 25, 50, 100]}
              onPageChange={(e) => {
                setFirst(e.first);
                setRows(e.rows);
              }}
              template="CurrentPageReport FirstPageLink PrevPageLink PageLinks NextPageLink LastPageLink RowsPerPageDropdown"
              currentPageReportTemplate="{first}–{last} of {totalRecords}"
            />

            <DataTable value={items} dataKey="uuid" stripedRows>
              <DataColumn
                header="Name"
                body={(row: BinnedPrincipal) => (
                  <div>
                    <div className="font-medium">{row.name}</div>
                    <div
                      className="text-sm"
                      style={{ color: "var(--text-muted)" }}
                    >
                      {row.detail}
                    </div>
                  </div>
                )}
              />
              <DataColumn
                header="Deleted"
                body={(row: BinnedPrincipal) => (
                  <span
                    className="text-sm whitespace-nowrap"
                    style={{ color: "var(--text-muted)" }}
                  >
                    {new Date(row.deleted_at).toLocaleString()}
                  </span>
                )}
              />
              <DataColumn
                header=""
                style={{ width: "11rem" }}
                body={(row: BinnedPrincipal) => (
                  <div className="flex gap-1">
                    <Button
                      label="Restore"
                      icon="pi pi-replay"
                      text
                      size="small"
                      onClick={() => restore.mutate(row.uuid)}
                      loading={restore.isPending && restore.variables === row.uuid}
                    />
                    <Button
                      icon="pi pi-trash"
                      text
                      size="small"
                      severity="danger"
                      aria-label="Delete permanently"
                      tooltip="Delete permanently"
                      onClick={() => confirmPurge(row.uuid)}
                      loading={purge.isPending && purge.variables === row.uuid}
                    />
                  </div>
                )}
              />
            </DataTable>
          </>
        )}
      </div>
    </AppDialog>
  );
}
