import type { PermissionAction, ResourceType } from "./types";

// Display order + labels for the action checkboxes in the Share dialog. `only`
// narrows an action to the resource types it means anything on; absent means all.
export const ACTIONS: {
  value: PermissionAction;
  label: string;
  help: string;
  only?: ResourceType[];
}[] = [
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
  {
    value: "lookup",
    label: "Lookup",
    help:
      "Use as a lookup target: values can be read through a lookup column, " +
      "but the table stays hidden and cannot be opened",
    // A lookup points at a table, so only a table — or the workspace whose tables
    // inherit the grant — can be one. On a record, view, or form it would be inert.
    only: ["table", "workspace"],
  },
];

// Convenience presets shown as one-click buttons; they set the action checkboxes.
export const ROLE_PRESETS: { label: string; actions: PermissionAction[] }[] = [
  { label: "Lookup only", actions: ["lookup"] },
  { label: "Viewer", actions: ["view"] },
  { label: "Editor", actions: ["view", "create", "edit", "delete"] },
  { label: "Manager", actions: ["manage"] },
];

/** The actions worth offering on one kind of resource. */
export function actionsFor(resourceType: ResourceType) {
  return ACTIONS.filter((a) => !a.only || a.only.includes(resourceType));
}

/** The presets whose actions all apply to one kind of resource — so "Lookup only"
 *  is not offered where it would grant nothing. */
export function presetsFor(resourceType: ResourceType) {
  const allowed = new Set(actionsFor(resourceType).map((a) => a.value));
  return ROLE_PRESETS.filter((p) => p.actions.every((a) => allowed.has(a)));
}

export const ACTION_LABELS: Record<PermissionAction, string> = Object.fromEntries(
  ACTIONS.map((a) => [a.value, a.label]),
) as Record<PermissionAction, string>;

// Same expansion the backend applies, so the UI can show effective actions.
// `lookup` implies nothing and everything that reads the resource implies it —
// mirroring `_IMPLIES` in backend/src/permissions/enums.py.
const IMPLIES: Record<PermissionAction, PermissionAction[]> = {
  manage: [
    "view",
    "create",
    "edit",
    "delete",
    "configure",
    "share",
    "manage",
    "lookup",
  ],
  configure: ["configure", "view", "lookup"],
  share: ["share", "view", "lookup"],
  edit: ["edit", "view", "lookup"],
  create: ["create", "view", "lookup"],
  delete: ["delete", "view", "lookup"],
  view: ["view", "lookup"],
  lookup: ["lookup"],
};

export function expandActions(granted: PermissionAction[]): Set<PermissionAction> {
  const out = new Set<PermissionAction>();
  for (const a of granted) for (const e of IMPLIES[a] ?? [a]) out.add(e);
  return out;
}
