import { useQuery } from "@tanstack/react-query";

import { api } from "../../lib/api";
import type { Column, Form, TableRecord, TableRead } from "../../lib/types";
import { RecordFormDialog } from "../records/RecordFormDialog";
import { resolveDisplayColumn } from "./displayColumn";

interface Props {
  /** The lookup's target table — the one gaining a row, not the one being edited. */
  targetTableUuid: string;
  /** What was typed into the picker, which becomes the new row's label. */
  term: string;
  /** The referring lookup's `display_column_uuid`, if it set one. Unset is the
   *  common case and falls back the way the server does. */
  displayColumnUuid?: string | null;
  /** Nested lookup: the target column that must point back at the parent's row,
   *  and what the parent cell holds. Both null on a plain lookup. */
  filterColumnUuid?: string | null;
  parentValues?: string[];
  visible: boolean;
  onHide: () => void;
  /** The new row's uuid, once it exists. */
  onCreated: (uuid: string) => void;
}

/**
 * Add a row to a lookup's target table without leaving the record being filled in.
 *
 * It is the target table's own create form, rendered where the picker was — not a
 * shortcut around it. That matters more than it sounds: the target may have
 * required columns of its own, and a "quick create" writing nothing but the typed
 * label would be refused by the write path with a message about a column that is
 * not on screen. Everything else follows from reusing `RecordFormDialog` — field
 * order, read-only fields, conditions and the `POST` itself are the ones the
 * target table already had.
 *
 * **Permissions are not checked here and must not be.** The caller only offers
 * this when the user has `create` on the target, and `POST /tables/{it}/records/`
 * enforces the same thing regardless. Two independent checks, neither of them
 * this component's business.
 *
 * The queries are the target's, so a caller with `create` — which confers `view`
 * — can always run them. They are keyed exactly as the target table's own page
 * keys them, so opening this dialog warms that page's cache rather than
 * duplicating it.
 */
export function CreateTargetRowDialog({
  targetTableUuid,
  term,
  displayColumnUuid,
  filterColumnUuid,
  parentValues,
  visible,
  onHide,
  onCreated,
}: Props) {
  // `enabled` on all three: this mounts alongside every lookup field that offers
  // creation, and most are never opened.
  const table = useQuery({
    queryKey: ["table", targetTableUuid],
    queryFn: () => api.get<TableRead>(`/tables/${targetTableUuid}`),
    enabled: visible,
  });
  const columns = useQuery({
    queryKey: ["columns", targetTableUuid],
    queryFn: () => api.get<Column[]>(`/tables/${targetTableUuid}/columns/`),
    enabled: visible,
  });
  const forms = useQuery({
    queryKey: ["forms", targetTableUuid],
    queryFn: () => api.get<Form[]>(`/tables/${targetTableUuid}/forms/`),
    enabled: visible,
  });

  const cols = columns.data ?? [];
  const createForm = (forms.data ?? []).find((f) => f.type === "create") ?? null;

  // The typed text goes into whichever column supplies this lookup's label, so
  // that the row comes back from the search under the name it was created with.
  const labelKey = resolveDisplayColumn(
    displayColumnUuid,
    table.data?.primary_column,
    cols,
  );

  const prefill: Record<string, unknown> = {};
  if (labelKey) prefill[labelKey] = term;
  // A nested lookup only offers rows whose filter cell matches the parent's, and
  // the write path enforces that on the record being saved — so a row created
  // without it would exist, be selected, and then fail the outer save with "does
  // not belong to the selected <parent>". Filling it in is what makes the new row
  // usable for the cell it was created for.
  if (filterColumnUuid && parentValues && parentValues.length > 0) {
    // The filter column is a lookup of the target's, so it has its own shape.
    // Writing a bare string into a multi-valued one would be refused, and a table
    // into a single-valued one likewise; any one of the parent's rows satisfies
    // the check, which asks for an intersection rather than an equality.
    const filterColumn = cols.find((c) => c.uuid === filterColumnUuid);
    prefill[filterColumnUuid] = filterColumn?.config.multiple
      ? parentValues
      : parentValues[0];
  }

  if (!visible) return null;
  // The dialog is only as good as the form behind it; opening an empty one over a
  // table whose schema has not arrived would look like a table with no fields.
  if (columns.isPending || forms.isPending || table.isPending) return null;

  return (
    <RecordFormDialog
      tableUuid={targetTableUuid}
      columns={cols}
      form={createForm}
      item={null}
      prefill={prefill}
      visible
      onHide={onHide}
      header={`Add to ${table.data?.display_name ?? "the linked table"}`}
      onCreated={(created: TableRecord) => onCreated(created.uuid)}
    />
  );
}
