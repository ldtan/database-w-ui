import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "primereact/button";
import { Checkbox } from "primereact/checkbox";
import { Message } from "primereact/message";

import { AppDialog } from "../../components/AppDialog";
import { api, ApiError } from "../../lib/api";
import { useToast } from "../../lib/toast";
import type { Column, Form, ListItem } from "../../lib/types";
import { LookupField } from "../lookups/LookupControls";
import { RecordFields } from "./RecordFields";
import {
  buildRecordData,
  deserialize,
  resolveFields,
  resolveMergedFields,
} from "./recordValues";

interface MergedItemRead {
  item: ListItem;
  linked_item: ListItem | null;
}

interface Props {
  listUuid: string;
  columns: Column[];
  /** The merged form — `config.merge` is what makes it one. */
  form: Form;
  /** The record being edited, or null when adding. */
  item?: ListItem | null;
  visible: boolean;
  onHide: () => void;
}

/**
 * Add or edit a record that spans two lists.
 *
 * A separate dialog rather than a mode on `RecordFormDialog`, for two reasons that are
 * both about that component rather than this one. Its mutation posts to two fixed URLs
 * and holds one value map, so merging would mean branching every line of it; and it is
 * also driven by the QR scanner, which fills a single-list record and must not start
 * carrying a second list's concerns.
 *
 * **The link is chosen, not typed.** The picker writes the linked record's uuid, and
 * leaving it empty means "make a new one from the fields below" — which is the whole
 * gesture: one submit, two records. The server writes the link cell from the record it
 * just created, so nothing here can point a row at a record it did not write.
 */
