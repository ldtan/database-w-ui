import type { PermissionAction } from "./types";

// Display order + labels for the action checkboxes in the Share dialog.
export const ACTIONS: { value: PermissionAction; label: string; help: string }[] = [
  { value: "view", label: "View", help: "See the resource and its records" },
  { value: "create", label: "Create", help: "Add records" },
  { value: "edit", label: "Edit", help: "Change records" },
  { value: "delete", label: "Delete", help: "Remove records / the resource" },
  {
    value: "configure",
    label: "Configure",
    help: "Change settings, columns, forms, and views",
  },
  { value: "share", label: "Share", help: "Manage who has access" },
  { value: "manage", label: "Manage", help: "Full control" },
];

// Convenience presets shown as one-click buttons; they set the action checkboxes.
export const ROLE_PRESETS: { label: string; actions: PermissionAction[] }[] = [
  { label: "Viewer", actions: ["view"] },
  { label: "Editor", actions: ["view", "create", "edit", "delete"] },
  { label: "Manager", actions: ["manage"] },
];

export const ACTION_LABELS: Record<PermissionAction, string> = Object.fromEntries(
  ACTIONS.map((a) => [a.value, a.label]),
) as Record<PermissionAction, string>;

// Same expansion the backend applies, so the UI can show effective actions.
const IMPLIES: Record<PermissionAction, PermissionAction[]> = {
  manage: ["view", "create", "edit", "delete", "configure", "share", "manage"],
  configure: ["configure", "view"],
  share: ["share", "view"],
  edit: ["edit", "view"],
  create: ["create", "view"],
  delete: ["delete", "view"],
  view: ["view"],
};

export function expandActions(granted: PermissionAction[]): Set<PermissionAction> {
  const out = new Set<PermissionAction>();
  for (const a of granted) for (const e of IMPLIES[a] ?? [a]) out.add(e);
  return out;
}
