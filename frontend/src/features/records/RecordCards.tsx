import { useEffect, useState } from "react";
import { Checkbox } from "primereact/checkbox";
import { Paginator } from "primereact/paginator";

import { RowActions } from "../../components/RowActions";

import type { Column, ListItem, PermissionAction } from "../../lib/types";
import type { PrincipalType } from "../principals/PrincipalControls";
import { renderCell } from "./renderCell";

interface Props {
  listUuid: string;
  items: ListItem[];
  /** Visible columns, in display order — the same list the grid renders. */
  columns: Column[];
  loading: boolean;
  principalUuids: Record<PrincipalType, string[]>;
  lookupUuids: Map<string, string[]>;
  selection: ListItem[];
  onSelectionChange: (next: ListItem[]) => void;
  /** Null unless the view groups; cards then carry the same subheaders the grid does. */
  groupColumn: Column | null;
  /** Changes when the *question* changes (the view, the search) — not when the
   *  answer is merely refetched. Paging returns to the top on a change. */
  resetKey: string;
  pageSize: number;
  onPageSize: (rows: number) => void;
  can: (uuid: string, action: PermissionAction) => boolean;
  onEdit: (item: ListItem) => void;
  onShare: (item: ListItem) => void;
  onDelete: (item: ListItem) => void;
}

/**
 * Records as cards, for a screen too narrow to be a table.
 *
 * The grid does not shrink to a phone: twelve columns across 390px is twelve
 * slivers, and scrolling sideways to read one record is not reading. A card puts a
 * single record's fields in a column — label beside value — so the unit you scroll
 * through is the record, which is the thing you came to look at.
 *
 * It is a second *layout*, not a second implementation: values go through the same
 * `renderCell` the grid uses, and it drives the same selection, the same page size
 * and the same row actions.
 *
 * Sorting comes in already applied: there is no column header to tap, so the page
 * sorts the records first — through the same `sortRows` the grid's columns use, so
 * neither layout can disagree with the other about what "sorted by Name" means.
 */
export function RecordCards({
  listUuid,
  items,
  columns,
  loading,
  principalUuids,
  lookupUuids,
  selection,
  onSelectionChange,
  groupColumn,
  resetKey,
  pageSize,
  onPageSize,
  can,
  onEdit,
  onShare,
  onDelete,
}: Props) {
  const [first, setFirst] = useState(0);

  // A search or a view change asks a different question, so it starts at the top.
  // Keyed on *that*, not on the `items` array: react-query hands back a new array
  // after every mutation and every background refetch, so keying on its identity
  // threw you back to page 1 whenever you edited a record — the grid, which keeps
  // its own paging state, does not.
  useEffect(() => setFirst(0), [resetKey]);

  const selected = new Set(selection.map((item) => item.uuid));
  // Deleting the last records of the last page leaves `first` past the end, which
  // would render an empty page with no way back to the records. Land on the new
  // last page instead.
  const lastPageFirst = Math.max(
    0,
    Math.floor((items.length - 1) / pageSize) * pageSize,
  );
  const start = Math.min(first, lastPageFirst);
  const page = items.slice(start, start + pageSize);

  function toggle(item: ListItem, checked: boolean) {
    onSelectionChange(
      checked
        ? [...selection, item]
        : selection.filter((s) => s.uuid !== item.uuid),
    );
  }

  if (loading) return <p>Loading…</p>;
  if (items.length === 0) return <p>No records yet.</p>;

  return (
    <div className="flex flex-col gap-3">
      {page.map((item, i) => {
        // The server orders by the group column, so a group starts wherever the
        // value differs from the row before it — the same subheader the grid draws,
        // without a second notion of what a group is.
        const groupValue = groupColumn ? item.data?.[groupColumn.uuid] : null;
        const startsGroup =
          !!groupColumn &&
          (i === 0 || page[i - 1].data?.[groupColumn.uuid] !== groupValue);

        return (
          <div key={item.uuid} className="flex flex-col gap-2">
            {startsGroup && groupColumn && (
              <div className="font-semibold pt-1">
                {groupColumn.display_name}:{" "}
                {renderCell(
                  groupColumn,
                  item,
                  listUuid,
                  principalUuids,
                  lookupUuids,
                )}
              </div>
            )}
            <div
              className="rounded border p-3 flex flex-col gap-2"
              style={{
                borderColor: "var(--border)",
                background: selected.has(item.uuid)
                  ? "var(--surface-hover, transparent)"
                  : undefined,
              }}
            >
              {columns.map((col) => (
                <div key={col.uuid} className="flex gap-2 items-baseline">
                  <span
                    className="text-sm shrink-0"
                    style={{ color: "var(--text-muted)", width: "38%" }}
                  >
                    {col.display_name}
                  </span>
                  <span className="min-w-0 flex-1 break-words">
                    {renderCell(
                      col,
                      item,
                      listUuid,
                      principalUuids,
                      lookupUuids,
                    )}
                  </span>
                </div>
              ))}

              <div
                className="flex items-center gap-1 pt-2 border-t"
                style={{ borderColor: "var(--border)" }}
              >
                <Checkbox
                  inputId={`sel-${item.uuid}`}
                  checked={selected.has(item.uuid)}
                  onChange={(e) => toggle(item, !!e.checked)}
                />
                <label htmlFor={`sel-${item.uuid}`} className="text-sm mr-auto">
                  Select
                </label>
                <RowActions
                  size="small"
                  actions={[
                    {
                      key: "edit",
                      icon: "pi pi-pencil",
                      label: "Edit record",
                      visible: can(item.uuid, "edit"),
                      onClick: () => onEdit(item),
                    },
                    {
                      key: "share",
                      icon: "pi pi-share-alt",
                      label: "Share record",
                      visible: can(item.uuid, "share"),
                      onClick: () => onShare(item),
                    },
                    {
                      key: "delete",
                      icon: "pi pi-trash",
                      label: "Delete record",
                      severity: "danger",
                      visible: can(item.uuid, "delete"),
                      onClick: () => onDelete(item),
                    },
                  ]}
                />
              </div>
            </div>
          </div>
        );
      })}

      <Paginator
        first={start}
        rows={pageSize}
        totalRecords={items.length}
        rowsPerPageOptions={[10, 25, 50, 100]}
        onPageChange={(e) => {
          setFirst(e.first);
          onPageSize(e.rows);
        }}
        template="PrevPageLink CurrentPageReport NextPageLink RowsPerPageDropdown"
      />
    </div>
  );
}
