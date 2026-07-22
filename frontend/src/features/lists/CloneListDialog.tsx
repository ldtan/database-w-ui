import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "primereact/button";
import { Checkbox } from "primereact/checkbox";

import { AppDialog } from "../../components/AppDialog";
import { api, ApiError } from "../../lib/api";
import { useToast } from "../../lib/toast";
import type { ListRead } from "../../lib/types";

interface Props {
  list: ListRead | null;
  onHide: () => void;
  // Called after a successful clone so the caller can refetch the workspace's lists.
  onDone: () => void;
}

/**
 * Duplicate a list into its own workspace. Structure — columns, views, forms — is
 * always copied; records come along only when the box is ticked (structure-only is
 * the default, since a clone is often a template). The copy lands with an
 * auto-suggested free name (`tasks` → `tasks_copy`) and no sharing: it is a fresh
 * list you own, private like any you create.
 */
export function CloneListDialog({ list, onHide, onDone }: Props) {
  const toast = useToast();
  const [withRecords, setWithRecords] = useState(false);
  const active = !!list;

  function close() {
    setWithRecords(false);
    onHide();
  }

  const clone = useMutation({
    mutationFn: () =>
      api.post<ListRead>(`/lists/${list!.uuid}/clone`, {
        with_records: withRecords,
      }),
    onSuccess: (created) => {
      toast.success("List duplicated", created.display_name);
      setWithRecords(false);
      onDone();
    },
    onError: (err) =>
      toast.error(
        "Could not duplicate list",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  return (
    <AppDialog
      header={`Duplicate “${list?.display_name ?? ""}”`}
      visible={active}
      onHide={close}
      style={{ width: 420 }}
    >
      <div className="flex flex-col gap-4">
        <span className="text-sm" style={{ color: "var(--text-muted)" }}>
          Make a copy in this workspace. Its columns, views and forms are copied;
          sharing is not, so the copy starts private.
        </span>
        <div className="flex items-center gap-2">
          <Checkbox
            inputId="clone-with-records"
            checked={withRecords}
            onChange={(e) => setWithRecords(!!e.checked)}
          />
          <label htmlFor="clone-with-records" className="cursor-pointer">
            Also copy the records
          </label>
        </div>
        <div className="flex justify-end gap-2">
          <Button label="Cancel" text onClick={close} />
          <Button
            label="Duplicate"
            icon="pi pi-copy"
            onClick={() => clone.mutate()}
            loading={clone.isPending}
          />
        </div>
      </div>
    </AppDialog>
  );
}
