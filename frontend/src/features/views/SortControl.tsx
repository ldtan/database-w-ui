import { useRef } from "react";
import { Button } from "primereact/button";
import { Dropdown } from "primereact/dropdown";
import { OverlayPanel } from "primereact/overlaypanel";
import { SelectButton } from "primereact/selectbutton";

import type { Column } from "../../lib/types";

type SortOrder = 1 | 0 | -1 | null | undefined;

interface Props {
  /** In display order — the same order the grid's headers are in. */
  columns: Column[];
  /** The grid's field name: `data.<column uuid>`, or undefined for unsorted. */
  sortField: string | undefined;
  sortOrder: SortOrder;
  onChange: (field: string | undefined, order: SortOrder) => void;
  /** Grouping orders the records itself, so the sort is not the caller's to set. */
  disabled?: boolean;
  disabledReason?: string;
}

/**
 * The sort, as one control.
 *
 * It used to be two: a `Sort by…` dropdown at a fixed 10rem, plus a separate
 * icon button for the direction that did nothing until the dropdown had a value
 * — a third of the toolbar's width spent on something that is unset most of the
 * time. Folded into a single button, it costs the width of the word "Sort" when
 * it is off and says "Name ↑" when it is on, which is the thing you actually
 * want to read off a toolbar.
 *
 * It drives the same `sortField`/`sortOrder` the grid's column headers do, so
 * clicking a header moves this control and vice versa — and on a phone, where
 * the records are cards and there are no headers to click, it is the only way
 * to sort at all.
 */
export function SortControl({
  columns,
  sortField,
  sortOrder,
  onChange,
  disabled,
  disabledReason,
}: Props) {
  const panel = useRef<OverlayPanel>(null);

  const active = columns.find((c) => `data.${c.uuid}` === sortField) ?? null;
  const descending = sortOrder === -1;

  return (
    <>
      <Button
        text
        className={active ? "is-on" : undefined}
        icon={
          active
            ? descending
              ? "pi pi-sort-amount-down"
              : "pi pi-sort-amount-up"
            : "pi pi-sort-alt"
        }
        label={active ? active.display_name : "Sort"}
        disabled={disabled || columns.length === 0}
        tooltip={disabled ? disabledReason : undefined}
        tooltipOptions={{ position: "bottom" }}
        aria-label={
          active
            ? `Sorted by ${active.display_name}, ${
                descending ? "descending" : "ascending"
              }`
            : "Sort"
        }
        onClick={(e) => panel.current?.toggle(e)}
      />
      <OverlayPanel ref={panel}>
        <div className="flex flex-col gap-3" style={{ minWidth: "16rem" }}>
          <div className="flex flex-col gap-2">
            <span className="text-sm font-medium">Sort by</span>
            <Dropdown
              value={sortField ?? null}
              options={columns.map((c) => ({
                label: c.display_name,
                value: `data.${c.uuid}`,
              }))}
              onChange={(e) =>
                // Choosing a column with no direction yet means ascending;
                // nobody picks a column in order to sort by nothing.
                onChange(e.value ?? undefined, e.value ? (sortOrder || 1) : undefined)
              }
              placeholder="Unsorted"
              showClear
              className="w-full"
            />
          </div>
          <div className="flex flex-col gap-2">
            <span className="text-sm font-medium">Direction</span>
            <SelectButton
              value={descending ? "desc" : "asc"}
              onChange={(e) => {
                if (!e.value || !sortField) return; // deselecting is not a third state
                onChange(sortField, e.value === "desc" ? -1 : 1);
              }}
              disabled={!sortField}
              options={[
                { label: "Ascending", value: "asc" },
                { label: "Descending", value: "desc" },
              ]}
            />
          </div>
        </div>
      </OverlayPanel>
    </>
  );
}
