import { describe, expect, it } from "vitest";

import type { PermissionAction, ResourceType } from "./types";
import {
  ACTION_LABELS,
  ACTIONS,
  ROLE_PRESETS,
  actionsFor,
  expandActions,
  presetsFor,
} from "./permissions";

/**
 * The client's copy of the permission model, which had no test.
 *
 * `expandActions` is a hand-written mirror of `_IMPLIES` in
 * `backend/src/permissions/enums.py` — two implementations of one rule, in two
 * languages, with nothing checking them against each other. Nothing here is
 * enforcement (the server re-checks every action on every request), so a drift is
 * not a hole; it is the subtler failure of a UI that disagrees with the server
 * about what someone can do. Too narrow and a control the user is entitled to is
 * hidden with no explanation; too wide and they are offered a button that 403s.
 *
 * The table below is transcribed from the backend's `_IMPLIES` rather than derived
 * from `IMPLIES` here, which is the entire point: deriving it from the thing under
 * test would pass no matter what either side said.
 */
const BACKEND_IMPLIES: Record<PermissionAction, PermissionAction[]> = {
  // Action.manage: ALL_ACTIONS
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
  // _READING is {view, lookup}; each of these adds itself.
  configure: ["view", "lookup", "configure"],
  share: ["view", "lookup", "share"],
  edit: ["view", "lookup", "edit"],
  create: ["view", "lookup", "create"],
  delete: ["view", "lookup", "delete"],
  view: ["view", "lookup"],
  lookup: ["lookup"],
};

describe("expandActions mirrors the backend's _IMPLIES", () => {
  it.each(Object.keys(BACKEND_IMPLIES) as PermissionAction[])(
    "%s confers exactly what the server says it does",
    (action) => {
      expect([...expandActions([action])].sort()).toEqual(
        [...BACKEND_IMPLIES[action]].sort(),
      );
    },
  );

  it("unions several grants rather than taking the last", () => {
    expect(expandActions(["create", "delete"])).toEqual(
      new Set(["view", "lookup", "create", "delete"]),
    );
  });

  it("is empty for no grants", () => {
    expect(expandActions([])).toEqual(new Set());
  });

  /**
   * The asymmetry that makes lookup-only sharing work, and the one most likely to
   * be "tidied" into symmetry: everything that reads confers `lookup`, and
   * `lookup` confers nothing. It is what lets a list be shared as a reference
   * target while staying out of the grantee's lists — see known-gaps.
   */
  it("does not let lookup confer view", () => {
    expect(expandActions(["lookup"]).has("view")).toBe(false);
    expect(expandActions(["view"]).has("lookup")).toBe(true);
  });

  /**
   * `create` conferring `view` is why the clone hole mattered and why a lookup's
   * `allow_create` is safe — being eligible to add a row already implies being
   * able to read the target's schema.
   */
  it("lets create confer view", () => {
    expect(expandActions(["create"]).has("view")).toBe(true);
  });

  it("ignores an action it does not know rather than throwing", () => {
    // A server that grows an action before the frontend is rebuilt sends one of
    // these. Dropping it silently is right; a crash in the share dialog is not.
    expect(expandActions(["teleport" as PermissionAction])).toEqual(
      new Set(["teleport"]),
    );
  });
});

describe("actionsFor", () => {
  it("offers lookup only where a lookup can point", () => {
    for (const type of ["list", "workspace"] as ResourceType[]) {
      expect(actionsFor(type).map((a) => a.value)).toContain("lookup");
    }
    // On a record, view or form the grant would be inert.
    for (const type of ["item", "view", "form"] as ResourceType[]) {
      expect(actionsFor(type).map((a) => a.value)).not.toContain("lookup");
    }
  });

  it("offers every unrestricted action on every resource type", () => {
    const unrestricted = ACTIONS.filter((a) => !a.only).map((a) => a.value);
    for (const type of ["list", "workspace", "item", "view", "form"] as ResourceType[]) {
      expect(actionsFor(type).map((a) => a.value)).toEqual(
        expect.arrayContaining(unrestricted),
      );
    }
  });
});

describe("presetsFor", () => {
  it("hides a preset that would grant nothing on this resource", () => {
    // "Lookup only" on a record is a grant of no actions at all — the degenerate
    // case the backend's CONFERRED_BY note calls out.
    expect(presetsFor("item").map((p) => p.label)).not.toContain("Lookup only");
    expect(presetsFor("list").map((p) => p.label)).toContain("Lookup only");
  });

  it("keeps every preset whose actions all apply", () => {
    expect(presetsFor("list").map((p) => p.label)).toEqual(
      ROLE_PRESETS.map((p) => p.label),
    );
  });
});

describe("ACTION_LABELS", () => {
  it("names every action the dialog can offer", () => {
    for (const { value, label } of ACTIONS) {
      expect(ACTION_LABELS[value]).toBe(label);
    }
  });
});
