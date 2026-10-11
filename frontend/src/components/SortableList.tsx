import type { ReactNode } from "react";
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

interface SortableListProps<T> {
  items: T[];
  /** Stable identity per row. Index would break the moment anything moves. */
  idOf: (item: T) => string;
  /** `from`/`to` are indices into `items`; see `moveTo` in features/views/reorder. */
  onReorder: (from: number, to: number) => void;
  /** The row's own content. The grip is supplied by this component, in front. */
  children: (item: T, index: number) => ReactNode;
  disabled?: boolean;
  className?: string;
  /** The row's own layout. Callers whose rows are a table need their own gap so
   *  the rows line up under the header. */
  rowClassName?: string;
}

/** Width of the grip column, so a caller with a header row can indent it to match. */
export const DRAG_GRIP_WIDTH = "1.5rem";

/**
 * A vertical table whose rows can be dragged into a new order.
 *
 * Three decisions in here are load-bearing.
 *
 * **The grip is a separate handle, not the whole row.** These rows carry
 * checkboxes, icon buttons and text inputs; a draggable row swallows the pointer
 * before any of them see it. Only the grip gets the drag listeners.
 *
 * **Drag never replaces the buttons.** Each caller keeps its up/down chevrons, so
 * reordering still works by keyboard, on a touch screen, and while the table is
 * scrolled — all cases a drag handles badly or not at all. dnd-kit's keyboard
 * sensor is wired too, but as a second way in rather than the only one.
 *
 * **No `@dnd-kit/modifiers`.** Constraining the drag to the vertical axis is one
 * line — zero the transform's `x` — and the package is not installed. `core`,
 * `sortable` and `utilities` are, and have been since the first commit.
 */
export function SortableList<T>({
  items,
  idOf,
  onReorder,
  children,
  disabled = false,
  className = "flex flex-col gap-1",
  rowClassName = "flex items-center gap-1",
}: SortableListProps<T>) {
  const sensors = useSensors(
    // Without a distance threshold a click on a checkbox or a chevron inside the
    // row is consumed as the start of a drag, and the control never fires.
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  const ids = items.map(idOf);

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const from = ids.indexOf(String(active.id));
    const to = ids.indexOf(String(over.id));
    if (from === -1 || to === -1) return;
    onReorder(from, to);
  }

  // Nothing to reorder, so no drag context and no grip — but the spacer stays, or
  // every row would shift left and stop lining up with a header above it.
  if (disabled) {
    return (
      <div className={className}>
        {items.map((item, i) => (
          <div key={idOf(item)} className={rowClassName}>
            <span style={{ width: DRAG_GRIP_WIDTH, flexShrink: 0 }} />
            {children(item, i)}
          </div>
        ))}
      </div>
    );
  }

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragEnd={handleDragEnd}
    >
      <SortableContext items={ids} strategy={verticalListSortingStrategy}>
        <div className={className}>
          {items.map((item, i) => (
            <SortableRow
              key={idOf(item)}
              id={idOf(item)}
              className={rowClassName}
            >
              {children(item, i)}
            </SortableRow>
          ))}
        </div>
      </SortableContext>
    </DndContext>
  );
}

function SortableRow({
  id,
  className,
  children,
}: {
  id: string;
  className: string;
  children: ReactNode;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id });

  return (
    <div
      ref={setNodeRef}
      className={className}
      style={{
        // x is zeroed so a row can only travel up and down: this is a table, and a
        // row dragged sideways is a row on its way to being dropped nowhere.
        transform: transform
          ? CSS.Transform.toString({ ...transform, x: 0, scaleX: 1, scaleY: 1 })
          : undefined,
        transition,
        // The dragged row stays legible above its neighbours rather than sliding
        // underneath them, which reads as the row disappearing.
        zIndex: isDragging ? 1 : undefined,
        position: isDragging ? "relative" : undefined,
        opacity: isDragging ? 0.85 : undefined,
      }}
    >
      <button
        type="button"
        className="drag-grip"
        aria-label="Reorder"
        {...attributes}
        {...listeners}
      >
        <i className="pi pi-bars" />
      </button>
      {children}
    </div>
  );
}
