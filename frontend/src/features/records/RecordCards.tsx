import { Checkbox } from "primereact/checkbox";
import { Paginator } from "primereact/paginator";

import { EmptyState } from "../../components/EmptyState";
import { RowActions } from "../../components/RowActions";
import { SkeletonRows } from "../../components/Skeleton";

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
  /** Offset of the page on screen, owned by the page (it is the `skip` sent to
   *  the server), so the cards and the grid page the same list the same way. */
  first: number;
  pageSize: number;
  /** How many records match in total, not how many arrived — the paginator needs
   *  the former to offer a page past this one. */
  total: number;
  onPage: (first: number, rows: number) => void;
  can: (uuid: string, action: PermissionAction) => boolean;
  /** Undefined when the list has no view form visible to this caller — the action
   *  then drops out, exactly as it does in the grid. */
  onView?: (item: ListItem) => void;
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
 * `renderCell` the grid uses, and it drives the same selection, the same page and
 * the same row actions.
 *
 * Both ordering and paging come in already applied. `items` is one page of records,
 * already sorted, exactly as the server returned it — so this renders what it was
 * given and reports where it wants to go next. It used to slice a local array,
 * which paged only the records that had been fetched.
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
  first,
  pageSize,
  total,
  onPage,
  can,
  onView,
  onEdit,
  onShare,
  onDelete,
}: Props) {
  const selected = new Set(selection.map((item) => item.uuid));

  function toggle(item: ListItem, checked: boolean) {
    onSelectionChange(
      checked
        ? [...selection, item]
        : selection.filter((s) => s.uuid !== item.uuid),
    );
  }

  if (loading) return <SkeletonRows count={3} />;
  if (items.length === 0) {
    return (
      <EmptyState
        icon="pi pi-inbox"
        title="No records yet"
        text="Records you add appear here, one card each."
      />
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {items.map((item, i) => {
        // The server orders by the group column, so a group starts wherever the
        // value differs from the row before it — the same subheader the grid draws,
        // without a second notion of what a group is.
        const groupValue = groupColumn ? item.data?.[groupColumn.uuid] : null;
        const startsGroup =
          !!groupColumn &&
          (i === 0 || items[i - 1].data?.[groupColumn.uuid] !== groupValue);

        return (
          <div key={item.uuid} className="flex flex-col gap-2">
            {startsGroup && groupColumn && (
              <div className="flex items-center gap-2 pt-1">
                <span className="nav-section" style={{ padding: 0 }}>
                  {groupColumn.display_name}
                </span>
                <span className="font-semibold">
                  {renderCell(
                    groupColumn,
                    item,
                    listUuid,
                    principalUuids,
                    lookupUuids,
                  )}
                </span>
              </div>
            )}
            <div
              className="surface-card p-3 flex flex-col gap-2"
              style={{
                // --hover, not PrimeReact's --surface-hover: that one is fixed at
                // #f6f9fc by the base theme and never moves with the mode, which
                // painted a selected card near-white on a dark palette.
                background: selected.has(item.uuid) ? "var(--hover)" : undefined,
                borderColor: selected.has(item.uuid)
                  ? "var(--brand)"
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
                className="flex items-center gap-2 pt-2 border-t"
                style={{ borderColor: "var(--border)" }}
              >
                <Checkbox
                  inputId={`sel-${item.uuid}`}
                  checked={selected.has(item.uuid)}
                  onChange={(e) => toggle(item, !!e.checked)}
                />
                <label
                  htmlFor={`sel-${item.uuid}`}
                  className="text-sm mr-auto cursor-pointer"
                  style={{ color: "var(--text-muted)" }}
                >
                  Select
                </label>
                <RowActions
                  size="small"
                  // Same split as the grid: view and edit inline, share and delete
                  // behind the overflow. The two layouts must not disagree about
                  // which actions a record has, or where.
                  maxVisible={2}
                  actions={[
                    {
                      key: "view",
                      icon: "pi pi-eye",
                      label: "View record",
                      visible: !!onView,
                      onClick: () => onView?.(item),
                    },
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
        first={first}
        rows={pageSize}
        totalRecords={total}
        rowsPerPageOptions={[10, 25, 50, 100]}
        onPageChange={(e) => onPage(e.first, e.rows)}
        template="PrevPageLink CurrentPageReport NextPageLink RowsPerPageDropdown"
      />
    </div>
  );
}
