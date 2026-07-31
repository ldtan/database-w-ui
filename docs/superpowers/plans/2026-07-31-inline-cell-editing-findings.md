# Inline Cell Editing Review Findings — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the ten open findings from the code review of PR #41, in three thematic PRs, with a vitest harness pinning the pure logic.

**Architecture:** The rules records go through (what "empty" means, how a value serializes, which columns may be written, what a text column's mode is) get pulled into pure modules that a node-environment test can import. The behavioural fixes then sit on top of those rules. Nothing renders in a test; the DOM-shaped findings are verified by reading PrimeReact's source and by hand.

**Tech Stack:** React 19, TypeScript 5.7, Vite 6, TanStack Query v5, PrimeReact 10.9, vitest (added by Task 1).

**Global Constraints:**
- **No AI attribution in commits.** No `Co-Authored-By` trailer, no "generated with" line. Match the repo's commit style: short imperative subject, then reasoning prose in paragraphs (see `git log 16a3c98 562c9aa f3b632f`).
- **Every PR is green before it opens:** `npx tsc --noEmit`, `npm run build`, and `npm test` all pass. Run them from `C:/Users/Asus/Projects/database-w-ui/frontend`.
- **Any claim about PrimeReact's behaviour cites `file:line` in `node_modules/primereact/`** in the commit message or PR body. This practice caught the `defaultPrevented` mistake in #10 before it was written; do not relax it.
- **PRs merge in order** — 1, then 2, then 3. Each branches from an up-to-date `main`.
- Branch → push → `gh pr create` → `gh pr merge --merge` → `git checkout main && git pull --ff-only`, the same flow as PRs #42 and #43.

**User decisions (already made):**
- "Add vitest, pure logic only" — node environment, no jsdom, no testing-library. Component tests are explicitly out of scope.
- "In resolveFields — fix both surfaces" for #9, so the record dialog gets the fix too, not just the grid.
- "Trust the draft, roll back, use the response" for #4 and #7 — the full write-path treatment.
- "Three thematic PRs."
- "cellMemo={false}" for #8, conditional on the render-cost measurement in Task 13.

---

## Note on structure: one addition beyond the spec

The spec put `toStored` and `clearedNested` in `recordValues.ts` but left `cellPatch` in `inlineEdit.tsx`. That file imports `RecordField` at module scope, which pulls PrimeReact into any test that imports `cellPatch` — and PrimeReact under a node environment (no `document`) is a gamble this plan should not take.

So Task 2 splits `inlineEdit.tsx` in two:

- `cellEdit.ts` — the pure rules: `CellEditKind`, `cellEditKinds`, `CellDraft`, `cellPatch`. No JSX, no React, importable from a node test.
- `inlineEdit.tsx` — `InlineCellEditor` only.

This is the split the file's own header already gestures at ("What this file does **not** do is decide whether the caller may edit the record"). It is a structural change the spec did not name; it is called out here so the reviewer sees it coming.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `frontend/vite.config.ts` | Add the `test` block | 1 |
| `frontend/package.json` | `vitest` devDependency, `test` scripts | 1 |
| `frontend/src/features/records/recordKeys.test.ts` | **Create.** Pin the namespace split that fixes #1 | 1 |
| `frontend/src/features/records/recordValues.ts` | Add `toStored`, `clearedNested`, `textMode`, `needsFullEditor`; `onupdate` → `enabled: false` | 2, 6, 11 |
| `frontend/src/features/records/recordValues.test.ts` | **Create.** `toStored`, `clearedNested`, `textMode`, `resolveFields` | 2, 6, 11 |
| `frontend/src/features/records/cellEdit.ts` | **Create.** Pure cell-editing rules, split out of `inlineEdit.tsx` | 2 |
| `frontend/src/features/records/cellEdit.test.ts` | **Create.** `cellPatch`, `cellEditKinds` | 2, 6, 11 |
| `frontend/src/features/records/inlineEdit.tsx` | `InlineCellEditor` only; gains the Escape handlers | 2, 8 |
| `frontend/src/features/records/RecordFields.tsx` | Use `clearedNested` and `textMode` | 2, 6 |
| `frontend/src/features/records/renderCell.tsx` | Use `textMode`; `editing` flag for link cells | 6, 7 |
| `frontend/src/features/lists/ListDetailPage.tsx` | `commitCell`, `saveCell`, cell callbacks, `cellMemo` | 3, 4, 7, 9, 12, 13 |

---

## PR 1 — The write path

Branch: `fix/cell-write-recovery-and-duplication`

### Task 1: Add the vitest harness

**Goal:** `npm test` runs pure-logic tests in a node environment, proven by a real test of the namespace split that fixed #1.

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/vite.config.ts`
- Create: `frontend/src/features/records/recordKeys.test.ts`

**Acceptance Criteria:**
- [ ] `npm test` runs and reports 2 passing tests
- [ ] The test environment is node — no jsdom in `package.json`
- [ ] Test globals are imported explicitly, so `tsconfig.json` needs no `types` entry
- [ ] `npx tsc --noEmit` stays clean with the new `.test.ts` file under `src`

**Verify:** `cd frontend && npm test` → `Test Files 1 passed (1)` / `Tests 2 passed (2)`

**Steps:**

- [ ] **Step 1: Install vitest**

```bash
cd C:/Users/Asus/Projects/database-w-ui/frontend
npm install -D vitest
```

- [ ] **Step 2: Add the test scripts to `package.json`**

In the `"scripts"` block, after `"preview": "vite preview",`:

```json
    "test": "vitest run",
    "test:watch": "vitest",
```

- [ ] **Step 3: Add the `test` block to `vite.config.ts`**

Change the import on line 1 from `"vite"` to `"vitest/config"` — it re-exports `defineConfig` with the `test` key typed:

```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Node, not jsdom, and deliberately: what is tested here are the rules a record
  // goes through — what counts as empty, how a value serializes, which columns may
  // be written. None of it renders. A DOM in the config is an invitation to write
  // component tests, and PrimeReact under jsdom is a maintenance cost of its own.
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
  server: {
    port: 5173,
    proxy: {
      // Proxy API calls to the FastAPI backend during development.
      "/api": {
        target: "http://localhost:5000",
        changeOrigin: true,
      },
    },
  },
});
```

- [ ] **Step 4: Write the first test**

Create `frontend/src/features/records/recordKeys.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import type { FilterGroup } from "../../lib/types";
import { metricValuesKey } from "../metrics/metricKeys";
import { recordPagesKey } from "./recordKeys";

const NO_FILTER: FilterGroup = { conjunction: "and", conditions: [] };

describe("recordPagesKey", () => {
  it("names the grid's pages inside the records namespace", () => {
    expect(recordPagesKey("list-1")).toEqual(["items", "list-1", "page"]);
  });

  // The bug this pins: both keys live under ["items", listUuid] so that one
  // invalidation refreshes the grid and the metric cards together. That makes the
  // prefix unsafe to *write* through, and a page-shaped updater handed a
  // MetricValues throws inside onMutate — which runs before mutationFn, so the
  // PATCH is never sent at all. They must diverge before the write filter's depth.
  it("does not prefix-match the metric values key", () => {
    const pages = recordPagesKey("list-1");
    const metrics = metricValuesKey("list-1", null, "", NO_FILTER);
    expect(metrics.slice(0, pages.length)).not.toEqual(pages);
  });
});
```

- [ ] **Step 5: Run the tests**

```bash
cd C:/Users/Asus/Projects/database-w-ui/frontend && npm test
```

Expected: `Test Files 1 passed (1)`, `Tests 2 passed (2)`.

- [ ] **Step 6: Confirm the typecheck still passes**

```bash
cd C:/Users/Asus/Projects/database-w-ui/frontend && npx tsc --noEmit
```

Expected: no output.

- [ ] **Step 7: Branch and commit**

```bash
cd C:/Users/Asus/Projects/database-w-ui
git checkout -b fix/cell-write-recovery-and-duplication
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts frontend/src/features/records/recordKeys.test.ts
git commit -F - <<'EOF'
Give the frontend somewhere to put a test

The backend's rules have been pinned by tests since the beginning; the browser's
have been pinned by reading them. That was tolerable while the frontend was
mostly wiring, and stopped being tolerable somewhere around the third defect in
a row that lived in a pure function — what counts as an empty value, which
columns may be written, whether two query keys collide.

Node, and no jsdom. The rules worth testing here do not render, and a DOM in the
config is an invitation to write component tests against PrimeReact, which is a
standing maintenance cost rather than a one-off setup. Globals are imported
rather than injected so that `tsconfig` needs no new `types` entry and a reader
can see where `describe` comes from.