export function MergedRecordFormDialog({
  listUuid,
  columns,
  form,
  item,
  visible,
  onHide,
}: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const merge = form.config.merge ?? null;
  const linkUuid = merge?.link_column_uuid ?? "";

  const [values, setValues] = useState<Record<string, unknown>>({});
  const [linkedValues, setLinkedValues] = useState<Record<string, unknown>>({});
  const [picked, setPicked] = useState<string | null>(null);
  const [updateLinked, setUpdateLinked] = useState(false);

  const linkColumn = columns.find((c) => c.uuid === linkUuid) ?? null;
  const targetUuid = linkColumn?.config.target_list_uuid ?? null;

  /**
   * The linked list's schema. `retry: false` because a 403 here is not a failure to
   * recover from — it is how a merged form degrades for someone who may use this list
   * but not the one it borrows from. The borrowed block simply does not render.
   */
  const linkedColumns = useQuery({
    queryKey: ["columns", targetUuid],
    queryFn: () => api.get<Column[]>(`/lists/${targetUuid}/columns/`),
    enabled: visible && !!targetUuid,
    retry: false,
  });

  /** The linked record's current values, for editing. */
  const linkedItem = useQuery({
    queryKey: ["item", targetUuid, picked],
    queryFn: () => api.get<ListItem>(`/lists/${targetUuid}/items/${picked}`),
    enabled: visible && !!targetUuid && !!picked && !!item,
    retry: false,
  });

  const fields = resolveFields(columns, form).filter((f) => f.column.uuid !== linkUuid);
  const borrowed = resolveMergedFields(linkedColumns.data ?? [], merge);

  useEffect(() => {
    if (!visible) return;
    setUpdateLinked(false);
    if (!item) {
      setValues({});
      setLinkedValues({});
      setPicked(null);
      return;
    }
    const initial: Record<string, unknown> = {};
    for (const column of columns) {
      const raw = item.data?.[column.uuid];
      if (raw !== undefined && raw !== null) {
        initial[column.uuid] = deserialize(column, raw);
      }
    }
    setValues(initial);
    const linked = item.data?.[linkUuid];
    setPicked(typeof linked === "string" ? linked : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, item?.uuid]);

  // Seeded separately: the linked record arrives on its own request, after the effect
  // above has already run.
  useEffect(() => {
    const row = linkedItem.data;
    if (!row || !linkedColumns.data) return;
    const initial: Record<string, unknown> = {};
    for (const column of linkedColumns.data) {
      const raw = row.data?.[column.uuid];
      if (raw !== undefined && raw !== null) {
        initial[column.uuid] = deserialize(column, raw);
      }
    }
    setLinkedValues(initial);
  }, [linkedItem.data, linkedColumns.data]);

  const persist = useMutation({
    mutationFn: () => {
      const data = buildRecordData(fields, values, !!item);
      const linked_data = buildRecordData(borrowed, linkedValues, !!item);
      if (item) {
        return api.patch<MergedItemRead>(
          `/lists/${listUuid}/items/${item.uuid}/merged`,
          { form_uuid: form.uuid, data, linked_data },
        );
      }
      return api.post<MergedItemRead>(`/lists/${listUuid}/items/merged`, {
        form_uuid: form.uuid,
        data,
        linked_data,
        linked_uuid: picked,
        update_linked: updateLinked,
      });
    },
    onSuccess: () => {
      // Both lists moved, so both caches have to. The linked list may well be open in
      // another tab of the same session.
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      if (targetUuid) {
        queryClient.invalidateQueries({ queryKey: ["items", targetUuid] });
      }
      toast.success(item ? "Record updated" : "Record added");
      onHide();
    },
  });

  const schemaRefused = linkedColumns.isError;
  const editingExisting = !item && !!picked;

  return (
    <AppDialog
      header={item ? "Edit record" : "Add record"}
      visible={visible}
      onHide={onHide}
      style={{ width: 560 }}
      footer={
        <>
          <Button label="Cancel" text onClick={onHide} />
          <Button
            label={item ? "Save" : "Add record"}
            icon={item ? "pi pi-check" : "pi pi-plus"}
            onClick={() => persist.mutate()}
            loading={persist.isPending}
          />
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {persist.error instanceof ApiError && (
          <Message severity="error" text={persist.error.message} />
        )}

        {linkColumn && (
          <div className="flex flex-col gap-1">
            <label className="field-label">{linkColumn.display_name}</label>
            <LookupField
              listUuid={listUuid}
              column={linkColumn}
              value={picked}
              onChange={(next) => setPicked((next as string) ?? null)}
              disabled={!!item}
            />
            {!item && !picked && (
              <small className="field-hint">
                Leave empty to create a new record from the fields below.
              </small>
            )}
            {item && (
              <small className="field-hint">
                Change which record this is linked to from the grid.
              </small>
            )}
          </div>
        )}

        {schemaRefused && (
          <Message
            severity="warn"
            text="You do not have access to the linked list, so its fields are not shown. The rest of this record still saves."
          />
        )}

        {fields.length > 0 && (
          <RecordFields
            listUuid={listUuid}
            fields={fields}
            columns={columns}
            values={values}
            onChange={setValues}
          />
        )}

        {borrowed.length > 0 && targetUuid && (
          <>
            <div className="flex items-center gap-2">
              <span className="field-label">Linked record</span>
              <span className="flex-1 border-t" />
            </div>

            {/*
              Writing through to a record somebody else picked is opt-in on a create,
              because with shared linking on it may be the record other rows are
              already showing — and one edit changes all of them at once.
            */}
            {editingExisting && (
              <div className="flex flex-col gap-1">
                <div className="flex items-center gap-2">
                  <Checkbox
                    inputId="update-linked"
                    checked={updateLinked}
                    onChange={(e) => setUpdateLinked(!!e.checked)}
                  />
                  <label htmlFor="update-linked">
                    Also update the linked record
                  </label>
                </div>
                {updateLinked && merge?.allow_shared && (
                  <Message
                    severity="warn"
                    text="Other rows may be linked to this record. Saving changes it for all of them."
                  />
                )}
              </div>
            )}

            <RecordFields
              listUuid={targetUuid}
              fields={borrowed}
              columns={linkedColumns.data ?? []}
              values={linkedValues}
              onChange={setLinkedValues}
            />
          </>
        )}
      </div>
    </AppDialog>
  );
}
