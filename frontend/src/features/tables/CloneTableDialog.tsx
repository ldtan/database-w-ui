import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "primereact/button";
import { CheckboxField } from "../../components/Field";

import { AppDialog } from "../../components/AppDialog";
import { api, ApiError } from "../../lib/api";
import { useToast } from "../../lib/toast";
import type { TableRead } from "../../lib/types";

interface Props {
  table: TableRead | null;
  onHide: () => void;
  // Called after a successful clone so the caller can refetch the workspace's tables.
  onDone: () => void;
}

/**
 * Duplicate a table into its own workspace. Structure — columns, views, forms — is
 * always copied; records come along only when the box is ticked (structure-only is
 * the default, since a clone is often a template). The copy lands with an
 * auto-suggested free name (`tasks` → `tasks_copy`) and no sharing: it is a fresh
 * table you own, private like any you create.
 */
export function CloneTableDialog({ table, onHide, onDone }: Props) {
  const toast = useToast();
  const [withRecords, setWithRecords] = useState(false);
  const active = !!table;

  function close() {
    setWithRecords(false);
    onHide();
  }

  const clone = useMutation({
    mutationFn: () =>
      api.post<TableRead>(`/tables/${table!.uuid}/clone`, {
        with_records: withRecords,
      }),
    onSuccess: (created) => {
      toast.success("Table duplicated", created.display_name);
      setWithRecords(false);
      onDone();
    },
    onError: (err) =>
      toast.error(
        "Could not duplicate table",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  return (
    <AppDialog
      header={`Duplicate “${table?.display_name ?? ""}”`}
      visible={active}
      onHide={close}
      style={{ width: 420 }}
      footer={
        <>
          <Button label="Cancel" text onClick={close} />
          <Button
            label="Duplicate"
            icon="pi pi-copy"
            onClick={() => clone.mutate()}
            loading={clone.isPending}
          />
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="field-hint">
          Make a copy in this workspace. Its columns, views and forms are copied;
          sharing is not, so the copy starts private.
        </p>
        <CheckboxField
          inputId="clone-with-records"
          checked={withRecords}
          onChange={setWithRecords}
          label="Also copy the records"
        />
      </div>
    </AppDialog>
  );
}