The first test is the one the last fix should have had: the grid's pages and the
metric cards' values share the `["items", listUuid]` namespace on purpose, and
must diverge before the depth a cache *write* filters on.
EOF
```

---

### Task 2: Split the pure cell rules out and de-duplicate them

**Goal:** `cellPatch` and friends live in a module a node test can import, and the two rules they duplicated from the dialog have one home each (#13).

**Files:**
- Create: `frontend/src/features/records/cellEdit.ts`
- Create: `frontend/src/features/records/cellEdit.test.ts`
- Create: `frontend/src/features/records/recordValues.test.ts`
- Modify: `frontend/src/features/records/recordValues.ts` (add `toStored`, `clearedNested`; rewrite `buildRecordData`'s editing branch)
- Modify: `frontend/src/features/records/inlineEdit.tsx` (remove everything but `InlineCellEditor`)
- Modify: `frontend/src/features/records/RecordFields.tsx:288-293`
- Modify: `frontend/src/features/lists/ListDetailPage.tsx:39-45` (imports)

**Acceptance Criteria:**
- [ ] `cellEdit.ts` exports `CellEditKind`, `cellEditKinds`, `cellEditKind`, `CellDraft`, `cellPatch` and imports no React
- [ ] `inlineEdit.tsx` exports only `InlineCellEditor`
- [ ] `toSend` is deleted, not aliased
- [ ] `recordValues.ts` exports `toStored` and `clearedNested`; `buildRecordData` and `cellPatch` both use `toStored`; `RecordFields` and `cellPatch` both use `clearedNested`
- [ ] An untouched draft produces `null`, including for a `time` column holding `"14:30"`

**Verify:** `cd frontend && npm test` → `Tests 15 passed (15)`

**Steps:**

- [ ] **Step 1: Add the two shared rules to `recordValues.ts`**

Append after `buildRecordData` (end of file):

```ts
/**
 * A form value as the API stores it: absent becomes `null`, anything else is
 * serialized for its column.
 *
 * Absence is spelled several ways — a cleared box is `""`, an emptied
 * multi-select is `[]`, a stored empty cell is `null` — and collapsing them in one
 * place is what keeps the dialog and the grid agreeing about the same edit.
 */
export function toStored(column: Column, value: unknown): unknown {
  return isEmptyValue(value) ? null : serialize(column, value);
}

/**
 * The cells a changed parent invalidates, ready to spread into whatever is being
 * built — a form's values, or a single-cell patch. A city picked for the old
 * country means nothing once the country moves, and the two surfaces that clear
 * it must not drift apart on what "under" means.
 */
export function clearedNested(
  columns: Column[],
  changedUuid: string,
): Record<string, null> {
  return Object.fromEntries(
    nestedDescendants(columns, changedUuid).map((uuid) => [uuid, null]),
  );
}
```

- [ ] **Step 2: Use `toStored` in `buildRecordData`**

Replace the loop body at `recordValues.ts:139-147`:

```ts
  for (const { column, enabled } of fields) {
    if (!enabled) continue; // read-only fields keep their default/existing value
    const value = values[column.uuid];
    if (editing) {
      data[column.uuid] = toStored(column, value);
    } else if (!isEmptyValue(value)) {
      // Creating omits an empty field rather than nulling it, so the column's
      // default still applies — which is why this branch is not `toStored`.
      data[column.uuid] = serialize(column, value);
    }
  }
```

- [ ] **Step 3: Use `clearedNested` in `RecordFields.tsx`**

Replace the `onChange` at `RecordFields.tsx:288-293`:

```tsx
            onChange={(v) =>
              onChange({
                ...values,
                [column.uuid]: v,
                ...clearedNested(columns, column.uuid),
              })
            }
```

Update its import to pull `clearedNested` from `./recordValues` and drop `nestedDescendants` if it is now unused.

- [ ] **Step 4: Create `cellEdit.ts` with the pure rules**

```ts
/**
 * The rules a grid cell's editor follows, with nothing in them that renders.
 *
 * Split from `inlineEdit.tsx` so that a test can import them: that file holds
 * `InlineCellEditor`, which reaches PrimeReact through `RecordField`, and a rule
 * about what counts as a changed value should not need a DOM to check.
 */
import type { Column } from "../../lib/types";
import { clearedNested, toStored } from "./recordValues";
import type { ResolvedField } from "./recordValues";

/**
 * What a column's cells do while the grid is in edit mode.
 *
 * "dialog" is the honest answer for a rich-text or multiline column: its editor
 * is a 200px box with a toolbar, or a textarea whose line breaks are the point,
 * and a table row is neither tall enough nor wide enough to be either. Rather
 * than shrink it into a cell or leave the cell inert, clicking one opens the
 * record in the dialog it needs.
 *
 * "locked" is every other reason a cell will not open: the update form marks the
 * field read-only, or the column is calculated (`resolveFields` drops those, and
 * the backend refuses to store one anyway).
 */
export type CellEditKind = "inline" | "dialog" | "locked";

/** Column uuid -> what its cells do. Anything absent is "locked". */
export function cellEditKinds(fields: ResolvedField[]): Map<string, CellEditKind> {
  const kinds = new Map<string, CellEditKind>();
  for (const { column, enabled } of fields) {
    const mode = column.config.text_mode;
    kinds.set(
      column.uuid,
      !enabled
        ? "locked"
        : column.type === "text" && (mode === "rich" || mode === "multiline")
          ? "dialog"
          : "inline",
    );
  }
  return kinds;
}

export function cellEditKind(
  kinds: Map<string, CellEditKind>,
  columnUuid: string,
): CellEditKind {
  return kinds.get(columnUuid) ?? "locked";
}

/** The cell an editor is currently open on, and what has been typed into it. */
export interface CellDraft {
  rowUuid: string;
  columnUuid: string;
  /**
   * What the editor opened holding, carried alongside the draft so that the
   * commit can ask "did this person change anything?" without consulting the
   * grid — see `cellPatch`, which compares against this rather than against the
   * stored cell.
   */
  initial: unknown;
  value: unknown;
}

/**
 * The `data` patch a committed cell sends, or **null when nothing changed**.
 *
 * The null case is not an optimisation. Opening a cell and clicking away is a
 * gesture people make constantly, and a column with an `onupdate` token is
 * re-stamped on *every* accepted write — so a no-op PATCH would quietly move the
 * record's "last modified" without anybody having modified it.
 *
 * "Changed" means *this person changed it*, so the draft is compared against
 * what the editor opened holding rather than against the record's current cell.
 * Those are the same thing at the moment a cell opens and stop being the same
 * thing the instant a poll lands: comparing against the record would read a
 * cell somebody else has since edited as changed-by-us, and reverting their
 * value on an Enter that typed nothing is the one outcome an untouched cell must
 * never have. Comparing against the seed also keeps a lossy round trip from
 * manufacturing an edit — `deserialize` then `serialize` turns a stored `14:30`
 * into `14:30:00`, which differs from the stored string but not from itself.
 *
 * A changed lookup also clears every lookup nested under it, exactly as
 * `RecordFields` does when the dialog's parent field changes.
 */
export function cellPatch(
  columns: Column[],
  column: Column,
  draft: CellDraft,
): Record<string, unknown> | null {
  const next = toStored(column, draft.value);
  if (JSON.stringify(next) === JSON.stringify(toStored(column, draft.initial))) {
    return null;
  }
  return { [column.uuid]: next, ...clearedNested(columns, column.uuid) };
}
```

- [ ] **Step 5: Reduce `inlineEdit.tsx` to the component**

Delete from `inlineEdit.tsx`: the `CellEditKind` type, `cellEditKinds`, `cellEditKind`, the `CellDraft` interface, `toSend`, and `cellPatch`. Keep the module docstring and `InlineCellEditor`. Its imports become:

```ts
import { useEffect, useRef, useState } from "react";

import type { Column, ListItem } from "../../lib/types";
import type { CellDraft } from "./cellEdit";
import { RecordField } from "./RecordFields";
import { deserialize } from "./recordValues";
```

Add to the module docstring, after the existing three bullets:

```
 * The rules themselves — which columns open an editor, what a committed cell
 * sends — are in `cellEdit.ts`, which imports no React so that they can be
 * tested without one.
```

- [ ] **Step 6: Point `ListDetailPage.tsx` at both modules**

Replace the import block at lines 39-45:

```ts
import {
  cellEditKind,
  cellEditKinds,
  cellPatch,
  type CellDraft,
} from "../records/cellEdit";
import { InlineCellEditor } from "../records/inlineEdit";
```

- [ ] **Step 7: Write the tests**

Create `frontend/src/features/records/recordValues.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import type { Column } from "../../lib/types";
import { clearedNested, toStored } from "./recordValues";

function column(over: Partial<Column> = {}): Column {
  return {
    uuid: "c1",
    name: "c1",
    display_name: "C1",
    type: "text",
    config: {},
    ...over,
  } as Column;
}

describe("toStored", () => {
  it.each([[undefined], [null], [""], [[]]])(
    "collapses %j to null",
    (value) => {
      expect(toStored(column(), value)).toBeNull();
    },
  );

  it("keeps a value that is merely falsy", () => {
    expect(toStored(column({ type: "number" }), 0)).toBe(0);
    expect(toStored(column({ type: "checkbox" }), false)).toBe(false);
  });

  it("serializes a Date for its column type", () => {
    const date = new Date(2026, 6, 12, 14, 30, 0);
    expect(toStored(column({ type: "date" }), date)).toBe("2026-07-12");
    expect(toStored(column({ type: "time" }), date)).toBe("14:30:00");
  });
});

