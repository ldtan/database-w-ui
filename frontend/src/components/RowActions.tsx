import { useId, useRef } from "react";
import { Button } from "primereact/button";
import { Menu } from "primereact/menu";

/**
 * One action in a row's action cluster — a share, rename, delete, and so on.
 * `visible` is the permission gate (default shown); an action the caller can't
 * perform is simply left out, so the cluster shrinks to what you're allowed to do.
 */
export interface RowAction {
  key: string;
  icon: string;
  /** Shown as the button's aria-label, and as the item's text in the overflow menu. */
  label: string;
  onClick: () => void;
  visible?: boolean;
  severity?: "danger";
  disabled?: boolean;
}

interface Props {
  actions: RowAction[];
  /**
   * How many actions show as icon buttons before the rest collapse into a "⋮"
   * overflow menu. Defaults to 3 — enough that a typical row (share/edit/delete)
   * never overflows. Rows with a fourth action, or a lot of them, pass 2 and keep
   * only what the row is *for* inline: the lists page and both record layouts do.
   * 0 collapses everything, which is what a narrow records grid wants — there the
   * column is pinned, so its width is spent whether or not it is being looked at.
   */
  maxVisible?: number;
  /** Button size, matched to the surface — records render theirs `small`. */
  size?: "small" | "large";
}

/**
 * A row's cluster of action buttons, with automatic overflow.
 *
 * Every list/workspace/record row grew the same hand-rolled strip of icon buttons,
 * each gated by a permission check, and the busiest of them (a list, once cloning
 * arrived) got crowded. This centralizes the pattern: pass the actions and how many
 * to keep visible, and the rest fold into a dropdown. Permission-hidden actions drop
 * out first, so the count is of what the caller can actually do — a viewer with one
 * available action never sees a lone "⋮".
 *
 * Clicks stop propagating, so an action inside a clickable row (the record table's
 * selectable rows) never also triggers the row.
 */
export function RowActions({ actions, maxVisible = 3, size }: Props) {
  const menuRef = useRef<Menu>(null);
  const menuId = useId();

  const shown = actions.filter((a) => a.visible !== false);
  if (shown.length === 0) return null;

  // A lone action stays inline even where the caller asked for everything to
  // collapse (`maxVisible={0}`): folding one button into a menu that holds one
  // item costs a click and saves no width.
  const limit = shown.length === 1 ? 1 : maxVisible;
  const overflows = shown.length > limit;
  const inline = overflows ? shown.slice(0, limit) : shown;
  const overflow = overflows ? shown.slice(limit) : [];

  return (
    <div className="row-actions flex items-center gap-1">
      {inline.map((a) => (
        <Button
          key={a.key}
          icon={a.icon}
          text
          size={size}
          severity={a.severity === "danger" ? "danger" : undefined}
          aria-label={a.label}
          disabled={a.disabled}
          onClick={(e) => {
            e.stopPropagation();
            a.onClick();
          }}
        />
      ))}
      {overflow.length > 0 && (
        <>
          <Button
            icon="pi pi-ellipsis-v"
            text
            size={size}
            aria-label="More actions"
            aria-haspopup
            aria-controls={menuId}
            onClick={(e) => {
              e.stopPropagation();
              menuRef.current?.toggle(e);
            }}
          />
          <Menu
            popup
            ref={menuRef}
            id={menuId}
            model={overflow.map((a) => ({
              label: a.label,
              icon: a.icon,
              disabled: a.disabled,
              className: a.severity === "danger" ? "row-action-danger" : undefined,
              command: (e) => {
                e.originalEvent?.stopPropagation();
                a.onClick();
              },
            }))}
          />
        </>
      )}
    </div>
  );
}
