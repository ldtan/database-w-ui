import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { SelectButton } from "primereact/selectbutton";
import { Dropdown } from "primereact/dropdown";
import { Button } from "primereact/button";

import { AppDialog } from "../../components/AppDialog";
import { api, ApiError } from "../../lib/api";
import { Field } from "../../components/Field";
import { UserPicker } from "../../components/UserPicker";
import { useToast } from "../../lib/toast";
import { useEffectiveActionsMap } from "../permissions/useEffectiveActions";
import type { ListRead, Workspace } from "../../lib/types";

interface Props {
  list: ListRead | null;
  // The workspace the list is in now — never offered as a move target.
  currentWorkspaceUuid: string;
  // Only the list's owner may transfer it (the backend enforces this too). When
  // false the dialog offers moving between workspaces but not the transfer mode.
  canTransfer: boolean;
  onHide: () => void;
  // Called after a successful move/transfer so the caller can refetch.
  onDone: () => void;
}

/**
 * Relocate a list: **move** it to another workspace you can configure, or **transfer**
 * it to another user — which drops it into that user's immutable "Incoming" workspace
 * and makes them the owner.
 *
 * The two are the same underlying operation (a list belongs to whoever owns its
 * workspace), split because their authorization differs: a move needs `configure` on
 * the destination, a transfer needs only that you own the list — you can hand a list
 * to someone who has shared nothing with you, which a plain move cannot.
 */
export function MoveOrTransferDialog({
  list,
  currentWorkspaceUuid,
  canTransfer,
  onHide,
  onDone,
}: Props) {
  const toast = useToast();
  const [mode, setMode] = useState<"move" | "transfer">("move");
  const [targetWorkspace, setTargetWorkspace] = useState<string | null>(null);
  const [targetUser, setTargetUser] = useState<string | null>(null);

  const active = !!list;

  // Candidate destinations: workspaces the caller can configure, minus the one the
  // list is in and every immutable inbox (a list is never *moved* into an inbox —
  // that only happens via a transfer).
  const workspaces = useQuery({
    queryKey: ["workspaces", "move-targets"],
    queryFn: () => api.get<Workspace[]>(`/workspaces/?limit=1000`),
    enabled: active && mode === "move",
  });
  const candidates = (workspaces.data ?? []).filter(
    (w) => !w.is_system && w.uuid !== currentWorkspaceUuid,
  );
  const canWs = useEffectiveActionsMap(
    "workspace",
    candidates.map((w) => w.uuid),
  );
  const workspaceOptions = candidates
    .filter((w) => canWs(w.uuid, "configure"))
    .map((w) => ({ label: w.display_name, value: w.uuid }));

  function reset() {
    setTargetWorkspace(null);
    setTargetUser(null);
  }
  function close() {
    reset();
    setMode("move");
    onHide();
  }
  function finish(message: string, detail?: string) {
    toast.success(message, detail);
    reset();
    onDone();
  }

  const move = useMutation({
    mutationFn: () =>
      api.patch(`/lists/${list!.uuid}`, { workspace_uuid: targetWorkspace }),
    onSuccess: () => finish("List moved"),
    onError: (err) =>
      toast.error(
        "Could not move list",
        err instanceof ApiError ? err.message : undefined,
      ),
  });
  const transfer = useMutation({
    mutationFn: () =>
      api.post(`/lists/${list!.uuid}/transfer`, { user_uuid: targetUser }),
    onSuccess: () =>
      finish(
        "List transferred",
        "It now lives in the recipient's Incoming workspace.",
      ),
    onError: (err) =>
      toast.error(
        "Could not transfer list",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  const modeOptions = [
    { label: "Move to workspace", value: "move" },
    { label: "Transfer to user", value: "transfer" },
  ];

  return (
    <AppDialog
      header={`Move or transfer “${list?.display_name ?? ""}”`}
      visible={active}
      onHide={close}
      style={{ width: 460 }}
      // One footer for both modes rather than one inside each: the buttons are
      // the same act at the same place, and only their label and what they are
      // waiting for changes with the mode.
      footer={
        <>
          <Button label="Cancel" text onClick={close} />
          {mode === "move" ? (
            <Button
              label="Move list"
              icon="pi pi-arrow-right"
              onClick={() => move.mutate()}
              loading={move.isPending}
              disabled={!targetWorkspace}
            />
          ) : (
            <Button
              label="Transfer list"
              icon="pi pi-send"
              severity="warning"
              onClick={() => transfer.mutate()}
              loading={transfer.isPending}
              disabled={!targetUser}
            />
          )}
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {canTransfer && (
          <SelectButton
            value={mode}
            options={modeOptions}
            // The buttons are exclusive; ignore the null a re-click of the active
            // one would emit.
            onChange={(e) => e.value && setMode(e.value)}
            allowEmpty={false}
          />
        )}

        {mode === "move" ? (
          <Field
            label="Destination workspace"
            hint="It keeps its sharing and everyone who already has access."
          >
            <Dropdown
              value={targetWorkspace}
              options={workspaceOptions}
              onChange={(e) => setTargetWorkspace(e.value)}
              placeholder={
                workspaces.isLoading ? "Loading…" : "Select a workspace…"
              }
              filter
              emptyMessage="No other workspaces you can move this into"
              className="w-full"
            />
          </Field>
        ) : (
          <Field
            label="New owner"
            hint="It moves into their Incoming workspace and becomes theirs — you lose access, but everyone you shared it with keeps theirs."
          >
            <UserPicker
              value={targetUser}
              onChange={setTargetUser}
              enabled={active && mode === "transfer"}
            />
          </Field>
        )}
      </div>
    </AppDialog>
  );
}
