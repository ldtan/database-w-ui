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
  const visibleCount = columns.filter(
    (c) => !hiddenColumns.includes(c.uuid),
  ).length;
  const empty = columns.length === 0;

  const columnList = (
    <div className="flex flex-col gap-1">
      {columns.map((col, i) => (
        <div key={col.uuid} className="flex items-center gap-2">
          <Button
            icon="pi pi-chevron-up"
            text
            size="small"
            aria-label="Move up"
            disabled={i === 0}
            onClick={() => onMoveColumn(i, -1)}
          />
          <Button
            icon="pi pi-chevron-down"
            text
            size="small"
            aria-label="Move down"
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
            className="flex-1"
          >
            {col.display_name}
          </label>
        </div>
      ))}
    </div>
  );

  const filterButton = canFilter ? (
    <Button
      label={filterCount > 0 ? `Filter (${filterCount})` : "Filter"}
      icon="pi pi-filter"
      text={!stacked}
      outlined={stacked}
      size={stacked ? "small" : undefined}
      onClick={onOpenFilter}
      disabled={empty}
    />
  ) : null;

  const groupDropdown = (
    <Dropdown
      value={groupBy}
      options={columns.map((c) => ({ label: c.display_name, value: c.uuid }))}
      onChange={(e) => onGroupBy(e.value)}
      placeholder="Group by…"
      showClear
      disabled={empty}
      className="w-48"
    />
  );

  // Stacked: the settings tab, where an overlay panel inside a dialog is awkward,
  // so the column list is simply shown.
  if (stacked) {
    return (
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          {filterButton}
          {groupDropdown}
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
      {filterButton}
      {groupDropdown}
      <Button
        label={
          hiddenColumns.length > 0
            ? `Columns (${visibleCount}/${columns.length})`
            : "Columns"
        }
        icon="pi pi-eye"
        text
        onClick={(e) => columnsPanel.current?.toggle(e)}
        disabled={empty}
      />
      <OverlayPanel ref={columnsPanel}>{columnList}</OverlayPanel>
    </>
  );
}