describe("clearedNested", () => {
  const columns = [
    column({ uuid: "country" }),
    column({ uuid: "city", config: { parent_column_uuid: "country" } }),
    column({ uuid: "street", config: { parent_column_uuid: "city" } }),
    column({ uuid: "unrelated" }),
  ];

  it("clears children and grandchildren of a changed parent", () => {
    expect(clearedNested(columns, "country")).toEqual({
      city: null,
      street: null,
    });
  });

  it("clears nothing for a column with no children", () => {
    expect(clearedNested(columns, "unrelated")).toEqual({});
  });
});
```

Create `frontend/src/features/records/cellEdit.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import type { Column } from "../../lib/types";
import { cellPatch, type CellDraft } from "./cellEdit";
import { deserialize, toStored } from "./recordValues";

function column(over: Partial<Column> = {}): Column {
  return {
    uuid: "c1",
    name: "c1",
    display_name: "C1",
    type: "text",
    config: {},
    ...over,
  } as Column;
}

function draft(over: Partial<CellDraft>): CellDraft {
  return { rowUuid: "r1", columnUuid: "c1", initial: null, value: null, ...over };
}

describe("cellPatch", () => {
  it("sends nothing when the draft was never touched", () => {
    const col = column();
    // Typing a value and putting it back is the same draft state — CellDraft
    // carries only the seed and the current value, so the two gestures are
    // indistinguishable here by construction.
    expect(cellPatch([col], col, draft({ initial: "Ada", value: "Ada" }))).toBeNull();
  });

  it("sends nothing when an untouched temporal value fails to round-trip", () => {
    const col = column({ type: "time" });
    const stored = "14:30";
    const seeded = deserialize(col, stored);

    // The round trip really is lossy — `deserialize` turns a stored "14:30" into
    // a Date and `serialize` writes it back as "14:30:00". This is the bug's
    // precondition, and without asserting it the test below would pass for the
    // wrong reason: comparing any value to itself is trivially stable.
    expect(toStored(col, seeded)).not.toBe(stored);

    // ...and comparing the draft against its seed absorbs that, rather than
    // inventing an edit out of the difference.
    expect(cellPatch([col], col, draft({ initial: seeded, value: seeded }))).toBeNull();
  });

  it("sends the new value when it changed", () => {
    const col = column();
    expect(cellPatch([col], col, draft({ initial: "Ada", value: "Bob" }))).toEqual({
      c1: "Bob",
    });
  });

  it("sends null when the cell was cleared", () => {
    const col = column();
    expect(cellPatch([col], col, draft({ initial: "Ada", value: "" }))).toEqual({
      c1: null,
    });
  });

  it("clears the lookups nested under a changed column", () => {
    const country = column({ uuid: "country", type: "lookup" });
    const city = column({ uuid: "city", config: { parent_column_uuid: "country" } });
    const columns = [country, city];
    expect(
      cellPatch(columns, country, draft({ columnUuid: "country", initial: "fr", value: "de" })),
    ).toEqual({ country: "de", city: null });
  });
});
```

- [ ] **Step 8: Run the tests and the typecheck**

```bash
cd C:/Users/Asus/Projects/database-w-ui/frontend && npm test && npx tsc --noEmit
```

Expected: `Tests 15 passed (15)`, then no typecheck output.

- [ ] **Step 9: Commit**

```bash
cd C:/Users/Asus/Projects/database-w-ui
git add frontend/src/features/records frontend/src/features/lists/ListDetailPage.tsx
git commit -F - <<'EOF'
Give a cell's rules one home, and the same one the dialog uses

Two rules were being kept in two places. What makes a value empty, and what a
value looks like once it is stored, was written out in `buildRecordData` and
again in the cell patch; which lookups a changed parent invalidates was written
out in `RecordFields` and again in the cell patch. Both are rules about a record
rather than about a surface, so both now live in `recordValues` and both surfaces
call them. The failure they were heading for is quiet: change what "empty" means
and the dialog and the grid start disagreeing about the same edit, each correct
by its own copy.

Splitting the rules out of `inlineEdit` is what lets any of this be tested.
That file reaches PrimeReact through `RecordField`, so importing `cellPatch`
imports a UI toolkit, and a question as small as "does an untouched cell send
anything" would have needed a DOM to answer. `cellEdit` imports no React. The
component stays where it was.
EOF
```

---

### Task 3: Trust the draft over the event's row

**Goal:** A cell edit survives the grid repaging underneath it, instead of being discarded without a word (#4).

**Files:**
- Modify: `frontend/src/features/lists/ListDetailPage.tsx:505-527` (`commitCell`)
- Modify: `frontend/src/features/lists/ListDetailPage.tsx:1135` (`onCellEditComplete`)

**Acceptance Criteria:**
- [ ] `commitCell` takes no arguments and reads the record and the column from the draft
- [ ] The only bail-out besides "no draft" is a column that no longer exists
- [ ] `onCellEditComplete` is `() => commitCell()`

**Verify:** `cd frontend && npx tsc --noEmit && npm test` → no output, then `Tests 15 passed (15)`

**Steps:**

- [ ] **Step 1: Rewrite `commitCell`**

Replace the whole function and its docstring:

```tsx
  /**
   * A cell edit, on its way to the server.
   *
   * PrimeReact calls this when the cell leaves edit mode by any route that means
   * "keep it" — Enter, Tab, a click elsewhere — and never on Escape, which is what
   * makes Escape a cancel.
   *
   * Everything it needs is in the draft, and deliberately so. PrimeReact keys its
   * body cells by row *index*, so a poll that reorders or repages the rows leaves
   * an open editor sitting where a different record now is; the event's row is
   * that different record. The draft knows which record and which column it was
   * typed into, and a row moving on screen is no reason to throw away what
   * somebody typed.
   */
  function commitCell() {
    const draft = cellDraft.current;
    cellDraft.current = null;
    if (!draft) return;
    // The one thing a draft cannot outlive: the column it belongs to being
    // deleted while its editor was open. There is nothing left to patch.
    const column = cols.find((c) => c.uuid === draft.columnUuid);
    if (!column) return;
    const patch = cellPatch(cols, column, draft);
    if (patch) saveCell.mutate({ uuid: draft.rowUuid, patch });
  }
```

- [ ] **Step 2: Update the call site**

At `ListDetailPage.tsx` in the `visibleCols.map` block:

```tsx
                onCellEditComplete={() => commitCell()}
```

- [ ] **Step 3: Verify**

```bash
cd C:/Users/Asus/Projects/database-w-ui/frontend && npx tsc --noEmit && npm test
```

Expected: no typecheck output; `Tests 15 passed (15)`.

- [ ] **Step 4: Commit**

```bash
cd C:/Users/Asus/Projects/database-w-ui
git add frontend/src/features/lists/ListDetailPage.tsx
git commit -F - <<'EOF'
Keep a cell edit when the row moves out from under it

PrimeReact keys its body cells by row index, so a poll that reorders or repages
the grid leaves an open editor sitting at a position a different record now
occupies. The commit compared the draft against the record on the event, found
they disagreed, and dropped the draft — no toast, no re-open, nothing on screen
to say the value had not been saved. The guard was right that the event's record
must not be written to; it was wrong about what to do next.

The draft has known which record and which column it belongs to since it was
first reported, and the last change to this file already made it the thing the
PATCH is addressed to. So the event's row is not consulted at all any more, and a
row moving on screen stops being a reason to throw away what somebody typed. The
only draft still dropped is one whose column has been deleted while its editor
was open, which is the one case with nothing left to patch.
EOF
```

---

### Task 4: Roll back on error, keep the record the response returns

**Goal:** A refused cell write leaves nothing behind even offline, and a burst of writes costs one refetch rather than one each (#7).

**Files:**
- Modify: `frontend/src/features/lists/ListDetailPage.tsx:338-379` (`saveCell`)
- Modify: `frontend/src/features/lists/ListDetailPage.tsx` (module scope: `SETTLE_DELAY_MS`, `withItem`)

**Acceptance Criteria:**
- [ ] `onMutate` snapshots with `getQueriesData` and returns `{ previous }`
- [ ] `onError` restores the snapshot before showing the toast
- [ ] `onSuccess` writes the returned `ListItem` into the cached pages
- [ ] `onSettled` schedules a debounced invalidate; the timer is cleared on unmount
- [ ] Ten commits in quick succession produce one invalidate

**Verify:** `cd frontend && npx tsc --noEmit && npm run build` → no typecheck output, then `✓ built`

**Steps:**

- [ ] **Step 1: Add the module-scope helpers**

Below `const EMPTY_FILTER` near the top of the file:

```ts
/** How long the grid waits after the last cell commit before asking the server
 *  what else moved. Long enough that tabbing across a row is one burst, short
 *  enough that a single edit settles while you are still looking at it. */
const SETTLE_DELAY_MS = 800;

