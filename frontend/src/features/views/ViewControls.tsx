import { useRef } from "react";
import { Button } from "primereact/button";
import { Checkbox } from "primereact/checkbox";
import { Dropdown } from "primereact/dropdown";
import { OverlayPanel } from "primereact/overlaypanel";

import type { Column } from "../../lib/types";

interface Props {
  // In display order, which is what the reorder buttons move within.
  columns: Column[];
  hiddenColumns: string[];
  onToggleColumn: (uuid: string, visible: boolean) => void;
  onMoveColumn: (index: number, delta: number) => void;
  groupBy: string | null;
  onGroupBy: (uuid: string | null) => void;
  filterCount: number;
  onOpenFilter: () => void;
  canFilter: boolean;
  // The toolbar lays these out in a row; the settings tab stacks them.
  stacked?: boolean;
}

/** What the current view *shows*: its filter, its grouping, and which columns are
 *  visible and in what order.
 *
 *  One component, rendered both on the toolbar (where you change these while
 *  watching the grid react) and in List settings → Views (where they sit with the
 *  rest of the list's configuration). Two copies of this markup would drift.
 *
 *  On the toolbar every one of these is a *button*, and each says whether it is
 *  currently doing anything — "Filter" against "Filter 2", "Group" against
 *  "Grouped by Status". A row of dropdowns sitting at their placeholder text
 *  takes three times the width to say nothing, and cannot tell you at a glance
 *  why the grid is showing what it is showing.
 */
export function ViewControls({
  columns,
  hiddenColumns,
  onToggleColumn,
  onMoveColumn,
  groupBy,
  onGroupBy,
  filterCount,
  onOpenFilter,
  canFilter,
  stacked = false,
}: Props) {
  const columnsPanel = useRef<OverlayPanel>(null);
  const groupPanel = useRef<OverlayPanel>(null);
  const visibleCount = columns.filter(
    (c) => !hiddenColumns.includes(c.uuid),
  ).length;
  const empty = columns.length === 0;
  const groupColumn = columns.find((c) => c.uuid === groupBy) ?? null;

  const columnList = (
    <div className="flex flex-col gap-1" style={{ minWidth: "15rem" }}>
      {columns.map((col, i) => (
        <div key={col.uuid} className="flex items-center gap-1">
          <Button
            icon="pi pi-chevron-up"
            text
            size="small"
            aria-label={`Move ${col.display_name} up`}
            disabled={i === 0}
            onClick={() => onMoveColumn(i, -1)}
          />
          <Button
            icon="pi pi-chevron-down"
            text
            size="small"
            aria-label={`Move ${col.display_name} down`}
            disabled={i === columns.length - 1}
            onClick={() => onMoveColumn(i, 1)}
          />
          <Checkbox
            inputId={`vis-${stacked ? "tab-" : ""}${col.uuid}`}
            checked={!hiddenColumns.includes(col.uuid)}
            onChange={(e) => onToggleColumn(col.uuid, !!e.checked)}
          />
          <label
            htmlFor={`vis-${stacked ? "tab-" : ""}${col.uuid}`}
            className="flex-1 truncate cursor-pointer text-sm"
          >
            {col.display_name}
          </label>
        </div>
      ))}
    </div>
  );

  const groupOptions = columns.map((c) => ({
    label: c.display_name,
    value: c.uuid,
  }));

  // Stacked: the settings tab, where an overlay panel inside a dialog is awkward,
  // so the column list and the grouping are simply shown.
  if (stacked) {
    return (
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          {canFilter && (
            <Button
              label={filterCount > 0 ? `Filter (${filterCount})` : "Filter"}
              icon="pi pi-filter"
              outlined
              size="small"
              onClick={onOpenFilter}
              disabled={empty}
            />
          )}
          <Dropdown
            value={groupBy}
            options={groupOptions}
            onChange={(e) => onGroupBy(e.value)}
            placeholder="Group by…"
            showClear
            disabled={empty}
            className="w-48"
          />
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-sm font-medium">
            Columns ({visibleCount}/{columns.length})
          </span>
          {columnList}
        </div>
      </div>
    );
  }

  return (
    <>
      {canFilter && (
        <Button
          label="Filter"
          icon="pi pi-filter"
          text
          className={filterCount > 0 ? "is-on" : undefined}
          badge={filterCount > 0 ? String(filterCount) : undefined}
          onClick={onOpenFilter}
          disabled={empty}
        />
      )}

      <Button
        label={groupColumn ? `Grouped by ${groupColumn.display_name}` : "Group"}
        icon="pi pi-list"
        text
        className={groupColumn ? "is-on" : undefined}
        onClick={(e) => groupPanel.current?.toggle(e)}
        disabled={empty}
      />
      <OverlayPanel ref={groupPanel}>
        <div className="flex flex-col gap-2" style={{ minWidth: "15rem" }}>
          <span className="text-sm font-medium">Group records by</span>
          <Dropdown
            value={groupBy}
            options={groupOptions}
            onChange={(e) => onGroupBy(e.value)}
            placeholder="No grouping"
            showClear
            className="w-full"
          />
          <span className="text-sm" style={{ color: "var(--text-muted)" }}>
            Grouping orders the records by that column, so it takes over from
            the sort.
          </span>
        </div>
      </OverlayPanel>

      <Button
        label="Columns"
        icon="pi pi-eye"
        text
        className={hiddenColumns.length > 0 ? "is-on" : undefined}
        badge={
          hiddenColumns.length > 0
            ? `${visibleCount}/${columns.length}`
            : undefined
        }
        onClick={(e) => columnsPanel.current?.toggle(e)}
        disabled={empty}
      />
      <OverlayPanel ref={columnsPanel}>
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-3">
            <span className="text-sm font-medium">Columns</span>
            {hiddenColumns.length > 0 && (
              <Button
                label="Show all"
                text
                size="small"
                onClick={() =>
                  hiddenColumns
                    .slice()
                    .forEach((uuid) => onToggleColumn(uuid, true))
                }
              />
            )}
          </div>
          {columnList}
        </div>
      </OverlayPanel>
    </>
  );
}
