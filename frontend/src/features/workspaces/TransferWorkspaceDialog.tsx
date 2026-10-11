import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "primereact/button";
import { Message } from "primereact/message";

import { AppDialog } from "../../components/AppDialog";
import { Field } from "../../components/Field";
import { UserPicker } from "../../components/UserPicker";
import { api, ApiError } from "../../lib/api";
import { useToast } from "../../lib/toast";
import type { Workspace } from "../../lib/types";

interface Props {
  workspace: Workspace | null;
  onHide: () => void;
  /** Called after a successful transfer so the caller can refetch — the workspace
   *  leaves the caller's listing entirely, so this is not optional. */
  onDone: () => void;
}

/**
 * Hand a workspace, and every table in it, to another user.
 *
 * There is no "move" counterpart here the way there is for a table: a workspace is
 * itself the ownership boundary, so the only thing to change is who owns it. That
 * also makes this a heavier act than a table transfer — the whole tree goes at once
 * and the giver keeps nothing — so the dialog states the cost rather than relying
 * on the button's colour to imply it.
 *
 * Owner-only, enforced by the backend; the page only offers it on workspaces the
 * caller owns.
 */
export function TransferWorkspaceDialog({ workspace, onHide, onDone }: Props) {
  const toast = useToast();
  const [targetUser, setTargetUser] = useState<string | null>(null);
  const active = !!workspace;

  function close() {
    setTargetUser(null);
    onHide();
  }

  const transfer = useMutation({
    mutationFn: () =>
      api.post(`/workspaces/${workspace!.uuid}/transfer`, {
        user_uuid: targetUser,
      }),
    onSuccess: () => {
      toast.success(
        "Workspace transferred",
        "It and its tables now belong to the new owner.",
      );
      setTargetUser(null);
      onDone();
    },
    onError: (err) =>
      toast.error(
        "Could not transfer workspace",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  return (
    <AppDialog
      header={`Transfer “${workspace?.display_name ?? ""}”`}
      visible={active}
      onHide={close}
      style={{ width: 460 }}
      footer={
        <>
          <Button label="Cancel" text onClick={close} />
          <Button
            label="Transfer workspace"
            icon="pi pi-send"
            severity="warning"
            onClick={() => transfer.mutate()}
            loading={transfer.isPending}
            disabled={!targetUser}
          />
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {/* Named before the picker, not after: which tables go with it is what
            decides whether to do this at all. */}
        <Message
          severity="warn"
          text="Every table in this workspace goes with it. You will lose access to all of them."
        />
        <Field
          label="New owner"
          hint="It keeps its name and links, and everyone you shared it with keeps their access."
        >
          <UserPicker
            value={targetUser}
            onChange={setTargetUser}
            enabled={active}
          />
        </Field>
      </div>
    </AppDialog>
  );
}