/** One page with one record replaced, or the page untouched if it is not on it. */
function withItem(
  page: Page<ListItem>,
  uuid: string,
  next: (item: ListItem) => ListItem,
): Page<ListItem> {
  return {
    ...page,
    items: page.items.map((item) => (item.uuid === uuid ? next(item) : item)),
  };
}
```

- [ ] **Step 2: Add the debounced settle inside the component**

Immediately above `const saveCell = useMutation({`:

```tsx
  // A row of cells tabbed across is one edit gesture, not ten. Each write settles
  // its own record from the response, so what is left for a refetch is everything
  // a record cannot answer for on its own: whether it still matches the active
  // filter, where it now sorts, and what the metric cards above it add up to.
  // Those want one answer after the burst, not one per cell.
  const settleTimer = useRef<number | null>(null);
  const scheduleSettle = () => {
    if (settleTimer.current !== null) window.clearTimeout(settleTimer.current);
    settleTimer.current = window.setTimeout(() => {
      settleTimer.current = null;
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
    }, SETTLE_DELAY_MS);
  };
  useEffect(
    () => () => {
      if (settleTimer.current !== null) window.clearTimeout(settleTimer.current);
    },
    [],
  );
```

- [ ] **Step 3: Rewrite `saveCell`**

```tsx
  // One cell, committed on its own. `PATCH /items/{uuid}` merges the keys it is
  // given onto the stored record, so the body is the one column that changed (plus
  // any lookup nested under it, which `cellPatch` clears) rather than the record.
  const saveCell = useMutation({
    mutationFn: (vars: { uuid: string; patch: Record<string, unknown> }) =>
      api.patch<ListItem>(`/lists/${listUuid}/items/${vars.uuid}`, {
        data: vars.patch,
      }),
    // Show the value the moment it is committed. Without this the cell falls back
    // to what it held until the write lands — a visible flick back to the old
    // value on every edit, which reads as the save having failed.
    //
    // Keyed on the *pages* rather than on `["items", listUuid]`, which is the
    // namespace the metric values share (see `recordKeys`): matching that would
    // hand this page-shaped updater a `MetricValues`, and `page.items.map` on an
    // object with no `items` throws — inside `onMutate`, which runs before
    // `mutationFn`, so the PATCH would never even be sent.
    onMutate: (vars) => {
      // Snapshotted before the write so that a refusal can be taken back without
      // asking the server anything. A refetch cannot do that job: offline there
      // is nothing to refetch, and the value the server rejected would sit on
      // screen until the connection came back.
      const previous = queryClient.getQueriesData<Page<ListItem>>({
        queryKey: recordPagesKey(listUuid),
      });
      queryClient.setQueriesData<Page<ListItem>>(
        { queryKey: recordPagesKey(listUuid) },
        (page) =>
          page &&
          withItem(page, vars.uuid, (item) => ({
            ...item,
            data: { ...item.data, ...vars.patch },
          })),
      );
      return { previous };
    },
    // The record the PATCH already answered with: the server's own rendering,
    // calculated columns recomputed and `onupdate` columns stamped. Refetching the
    // page to ask again would be a slower way to learn less than we already hold.
    onSuccess: (saved) => {
      queryClient.setQueriesData<Page<ListItem>>(
        { queryKey: recordPagesKey(listUuid) },
        (page) => page && withItem(page, saved.uuid, () => saved),
      );
    },
    onError: (err, _vars, context) => {
      for (const [key, page] of context?.previous ?? []) {
        queryClient.setQueryData(key, page);
      }
      toast.error(
        "Could not save that value",
        err instanceof ApiError ? err.message : undefined,
      );
    },
    // What is left after the row has settled itself: whether it still belongs on
    // this page at all, and the totals above it. Debounced, because that question
    // has one answer per burst of edits rather than one per cell.
    onSettled: scheduleSettle,
  });
```

- [ ] **Step 4: Verify**

```bash
cd C:/Users/Asus/Projects/database-w-ui/frontend && npx tsc --noEmit && npm test && npm run build
```

Expected: no typecheck output, `Tests 15 passed (15)`, `✓ built`.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Asus/Projects/database-w-ui
git add frontend/src/features/lists/ListDetailPage.tsx
git commit -F - <<'EOF'
Take back a refused cell write, and stop asking twice about an accepted one

"The refetch is the revert" was true only where there was something to refetch
with. Go offline and edit a cell: the toast says it failed, the invalidate cannot
reach anything, and the value the server never accepted stays on the screen for
as long as the connection is down. So the pages are snapshotted before the
optimistic write and put back on failure, which is a revert that does not depend
on the network being the thing that just failed.

The accepted case was paying twice for one answer. `PATCH` already responds with
the server's rendering of the record — calculated columns recomputed, `onupdate`
columns stamped — and that response was being dropped on the floor in favour of
refetching the page to ask the same question again. It is written into the cache
instead.

What a record cannot answer for is whether it still belongs on the page: a value
can move it out of the active filter, change where it sorts, or change what the
cards above it total. That still wants the server, but once per burst rather than
once per cell — tabbing across ten columns was ten full refetches of the page and
every metric query hanging off it. Debounced, it is one.
EOF
```

---

### Task 5: Ship PR 1

**Goal:** PR 1 is merged into `main` and `main` is synced locally.

**Files:** none — git and `gh` only.

**Acceptance Criteria:**
- [ ] `npx tsc --noEmit`, `npm test` and `npm run build` all pass on the branch tip
- [ ] PR body names the findings closed (#4, #7, #13) and the structural split
- [ ] PR merged, local `main` fast-forwarded, tree clean

**Verify:** `gh pr view <n> --json state` → `{"state":"MERGED"}`

**Steps:**

- [ ] **Step 1: Final green run**

```bash
cd C:/Users/Asus/Projects/database-w-ui/frontend && npx tsc --noEmit && npm test && npm run build
```

- [ ] **Step 2: Push and open the PR**

```bash
cd C:/Users/Asus/Projects/database-w-ui
git push -u origin fix/cell-write-recovery-and-duplication
gh pr create --base main --head fix/cell-write-recovery-and-duplication \
  --title "Take back a refused cell write, and keep one the row moved under" \
  --body "Closes review findings #4, #7 and #13, and gives the frontend a test harness.

## #4 — a moved row is not a reason to drop an edit
PrimeReact keys body cells by row index, so a poll that repages the grid leaves an open editor where a different record now sits. \`commitCell\` compared the draft against the event's record, disagreed, and discarded the draft silently. It now reads the record and the column from the draft itself; the only remaining bail is a column deleted mid-edit.

## #7 — rollback, the returned record, one refetch per burst
\`onMutate\` snapshots the pages and \`onError\` restores them, so a refusal is taken back without needing the network that just failed. \`onSuccess\` writes the record the PATCH already returned instead of refetching to ask again. \`onSettled\` is debounced at 800ms, so tabbing across ten columns costs one refetch rather than ten.

## #13 — two rules, one home each
\`toStored\` and \`clearedNested\` move to \`recordValues.ts\`; \`buildRecordData\` and \`cellPatch\` now share the first, \`RecordFields\` and \`cellPatch\` the second.

## Structural: \`cellEdit.ts\`
The pure rules are split out of \`inlineEdit.tsx\`, which reaches PrimeReact through \`RecordField\` — importing \`cellPatch\` imported a UI toolkit. This is what makes them testable at all.

## Harness
vitest, node environment, no jsdom. 15 tests covering \`recordPagesKey\`, \`toStored\`, \`clearedNested\` and \`cellPatch\` — including the temporal round-trip case that used to leak a no-op PATCH.

\`tsc --noEmit\`, \`npm test\` and \`vite build\` all clean."
```

- [ ] **Step 3: Merge and sync**

```bash
cd C:/Users/Asus/Projects/database-w-ui
gh pr merge --merge
git checkout main && git pull --ff-only
git --no-pager log --oneline -3
```

If `git pull` fails with `Recv failure: Connection was reset`, retry once — TLS interception on this machine makes that intermittent.

---

## PR 2 — Reach and affordance

Branch: `fix/cell-affordance-and-keyboard-reach`

### Task 6: One vocabulary for text modes

**Goal:** The `rich` / `multiline` / everything-else rule is stated once and read by all three surfaces that branch on it (#12).

**Files:**
- Modify: `frontend/src/features/records/recordValues.ts` (add `TextMode`, `textMode`, `needsFullEditor`)
- Modify: `frontend/src/features/records/cellEdit.ts:cellEditKinds`
- Modify: `frontend/src/features/records/RecordFields.tsx:94-121`
- Modify: `frontend/src/features/records/renderCell.tsx:86-111`
- Modify: `frontend/src/features/records/recordValues.test.ts`
- Modify: `frontend/src/features/records/cellEdit.test.ts`

**Acceptance Criteria:**
- [ ] `textMode` returns `"single"` for a non-text column, an unset mode, and an unrecognised mode
- [ ] `cellEditKinds`, `RecordFields` and `renderCell` all call `textMode`; none of them reads `config.text_mode` directly
- [ ] `cellEditKinds` returns `"dialog"` for rich and multiline, `"inline"` otherwise, `"locked"` when disabled

**Verify:** `cd frontend && npm test` → `Tests 25 passed (25)`

**Steps:**

- [ ] **Step 1: Add the accessor to `recordValues.ts`**

Append at the end of the file:

```ts
/**
 * How a text column's value is written and shown.
 *
 * A text column's editor comes from its config rather than its type — "single"
 * and "multiline" used to be the `text` and `long_text` types — and anything
 * unrecognised, including unset, is a single line. Stated once here because three
 * surfaces branch on it and must not drift: which editor the dialog renders, how
 * the value displays in a cell or a dialog, and whether a grid cell can hold an
 * editor at all.
 */
export type TextMode = "rich" | "multiline" | "single";

export function textMode(column: Column): TextMode {
  if (column.type !== "text") return "single";
  const mode = column.config.text_mode;
  return mode === "rich" || mode === "multiline" ? mode : "single";
}

/**
 * A column whose editor needs more room than a table row has. A rich text editor
 * is a toolbar and a 200px box; a multiline value's line breaks are its content.
 * Shrinking either into a row would be a worse editor than the one that exists.
 */
export function needsFullEditor(column: Column): boolean {
  return textMode(column) !== "single";
}
```

- [ ] **Step 2: Use it in `cellEditKinds`**

```ts
export function cellEditKinds(fields: ResolvedField[]): Map<string, CellEditKind> {
  const kinds = new Map<string, CellEditKind>();
  for (const { column, enabled } of fields) {
    kinds.set(
      column.uuid,
      !enabled ? "locked" : needsFullEditor(column) ? "dialog" : "inline",
    );
  }
  return kinds;
}
```

Add `needsFullEditor` to the `./recordValues` import in `cellEdit.ts`.

- [ ] **Step 3: Use it in `RecordFields.tsx`**

Replace the `case "text"` block at lines 94-121:

```tsx
    case "text": {
      const mode = textMode(column);
      if (mode === "rich") {
        return (
          <Editor
            id={id}
            value={(value as string) ?? ""}
            // `htmlValue` is the markup; `textValue` is the same content
            // flattened. The column stores HTML, so it is the former that is
            // kept — see columns/richtext.py, which cleans it on arrival.
            onTextChange={(e) => onChange(e.htmlValue ?? "")}
            headerTemplate={RICH_TOOLBAR}
            style={{ height: 200 }}
            readOnly={disabled}
          />
        );
      }
      if (mode === "multiline") {
        return (
          <InputTextarea
            id={id}
            value={(value as string) ?? ""}
            onChange={(e) => onChange(e.target.value)}
            rows={3}
            disabled={disabled}
          />
        );
      }
      break;
    }
```

Add `textMode` to its `./recordValues` import.

- [ ] **Step 4: Use it in `renderCell.tsx`**

Replace the `column.type === "text"` block at lines 86-112 — through the
function's trailing `return String(value);`, which the replacement carries.
The type guard goes because `textMode` already answers `"single"` for a non-text
column, and that lands on the same fallthrough it landed on before; leaving line
112 in place would strand a second, unreachable `return`.

```tsx
  const mode = textMode(column);
  if (mode === "rich") {
    // Rendered as markup only where there is room for it. Safe because the
    // backend sanitises every write (columns/richtext.py) — this is why that
    // sits on the write path and not in the editor: a value could equally
    // have arrived by API or CSV import, and it lands here just the same.
    if (variant === "full") {
      return (
        <span
          className="rich-text-cell"
          dangerouslySetInnerHTML={{ __html: String(value) }}
        />
      );
    }
    // In a row, the formatting is noise and the headings would set the row
    // height. The words are what identifies the record.
    return stripHtml(String(value));
  }
  // A multiline column's line breaks are content, and collapsing them was the
  // one thing `long_text` never got right. Honoured only where there is room:
  // in the grid the value keeps its single line.
  if (mode === "multiline" && variant === "full") {
    return <span style={{ whiteSpace: "pre-wrap" }}>{String(value)}</span>;
  }
  return String(value);
```

Add `import { textMode } from "./recordValues";` to `renderCell.tsx`.

- [ ] **Step 5: Add the tests**

Append to `recordValues.test.ts`:

```ts
describe("textMode", () => {
  it.each([
    [{ type: "text", config: { text_mode: "rich" } }, "rich"],
    [{ type: "text", config: { text_mode: "multiline" } }, "multiline"],
    [{ type: "text", config: { text_mode: "single" } }, "single"],
    [{ type: "text", config: {} }, "single"],
    [{ type: "text", config: { text_mode: "typo" } }, "single"],
    [{ type: "number", config: { text_mode: "rich" } }, "single"],
  ])("reads %j as %s", (over, expected) => {
    expect(textMode(column(over as Partial<Column>))).toBe(expected);
  });
});

describe("needsFullEditor", () => {
  it("is true only for rich and multiline text", () => {
    expect(needsFullEditor(column({ config: { text_mode: "rich" } }))).toBe(true);
    expect(needsFullEditor(column({ config: { text_mode: "multiline" } }))).toBe(true);
    expect(needsFullEditor(column())).toBe(false);
    expect(needsFullEditor(column({ type: "link" }))).toBe(false);
  });
});
```

Add `needsFullEditor, textMode` to that file's import from `./recordValues`.

Append to `cellEdit.test.ts`:

```ts
describe("cellEditKinds", () => {
  const field = (col: Column, enabled = true) => ({
    column: col,
    enabled,
    label: col.display_name,
  });

  it("sends a rich-text column to the dialog", () => {
    const col = column({ config: { text_mode: "rich" } });
    expect(cellEditKinds([field(col)]).get("c1")).toBe("dialog");
  });

  it("opens an ordinary column in place", () => {
    expect(cellEditKinds([field(column())]).get("c1")).toBe("inline");
  });

  it("locks a field the form marks read-only", () => {
    expect(cellEditKinds([field(column(), false)]).get("c1")).toBe("locked");
  });
});
```

Add `cellEditKinds` to that file's import from `./cellEdit`.

- [ ] **Step 6: Verify**

```bash
cd C:/Users/Asus/Projects/database-w-ui/frontend && npm test && npx tsc --noEmit
```

Expected: `Tests 25 passed (25)`, then no typecheck output.

- [ ] **Step 7: Branch and commit**

```bash
cd C:/Users/Asus/Projects/database-w-ui
git checkout -b fix/cell-affordance-and-keyboard-reach
git add frontend/src/features/records
git commit -F - <<'EOF'
Say what a text column's mode is in one place

Whether a text column is rich, multiline or a single line was being worked out
in three files: the dialog to pick an editor, the cell renderer to pick a
display, and the grid to decide whether a cell can hold an editor at all. Three
copies of one rule, including three copies of the part that is easy to forget —
that an unset or unrecognised mode is a single line. Adding a mode meant finding
all three, and the header of `RecordFields` says in as many words that the shared
`RecordField` exists so that this does not happen.

`textMode` is the accessor rather than a boolean because the three surfaces do
not ask the same question: two of them branch per mode and only the grid wants
the union, which is `needsFullEditor` on top of it.
EOF
```

---

### Task 7: A link cell in edit mode opens its editor

**Goal:** A link column stops advertising an editor it will not open (#5).

**Files:**
- Modify: `frontend/src/features/records/renderCell.tsx:26-33` (signature) and `:65-81` (link branch)
- Modify: `frontend/src/features/lists/ListDetailPage.tsx` (the column `body`)

**Acceptance Criteria:**
- [ ] `renderCell` takes `editing = false` as its last parameter
- [ ] `RecordCards` and `RecordViewDialog` are untouched and get the default
- [ ] With the mode on and the row editable, a link cell renders `linkLabel(...)` as text, no `<a>`
- [ ] With the mode off, the anchor is back

**Verify:** `cd frontend && npx tsc --noEmit && npm run build` → no output, then `✓ built`

**Steps:**

- [ ] **Step 1: Add the parameter**

```tsx
export function renderCell(
  column: Column,
  row: ListItem,
  listUuid: string,
  principalUuids: Record<PrincipalType, string[]>,
  lookupUuids: Map<string, string[]>,
  variant: CellVariant = "compact",
  /** The grid is in edit mode and this cell will open an editor. Defaults to
   *  false so the card list and the read-only dialog are unaffected. */
  editing = false,
) {
```

- [ ] **Step 2: Make the link plain text while editing**

Replace the link branch:

```tsx
  if (column.type === "link") {
    const href = safeHref(column, value);
    // A value the scheme check rejects is shown as plain text, never an anchor.
    if (!href) return String(value);
    // Nor is it an anchor while the cell is an editor. The `stopPropagation`
    // below exists so that following a link does not also select the row, and in
    // edit mode it eats the click the cell needs — leaving a cell drawn as
    // editable that opens a browser tab instead. Edit mode is already a mode
    // where a click means something else; turning it off makes this a link again.
    if (editing) return linkLabel(column, href);
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="hover:underline"
        style={{ color: "var(--brand)" }} // themed per palette; no Tailwind token for it
        onClick={(e) => e.stopPropagation()} // don't also toggle row selection
      >
        {linkLabel(column, href)}
      </a>
    );
  }
```

- [ ] **Step 3: Pass it from the grid**

In `ListDetailPage.tsx`, in the column's `body`:

```tsx
                body={(row: ListItem) => {
                  const cell = renderCell(
                    col,
                    row,
                    listUuid,
                    principalUuids,
                    lookupUuids,
                    "compact",
                    kind === "inline" && canItem(row.uuid, "edit"),
                  );
```

- [ ] **Step 4: Verify**

```bash
cd C:/Users/Asus/Projects/database-w-ui/frontend && npx tsc --noEmit && npm test && npm run build
```

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Asus/Projects/database-w-ui
git add frontend/src/features/records/renderCell.tsx frontend/src/features/lists/ListDetailPage.tsx
git commit -F - <<'EOF'
Let a link cell be edited from the part of it that looks editable

A link column is a single-line text input like any other, so the grid drew its
cells with the dashed outline that means "this opens". Clicking the value opened
a browser tab instead: the anchor stops the click so that following a link does
not also select the row, and that same guard eats the click the cell needs.
Clicking the whitespace beside the link opened the editor, so the same cell did
two different things depending on where in it you hit — the sort of behaviour
people conclude is their own fault.

While the mode is on and the row is one this caller may write, the value renders
as its label rather than as an anchor. That follows from what the mode already
is: a click has been spent on the cell under it, which is why clicking a row
stopped opening the record too. Turn the mode off and the link is a link.
EOF
```

---

### Task 8: Escape dismisses the panel without cancelling the cell

**Goal:** Dismissing a dropdown's overlay with Escape stops throwing away the option just chosen (#10).

**Files:**
- Modify: `frontend/src/features/records/inlineEdit.tsx` (`InlineCellEditor`'s wrapper)

**Acceptance Criteria:**
- [ ] `onKeyDownCapture` records whether `[data-pr-is-overlay]` is in the document when Escape is pressed
- [ ] `onKeyDown` on the same element stops propagation only when that ref is set, and resets it
- [ ] Escape on a cell with no overlay open still cancels the edit

**Verify:** `cd frontend && npx tsc --noEmit && npm run build` → no output, then `✓ built`

**Steps:**

- [ ] **Step 1: Add the ref and the two handlers**

In `InlineCellEditor`, above the `return`:

```tsx
  // Escape means two things in the same place. PrimeReact's overlay components
  // close their panel on it and call `preventDefault()` but never
  // `stopPropagation()` (dropdown.cjs.js:1187-1190; Calendar is the same), so the
  // keypress carries on to the `<td>`, which reads it as "cancel this cell" and
  // throws away the option that was just picked.
  //
  // Sampled at capture because by the time it bubbles back the panel has been
  // hidden and the evidence is gone. `data-pr-is-overlay` is the library's own
  // marker rather than a guessed class: datatable.cjs.js:1232-1238 uses exactly it
  // to stop an outside click *inside* a dropdown from closing the cell, which is
  // the same distinction being drawn here.
  //
  // Note this cannot key off `event.defaultPrevented`: `onEscapeKey` calls
  // `preventDefault()` whether or not a panel is open, so that flag is set on
  // every Escape in a dropdown and swallowing it would break cancel outright.
  const dismissedOverlay = useRef(false);
```

Replace the wrapper element:

```tsx
    <div
      className="cell-editor"
      onKeyDownCapture={(e) => {
        if (e.key === "Escape") {
          dismissedOverlay.current =
            document.querySelector("[data-pr-is-overlay]") !== null;
        }
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape" && dismissedOverlay.current) {
          dismissedOverlay.current = false;
          e.stopPropagation();
        }
      }}
    >
```

- [ ] **Step 2: Verify**

```bash
cd C:/Users/Asus/Projects/database-w-ui/frontend && npx tsc --noEmit && npm test && npm run build
```

- [ ] **Step 3: Commit**

```bash
cd C:/Users/Asus/Projects/database-w-ui
git add frontend/src/features/records/inlineEdit.tsx
git commit -F - <<'EOF'
Stop Escape cancelling a cell when it was only closing a panel

Open a choice cell, pick an option, reopen the panel, press Escape to get rid of
it: the panel closed and the option went with it. PrimeReact's overlay components
close on Escape and call `preventDefault()` without ever calling
`stopPropagation()`, so the same keypress carries on to the cell, which has no
way of knowing it has already been spent and reads it as a cancel.

The tempting fix is to swallow an Escape that arrives already default-prevented,
and it is wrong: `onEscapeKey` calls `preventDefault()` whether or not a panel is
open, so the flag is set on every Escape pressed in a dropdown and keying off it
would break cancelling outright on every choice, lookup and reference column.

What actually distinguishes the two is whether a panel was open, which has to be
sampled before the panel closes — hence capture on the way in and the decision on
the way back out. `data-pr-is-overlay` is the same attribute the table's own
outside-click handler uses to tell a click inside a dropdown from a click away
from the cell.
EOF
```

---

### Task 9: Make the record-opening cells reachable by keyboard

**Goal:** Rich-text and multiline columns stop being mouse-only in edit mode (#11).

**Files:**
- Modify: `frontend/src/features/lists/ListDetailPage.tsx` (the `kind === "dialog"` branch of the column `body`)

**Acceptance Criteria:**
- [ ] The span has `role="button"`, `tabIndex={0}` and an `aria-label` naming the column
- [ ] Enter and Space open the record dialog; Space does not scroll the page
- [ ] The existing `title` is kept

**Verify:** `cd frontend && npx tsc --noEmit && npm run build` → no output, then `✓ built`

**Steps:**

- [ ] **Step 1: Replace the span**

```tsx
                  // Rich text and multiline have no cell-sized editor, so the
                  // click goes where their editor lives instead of doing nothing.
                  // A real button, not a bare span: the column deliberately has no
                  // `editor`, so PrimeReact renders no tab stop for it, and the row
                  // click is off while the mode is on — leaving this the only route
                  // to those columns, and until now a mouse-only one.
                  if (kind !== "dialog" || !canItem(row.uuid, "edit")) return cell;
                  const openRecord = () => setRecordDialog({ open: true, item: row });
                  const hint = `Edit ${col.display_name} in the record's own form`;
                  return (
                    <span
                      role="button"
                      tabIndex={0}
                      aria-label={hint}
                      title={hint}
                      onClick={openRecord}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault(); // Space would scroll the grid
                          openRecord();
                        }
                      }}
                    >
                      {cell}
                    </span>
                  );
```

- [ ] **Step 2: Verify**

```bash
cd C:/Users/Asus/Projects/database-w-ui/frontend && npx tsc --noEmit && npm test && npm run build
```

- [ ] **Step 3: Commit**

```bash
cd C:/Users/Asus/Projects/database-w-ui
git add frontend/src/features/lists/ListDetailPage.tsx
git commit -F - <<'EOF'
Reach a rich text cell without a mouse

A column that opens the record rather than an editor deliberately has no
`editor`, so PrimeReact renders no tab stop for it, and the row click is off
while the mode is on. That left a bare `span` with an `onClick` as the only way
into those columns from the grid — no role, no tab index, no key handler, and
nothing announcing it as something you could activate. Someone working by
keyboard or screen reader had no route to a rich text or multiline column at all
except the row's overflow menu.

It is the same element with the things a button needs. Space is prevented
because otherwise it would also scroll the grid out from under the dialog it
just opened.
EOF
```

---

### Task 10: Ship PR 2

**Goal:** PR 2 is merged into `main` and `main` is synced locally.

**Files:** none — git and `gh` only.

**Acceptance Criteria:**
- [ ] `npx tsc --noEmit`, `npm test` and `npm run build` all pass on the branch tip
- [ ] PR body names the findings closed (#5, #10, #11, #12) and cites the PrimeReact lines behind #10
- [ ] PR merged, local `main` fast-forwarded, tree clean

**Verify:** `gh pr view <n> --json state` → `{"state":"MERGED"}`

**Steps:**

- [ ] **Step 1: Final green run**

```bash
cd C:/Users/Asus/Projects/database-w-ui/frontend && npx tsc --noEmit && npm test && npm run build
```

- [ ] **Step 2: Push and open the PR**

```bash
cd C:/Users/Asus/Projects/database-w-ui
git push -u origin fix/cell-affordance-and-keyboard-reach
gh pr create --base main --head fix/cell-affordance-and-keyboard-reach \
  --title "Make a cell behave the way it is drawn, and reach it without a mouse" \
  --body "Closes review findings #5, #10, #11 and #12.

## #12 — one vocabulary for text modes
\`textMode(column)\` and \`needsFullEditor(column)\` move into \`recordValues.ts\`. \`cellEditKinds\`, \`RecordFields\` and \`renderCell\` all read them instead of each restating \`config.text_mode === \"rich\" | \"multiline\"\` — including the easily-forgotten part, that an unset or unrecognised mode is a single line.

## #5 — link cells
A link's anchor stops click propagation so that following it does not also select the row; in edit mode that ate the click the cell needed, so the cell advertised an editor and opened a browser tab. While the mode is on and the row is writable, the value renders as its label rather than as an anchor.

## #10 — Escape
PrimeReact's overlays close on Escape with \`preventDefault()\` and no \`stopPropagation()\` (\`dropdown.cjs.js:1187-1190\`), so the keypress reached the \`<td>\` and cancelled the whole cell edit, discarding an option just picked.

Keying off \`event.defaultPrevented\` was tried and rejected: \`onEscapeKey\` calls \`preventDefault()\` whether or not a panel is open, so that flag is set on every Escape in a dropdown and swallowing it would have broken cancel on every choice, lookup and reference column. The fix samples \`[data-pr-is-overlay]\` at capture — the same attribute \`datatable.cjs.js:1232-1238\` uses for its own outside-click check — and stops propagation on the way back out.

## #11 — keyboard reach
The span that opens the record for rich-text and multiline columns gains \`role=\"button\"\`, \`tabIndex\`, an aria-label and an Enter/Space handler. It was the only route to those columns in edit mode and it was mouse-only.

\`tsc --noEmit\`, \`npm test\` (25) and \`vite build\` all clean."
```

- [ ] **Step 3: Merge and sync**

```bash
cd C:/Users/Asus/Projects/database-w-ui
gh pr merge --merge
git checkout main && git pull --ff-only
```

---

## PR 3 — Permission gating

Branch: `fix/cell-permission-gating`

### Task 11: A server-stamped column is shown, not typed into

**Goal:** A column with `config.onupdate` comes back read-only from `resolveFields`, so neither the grid nor the dialog offers an input the server will overwrite (#9).

**Files:**
- Modify: `frontend/src/features/records/recordValues.ts:40-74` (`resolveFields`)
- Modify: `frontend/src/features/records/recordValues.test.ts`

**Acceptance Criteria:**
- [ ] A column with `config.onupdate` set comes back `enabled: false` from all three branches of `resolveFields` — configured, appended, and the no-config default
- [ ] It comes back `enabled: false` even when the form config says `enabled: true`
- [ ] It is still *present* in the result — not filtered out — so `RecordViewDialog` and `RecordFormDialog` still show it
- [ ] A column without `onupdate` is unaffected

**Verify:** `cd frontend && npm test` → `Tests 29 passed (29)`

**Steps:**

- [ ] **Step 1: Add the predicate above `resolveFields`**

```ts
/**
 * A column the server writes for you cannot be an input, whatever a form config
 * says: `config.onupdate` is stamped over anything sent on every accepted write
 * (`ListItemService._validate_data`). Read-only rather than absent, because a
 * "last modified" is exactly the sort of thing you want to *see* on a record —
 * which is what `enabled: false` already means everywhere it is read.
 *
 * The same reasoning `resolveFields` applies to calculated columns, one step
 * short of it: those store nothing at all, so they are dropped rather than shown.
 */
function writable(column: Column): boolean {
  return !column.config.onupdate;
}
```

- [ ] **Step 2: Apply it in all three branches**

In the configured branch:

```ts
        return {
          column,
          enabled: f.enabled !== false && writable(column),
          label:
            (f.label_override && f.label_override.trim()) || column.display_name,
        };
```

In the appended branch:

```ts
    const appended = eligible
      .filter((c) => !known.has(c.uuid))
      .map((c) => ({ column: c, enabled: writable(c), label: c.display_name }));
```

And the no-config return:

```ts
  return eligible.map((c) => ({
    column: c,
    enabled: writable(c),
    label: c.display_name,
  }));
```

- [ ] **Step 3: Add the tests**

Append to `recordValues.test.ts`:

```ts
describe("resolveFields and server-stamped columns", () => {
  const name = column({ uuid: "name" });
  const stamped = column({ uuid: "updated", config: { onupdate: "now" } });

  it("marks an onupdate column read-only with no form config", () => {
    const fields = resolveFields([name, stamped], null);
    expect(fields.map((f) => [f.column.uuid, f.enabled])).toEqual([
      ["name", true],
      ["updated", false],
    ]);
  });

  it("keeps it read-only even when the form config enables it", () => {
    const form = {
      config: {
        fields: [
          { column_uuid: "name", included: true, enabled: true, order: 0 },
          { column_uuid: "updated", included: true, enabled: true, order: 1 },
        ],
      },
    } as unknown as Form;
    const fields = resolveFields([name, stamped], form);
    expect(fields.find((f) => f.column.uuid === "updated")?.enabled).toBe(false);
  });

  // Read-only, not absent: a stamp is exactly the sort of thing you want to see
  // on a record you are editing.
  it("still returns the column so a form can show it", () => {
    const fields = resolveFields([name, stamped], null);
    expect(fields.map((f) => f.column.uuid)).toContain("updated");
  });

  it("leaves an ordinary column alone", () => {
    expect(resolveFields([name], null)[0].enabled).toBe(true);
  });
});
```

Add `resolveFields` to the import from `./recordValues`, and `import type { Column, Form } from "../../lib/types";`.

- [ ] **Step 4: Verify**

```bash
cd C:/Users/Asus/Projects/database-w-ui/frontend && npm test && npx tsc --noEmit
```

Expected: `Tests 29 passed (29)`, then no typecheck output.

- [ ] **Step 5: Branch and commit**

```bash
cd C:/Users/Asus/Projects/database-w-ui
git checkout -b fix/cell-permission-gating
git add frontend/src/features/records/recordValues.ts frontend/src/features/records/recordValues.test.ts
git commit -F - <<'EOF'
Show a stamped column without offering to type into it

A column carrying an `onupdate` token is written by the server on every accepted
write, whatever the request said. Both editing surfaces offered it as an input
anyway. In the dialog that was a field you could fill in and watch not take; in
the grid the optimistic update made it worse, since the typed value sat in the
cell for the length of the round trip before the record came back and replaced
it, which looks like the save going wrong rather than like the column doing its
job.

Read-only rather than absent. A "last modified" is exactly the sort of thing you
want to see on a record you are editing, and `enabled: false` is already what
every reader of a resolved field does with "shown, not writable" — the dialog
disables the input, the grid locks the cell, and the QR fill skips it. So one
rule in `resolveFields` reaches all four surfaces without any of them learning a
new word.

This is the reasoning already applied to calculated columns, one step short of
it: those store nothing at all, so they are dropped rather than shown.
EOF
```

---

### Task 12: One editability answer per column

**Goal:** The class, the body and the edit gate cannot disagree about the same cell (#14).

**Files:**
- Modify: `frontend/src/features/lists/ListDetailPage.tsx` (the `visibleCols.map` block)
- Modify: `frontend/src/features/records/cellEdit.ts` (delete `cellEditKind`)

**Acceptance Criteria:**
- [ ] One `isEditable(row)` per column drives `bodyClassName`, `body` and `onBeforeCellEditShow`
- [ ] `cellEditKind` is deleted and its `?? "locked"` folded into the map read
- [ ] Behaviour is unchanged: a locked column, and a row the caller cannot edit, still render flat and still refuse the editor

**Verify:** `cd frontend && npx tsc --noEmit && npm run build` → no output, then `✓ built`

**Steps:**

- [ ] **Step 1: Compute it once at the top of the map**

```tsx
          {visibleCols.map((col) => {
            // Everything is "locked" while the mode is off, which is how a column
            // that is editable in principle still renders as a plain value.
            const kind: CellEditKind = inlineEdit
              ? (editKinds.get(col.uuid) ?? "locked")
              : "locked";
            // One answer for the outline, the cell body and the gate on opening an
            // editor. Asked separately they could disagree about the same cell —
            // a cell drawn as editable that refuses to open, or the reverse.
            const isEditable = (row: ListItem) =>
              kind !== "locked" && canItem(row.uuid, "edit");
            return (
```

- [ ] **Step 2: Use it in all three callbacks**

```tsx
                bodyClassName={(row: ListItem) =>
                  !isEditable(row)
                    ? ""
                    : kind === "inline"
                      ? "cell-editable"
                      : "cell-opens-record"
                }
```

```tsx
                body={(row: ListItem) => {
                  const editing = isEditable(row);
                  const cell = renderCell(
                    col,
                    row,
                    listUuid,
                    principalUuids,
                    lookupUuids,
                    "compact",
                    kind === "inline" && editing,
                  );
                  if (kind !== "dialog" || !editing) return cell;
```

```tsx
                onBeforeCellEditShow={(e) =>
                  isEditable(e.rowData as ListItem) ? undefined : false
                }
```

- [ ] **Step 3: Delete `cellEditKind`**

Remove the function from `cellEdit.ts` and drop it from the `../records/cellEdit` import in `ListDetailPage.tsx`. Add `type CellEditKind` to that import.

- [ ] **Step 4: Verify**

```bash
cd C:/Users/Asus/Projects/database-w-ui/frontend && npx tsc --noEmit && npm test && npm run build
```

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Asus/Projects/database-w-ui
git add frontend/src/features/lists/ListDetailPage.tsx frontend/src/features/records/cellEdit.ts
git commit -F - <<'EOF'
Ask once whether a cell can be edited

Three callbacks decided it separately: the one that draws the outline, the one
that renders the value, and the one that gates the editor opening. All three
asked the same two questions in the same order and could still have drifted
apart, and the failure would have been the confusing kind — a cell drawn as
editable that refuses to open, or one that opens with nothing to say it would.
One predicate per column now answers for all three, which makes disagreeing
impossible rather than merely unlikely.

`cellEditKind` goes with it: a one-line wrapper supplying a `?? "locked"` default
that its only caller then restated on the line above.
EOF
```

---

### Task 13: Break the stale permission closure in the cell memo

**Goal:** Cells that become editable after the permission query resolves actually become editable (#8) — with the render cost measured before the approach is locked in.

> **USER-ORDERED GATE — NON-SKIPPABLE.** This task was requested by the user in the current conversation. It MUST NOT be closed by walking around it, by declaring it "verified inline", or by substituting a cheaper check. Close only after every item in `acceptanceCriteria` has been re-validated independently, with output captured.

**Files:**
- Modify: `frontend/src/features/lists/ListDetailPage.tsx` (the `<DataTable>` element)

**Acceptance Criteria:**
- [ ] `cellMemo={false}` is set on the `<DataTable>` with a comment naming why the memo cannot be narrowed
- [ ] The grid is exercised on a real list with the permission map resolving late, and the affordance appears on rows the caller may edit
- [ ] Interaction cost is observed on a full page and reported with what was measured and how
- [ ] If the cost is judged unacceptable, `cellMemo={false}` is reverted and `editMode` is instead gated on the effective-actions query settling — and the narrower hole (a mid-session Share change) is stated in the commit message

**Verify:** Run the app (`uvicorn main:create_app --factory` on :5000, `npm run dev`, open `http://localhost:5173`), load a list seeded by `uv run python -m src.demo.school`, toggle "Edit cells", and exercise the grid. Capture what was measured.

**Steps:**

- [ ] **Step 1: Set `cellMemo={false}`**

On the `<DataTable>` element, next to `editMode`:

```tsx
          // PrimeReact memoises every body cell against a fixed prop list
          // (datatable.cjs.js:493) that does not include the column, so a cell
          // holds on to the `canItem` closure it first rendered with. When the
          // effective-actions query resolves after the grid has drawn, cells that
          // just became editable never gain the affordance and their
          // `onBeforeCellEditShow` still answers `false` on rows this caller may
          // in fact write.
          //
          // The memo cannot be narrowed into correctness: adding `column` to
          // `cellMemoProps` compares a React element rebuilt every render, which
          // never matches, so the memo would be off in all but name. Off honestly
          // is the smaller lie, and it also covers the case gating on the query
          // would miss — a permission changed through the Share dialog mid-session.
          cellMemo={false}
```

- [ ] **Step 2: Start the app**

```bash
cd C:/Users/Asus/Projects/database-w-ui/backend && uv run --system-certs uvicorn main:create_app --factory --port 5000
```

In a second shell:

```bash
cd C:/Users/Asus/Projects/database-w-ui/frontend && npm run dev
```

Open `http://localhost:5173` (not `127.0.0.1` — vite binds IPv6 here).

- [ ] **Step 3: Seed a list worth measuring on, if one is not there**

```bash
cd C:/Users/Asus/Projects/database-w-ui/backend && uv run --system-certs python -m src.demo.school
```

This is idempotent and re-runnable. It seeds ~740 records across every column type.

- [ ] **Step 4: Check the correctness half**

Hard-reload the list page so the effective-actions query resolves after the first paint, turn on "Edit cells", and confirm every row the caller may edit shows the dashed `cell-editable` outline and opens an editor on click. Before this change, rows that resolved late stayed flat and refused to open.

- [ ] **Step 5: Measure the cost half**

With the mode on and a full page of records on screen, exercise the interactions that re-render the page: type into the search box (each keystroke re-renders before the debounce fires), page through the paginator, and toggle a column's visibility. Record what you observe and how you observed it — the browser's Performance panel over a few seconds of typing is enough, and the number that matters is whether a keystroke's render is perceptible.

- [ ] **Step 6: Decide, and record the decision**

If the cost is not perceptible, keep `cellMemo={false}` and commit with the measurement in the message.

If it is perceptible, revert `cellMemo={false}` and gate the edit mode instead:

```tsx
          // Applied only once the per-row permissions have resolved. `editMode` is
          // in the cell memo's compare list, so flipping it re-renders every cell
          // with a final `canItem` — where turning the mode on beforehand would
          // leave cells holding the closure they first drew with. The hole this
          // leaves is narrower than the one it closes: a permission changed through
          // the Share dialog mid-session invalidates ["effective"] without touching
          // any prop the memo compares.
          editMode={inlineEdit && permissionsReady ? "cell" : undefined}
```

which needs `useEffectiveActionsMap` to say whether it has answered yet. It has six
call sites, so widen the return rather than reshaping it — a callable that also
carries the flag keeps every existing `can(uuid, action)` working untouched. In
`useEffectiveActions.ts`:

```ts
/** The batch answer for a collection: callable per row, and able to say whether
 *  it has arrived. Most callers only hide a control and can ignore `ready`; one
 *  that changes a grid's *mode* cannot, because PrimeReact memoises its cells
 *  against a prop list this is not part of. */
export type ActionsMap = ((
  uuid: string,
  action: PermissionAction,
) => boolean) & { ready: boolean };
```

and at the end of `useEffectiveActionsMap`, replacing the bare `return`:

```ts
  const can = ((uuid: string, action: PermissionAction) => {
    const rowActions = byUuid.get(uuid);
    if (rowActions) return rowActions.has(action);
    return fallbackActions?.has(action) ?? false;
  }) as ActionsMap;
  // Nothing to wait for when there is nothing to ask about: the query is disabled
  // on an empty list, so it would never report success.
  can.ready = key.length === 0 || query.isSuccess;
  return can;
```

with its declared return type changed from the inline function type to `ActionsMap`.
Then in `ListDetailPage.tsx`, `permissionsReady` is simply `canItem.ready`.

Say plainly in the commit message which branch was taken and why.

- [ ] **Step 7: Verify and commit**

```bash
cd C:/Users/Asus/Projects/database-w-ui/frontend && npx tsc --noEmit && npm test && npm run build
```

```bash
cd C:/Users/Asus/Projects/database-w-ui
git add frontend/src/features/lists/ListDetailPage.tsx
git commit
```

Write the message in the repo's style, and include: what the stale closure did, why `cellMemoProps` cannot be narrowed, what was measured and how, and which branch was taken.

```json:metadata
{"userGate": true, "tags": ["user-gate"], "requiresUserSpecification": true, "files": ["frontend/src/features/lists/ListDetailPage.tsx"], "verifyCommand": "cd frontend && npm run dev  # then exercise the grid by hand", "acceptanceCriteria": ["cellMemo={false} set with a comment naming why the memo cannot be narrowed", "affordance appears on rows whose permissions resolved after first paint", "interaction cost observed on a full page and reported with method", "if cost unacceptable, reverted to gating editMode and the narrower hole stated"], "modelTier": "frontier"}
```

---

### Task 14: Ship PR 3

**Goal:** PR 3 is merged into `main`, closing the last of the fourteen review findings.

**Files:** none — git and `gh` only.

**Acceptance Criteria:**
- [ ] `npx tsc --noEmit`, `npm test` and `npm run build` all pass on the branch tip
- [ ] PR body names the findings closed (#8, #9, #14) and reports Task 13's measurement
- [ ] PR merged, local `main` fast-forwarded, tree clean
- [ ] All fourteen findings from the original review are accounted for

**Verify:** `gh pr view <n> --json state` → `{"state":"MERGED"}`

**Steps:**

- [ ] **Step 1: Final green run**

```bash
cd C:/Users/Asus/Projects/database-w-ui/frontend && npx tsc --noEmit && npm test && npm run build
```

- [ ] **Step 2: Push and open the PR**

```bash
cd C:/Users/Asus/Projects/database-w-ui
git push -u origin fix/cell-permission-gating
gh pr create --base main --head fix/cell-permission-gating \
  --title "Gate a cell on permissions that have actually arrived" \
  --body "Closes review findings #8, #9 and #14 — the last of the fourteen from the inline cell editing review.

## #9 — a server-stamped column is shown, not typed into
A column with \`config.onupdate\` is written by the server on every accepted write. Both editing surfaces offered it as an input. \`resolveFields\` now returns it \`enabled: false\` — read-only rather than absent, so a \"last modified\" is still visible on the record — and that one rule reaches the dialog, the grid, the view dialog and the QR fill through machinery that already existed.

## #8 — the stale permission closure
PrimeReact memoises body cells against a fixed prop list (\`datatable.cjs.js:493\`) that excludes the column, so a cell keeps the \`canItem\` closure it first rendered with. Cells that became editable after the effective-actions query resolved never gained the affordance and refused to open.

Narrowing the memo does not work: adding \`column\` to \`cellMemoProps\` compares a React element rebuilt every render, so it would never match and the memo would be off in all but name.

[Report here what was measured, how, and which branch was taken.]

## #14 — one editability answer per column
\`bodyClassName\`, \`body\` and \`onBeforeCellEditShow\` each resolved editability separately. One predicate per column now answers for all three. \`cellEditKind\` — a one-line \`?? \"locked\"\` wrapper whose only caller restated the default anyway — is deleted.

\`tsc --noEmit\`, \`npm test\` (29) and \`vite build\` all clean."
```

- [ ] **Step 3: Merge and sync**

```bash
cd C:/Users/Asus/Projects/database-w-ui
gh pr merge --merge
git checkout main && git pull --ff-only
git --no-pager log --oneline -6
```

- [ ] **Step 4: Confirm the review is closed out**

All fourteen findings should now be accounted for: #1, #2, #3 and #6 in PRs #42 and #43; #4, #7 and #13 in PR 1; #5, #10, #11 and #12 in PR 2; #8, #9 and #14 in PR 3.
