# Closing the inline cell editing review findings

**Date:** 2026-07-31
**Scope:** the ten open findings from the code review of PR #41 (`16a3c98`, "Edit a
record's values from the grid, behind a toggle")

---

## Where this starts

The review raised fourteen findings. Four are closed:

| # | What it was | Closed by |
|---|---|---|
| 1 | `onMutate` prefix-matched the metric-values query and threw, so no PATCH was ever sent | PR #43 |
| 2 | The editor seeded from PrimeReact's stale `rowData`, so an untouched cell could revert a newer value | PR #43 |
| 3 | Overlapping cell PATCHes were an unlocked read-modify-write of one JSONB blob | PR #42 |
| 6 | Temporal values did not round-trip, so opening a date cell and closing it sent a no-op PATCH | fell out of #2's fix |

Ten remain. All are frontend, and none of them lose data outright — #3 was the only
one that did, and it is fixed.

## Decisions taken before designing

Five choices shaped everything below. They are recorded here because the design does
not make sense without them.

1. **A test harness, for pure logic only.** `frontend/` has no tests at all — no
   vitest, no testing-library, no test files. We add vitest with a node environment
   and no jsdom. That pins the pure functions the fixes touch and leaves the
   DOM-shaped findings verified by reading and by hand. Rendering PrimeReact
   components in jsdom is a standing maintenance cost we are not taking on for this.

2. **#9 is fixed in `resolveFields`, so both surfaces get it.** The record dialog has
   the same gap as the grid. One rule, applied where both already read from.

3. **The write path gets the full treatment** — trust the draft, roll back on error,
   use the record the response already carries. See PR 1.

4. **Three thematic PRs**, each one idea with one reasoning message, matching how
   #40–#43 were shaped.

5. **#8 is fixed with `cellMemo={false}`**, not by gating on the permission query.
   See PR 3 for why the narrower fix leaves a hole.

---

## PR 1 — The write path

**Findings:** #4, #7, #13. **Also:** the vitest harness.

### #4 — the draft is the record

`commitCell` currently discards a draft whose row or column does not match the one on
the event, with no feedback. PrimeReact keys body cells by `${rowIndex}_${field}_${index}`,
so a `useLiveList` poll that reorders or repages rows leaves an open editor sitting at
a position now held by a different record. The guard correctly prevents a wrong write,
but turns it into invisible data loss.

PR #43 already changed the mutate target to `draft.rowUuid`, which makes the guard
vestigial: the draft knows which record and which column it belongs to, so a repage is
no reason to throw the edit away. `commitCell` stops reading the event's row entirely.

```ts
function commitCell() {
  const draft = cellDraft.current;
  cellDraft.current = null;
  if (!draft) return;
  const column = cols.find((c) => c.uuid === draft.columnUuid);
  if (!column) return;          // the column was deleted while the editor was open
  const patch = cellPatch(cols, column, draft);
  if (patch) saveCell.mutate({ uuid: draft.rowUuid, patch });
}
```

`onCellEditComplete` becomes `() => commitCell()`. The one remaining bail — a column
deleted mid-edit — is the only case where there is genuinely nothing to send.

A record deleted server-side while its editor was open now produces a 404 and a toast
rather than silence. That is the honest outcome.

### #7 — rollback, the returned record, and one refetch per burst

Three changes to `saveCell`:

- **`onMutate` returns a rollback context.** It snapshots the matching pages with
  `getQueriesData` before writing the optimistic value, and returns them. `onError`
  restores them. Today recovery depends entirely on `onSettled`'s refetch succeeding,
  which is exactly what fails offline: the toast says the save failed, the invalidate
  cannot refetch, and the rejected value sits on screen indefinitely — contradicting
  the "the refetch is the revert" comment the code carries.

- **`onSuccess` writes the record the PATCH already returned.** `api.patch<ListItem>`
  resolves to the server's own rendering of the record, with calculated columns
  recomputed and `onupdate` columns stamped. Discarding it and refetching the page is
  strictly more work for a strictly worse answer.

- **`onSettled` still invalidates, but debounced.** The returned record cannot say that
  the row has moved out of the active filter or changed sort position, and the metric
  cards go stale on every write, so a refetch is still needed. It is not needed *per
  cell*: a ~800 ms debounce, cleared on unmount, collapses a burst of commits into one
  refetch instead of ten for tabbing across ten columns.

### #13 — two duplicated rules, one home each

`toSend` in `inlineEdit.tsx` duplicates `buildRecordData`'s `empty ? null :
serialize(column, value)` (`recordValues.ts:143-146`), and the nested-descendant
clearing loop duplicates `RecordFields.tsx:288-293`. Both rules now exist twice, so a
change to what "empty" means, or to how a changed parent cascades, has to be made in
two places or the dialog and the grid will disagree about the same edit.

Both move into `recordValues.ts`:

- `toStored(column, value)` — the empty-or-serialize rule. Used by `buildRecordData`
  and by `cellPatch`. `toSend` in `inlineEdit.tsx` is deleted, not kept as an alias.
- `clearedNested(columns, changedUuid): Record<string, null>` — returns the map rather
  than mutating, so both call sites spread it:

```ts
const patch = { [column.uuid]: next, ...clearedNested(columns, column.uuid) };
```

### The harness

vitest, a `test` block in `vite.config.ts`, node environment, no jsdom. One
devDependency and one script.

First tests:

- `cellPatch` — an untouched draft sends nothing, including the `"14:30"` temporal case
  that used to leak a PATCH; a real edit sends; a changed lookup clears its descendants.
- `toStored` — the several spellings of absence collapse to `null`; a `Date` serializes
  per column type.
- `clearedNested` — a changed parent clears children and grandchildren, and nothing else.

---

## PR 2 — Reach and affordance

**Findings:** #5, #10, #11, #12.

### #12 — one vocabulary for text modes

The `config.text_mode === "rich" | "multiline"` branching exists in three files, but
they are not the same predicate: `cellEditKinds` wants the union, `RecordFields` picks
an editor per mode, `renderCell` picks a display per mode. The shared thing is
therefore the accessor, not a boolean.

`textMode(column): "rich" | "multiline" | "single"` goes in `recordValues.ts` — not
`columnTypes.ts`, which is only the add-column dropdown's options. It carries the
"anything unrecognised, including unset, is a single line" rule that all three sites
currently restate. `needsFullEditor(column)` is then `textMode(column) !== "single"`,
used only by `cellEditKinds`.

Adding or renaming a text mode becomes one edit instead of three that must stay in
agreement — the drift `RecordFields.tsx`'s own header says the shared `RecordField`
exists to prevent.

### #5 — link cells advertise an editor and navigate instead

`renderCell` renders a link value as `<a … onClick={(e) => e.stopPropagation()}>`
(`renderCell.tsx:76`), so that clicking a link does not also toggle row selection.
`cellEditKinds` classifies `link` as `"inline"`, so the cell gets `cell-editable` and
the dashed hover outline. Clicking the link text stops the click before the `<td>`'s
handler runs: a new tab opens, no editor appears. Clicking the whitespace beside the
anchor opens the editor. The same cell behaves two ways depending on where you hit it.

`renderCell` gains an `editing` flag, defaulting to `false` so that `RecordCards` —
the other caller, which has no edit mode — is unaffected. A link column in edit mode
renders its label as plain text rather than an anchor. Edit mode is already a mode where a click means
something different — that is the feature's premise — so a link that stops being a
link while you are editing is consistent rather than surprising. Turning the toggle
off restores it.

The alternative, marking `link` as non-inline-editable, gives up a perfectly good
single-line text input to dodge a click handler.

### #10 — Escape is overloaded

PrimeReact's Dropdown `onEscapeKey` (`dropdown.cjs.js:1187-1190`) calls only
`event.preventDefault()`, never `stopPropagation()`; Calendar behaves the same. The
keydown bubbles to the `<td>`'s handler, which sees Escape and calls
`switchCellToViewMode(event, false)` → `onCellEditCancel` → the draft is dropped. Open
a choice cell, pick an option, reopen the panel, press Escape to dismiss it: the panel
closes and the option you just picked is thrown away.

**Rejected:** keying off `e.defaultPrevented`. `onEscapeKey` calls `preventDefault()`
*unconditionally* — open panel or not — and is reached on every Escape inside the
dropdown. Swallowing on that flag would break Escape-to-cancel on every choice, lookup
and reference column, trading a narrow bug for a wide one.

**Chosen:** sample the DOM at capture time. Both handlers live on `InlineCellEditor`'s
existing `cell-editor` wrapper:

- `onKeyDownCapture` — if the key is Escape and `document.querySelector("[data-pr-is-overlay]")`
  matches, record it in a ref. At capture the panel is still mounted, because
  PrimeReact's `hide()` runs on its own handler further in.
- `onKeyDown` (bubble, same div) — if the ref is set, `stopPropagation()`. The `<td>`
  never sees the Escape; the panel still closes.

`data-pr-is-overlay` is the library's own contract rather than a guessed class name:
`datatable.cjs.js:1232-1238` uses exactly that attribute to keep its outside-click
handler from closing a cell when you click inside a dropdown panel.

### #11 — rich-text and multiline cells are keyboard-unreachable

For `kind === "dialog"` the column deliberately has no `editor`, so PrimeReact renders
no tab stop for it, and `rowOpener` returns `null` while the mode is on, so the row
click is disabled too. The only route to those columns in edit mode is a bare
`<span onClick>` with no role, tabIndex or key handler.

The span gets `role="button"`, `tabIndex={0}`, an Enter/Space `onKeyDown`, and an
aria-label naming the column.

---

## PR 3 — Permission gating

**Findings:** #8, #9, #14.

### #9 — disabled, not absent

A column with an `config.onupdate` token is offered as an editable inline cell whose
typed value the server unconditionally replaces with the stamp
(`backend/src/lists/items/services.py:846-849`). The optimistic update makes this worse
than the dialog's version of the same gap: the typed value shows for the round trip,
then the refetch silently replaces it.

Every `resolveFields` call site was checked. `includeCalculated: true` is used by
exactly one caller, `RecordViewDialog` — a display surface. The three input surfaces
(the grid's edit kinds, the record dialog, the QR create fill) all take the default.

So the fix is **not** to filter `onupdate` columns out, which would make a "Last
modified" field vanish from the edit dialog. It is to force `enabled: false` on them
regardless of what the form config says. That one rule reaches four surfaces through
machinery that already exists:

| Surface | Effect |
|---|---|
| `RecordViewDialog` | unaffected — still shows the column and its value |
| `RecordFormDialog` | field visible, disabled. You can read the stamp, not type into it |
| `cellEditKinds` | `!enabled` already maps to `"locked"`, so the cell stays flat |
| `buildRecordData` | already skips `!enabled` fields, so a QR code cannot set the stamp |

This is the reasoning `resolveFields` already applies to calculated columns: the server
refuses the value, so offering an input for it is a lie.

### #8 — `cellMemo={false}`

`BodyCell`'s comparator uses a fixed `cellMemoProps` list (`datatable.cjs.js:493`) that
does not include the column, and `cellMemo` defaults to `true`. When
`useEffectiveActionsMap` resolves after the grid has rendered with `editMode="cell"`,
cells that just became editable never gain the `cell-editable` affordance and their
`onBeforeCellEditShow` still answers `false` on rows the caller may in fact write.

**Rejected:** adding `column` to `cellMemoProps`. The Column is a React element
recreated every render, so it would never compare equal — the memo would be off in all
but name.

**Rejected:** gating `editMode="cell"` on the effective-actions query settling.
`editMode` *is* in the compare list, so cells would re-render when it flips, with a
final `canItem`. But it leaves a hole: a permission changed through the Share dialog
invalidates `["effective"]` mid-session without busting the cell memo.

**Rejected:** a `key` on the DataTable derived from the permission map. Correct, but a
remount resets scroll position, and doing that on a query resolve jumps the grid under
someone who has scrolled.

**Chosen:** `cellMemo={false}`. Correct unconditionally, including the mid-session
permission change. The cost is that every cell re-renders on every parent render;
default page size is 25, so that is roughly 300 cheap `renderCell` calls rather than
the thousands the memo was designed for.

**This one is conditional on a measurement.** Render cost is checked on a full page
before the PR is opened. If it is visible, we say so and fall back to gating on the
permission query, accepting the narrower hole.

### #14 — one predicate per column

`bodyClassName`, `body` and `onBeforeCellEditShow` each call `canItem(row.uuid, "edit")`
for every cell of every row, and `kind` already encodes "locked". One helper defined
once per column drives all three:

```ts
const isEditable = (row: ListItem) => kind !== "locked" && canItem(row.uuid, "edit");
```

The point is not the saved lookups — `canItem` is a Map read — it is that the class,
the body and the edit gate become incapable of disagreeing about the same cell.

`cellEditKind`, a one-line `?? "locked"` wrapper used exactly once, goes with it; the
default folds back into the map read. `inlineEdit ? cellEditKind(editKinds, col.uuid) :
"locked"` currently states the same fallback twice.

These two land in the same PR on purpose: with the memo off, those three callbacks run
on every render, so having them share one predicate is worth slightly more than it was.

---

## Testing

| PR | Unit-tested | Verified by reading and by hand |
|---|---|---|
| 1 | `cellPatch`, `toStored`, `clearedNested` | the mutation's rollback and debounce |
| 2 | `textMode`, `needsFullEditor` | #5, #10, #11 |
| 3 | `resolveFields`, `cellEditKinds` | #8's render cost, #14's structure |

Every PR also runs `tsc --noEmit` and `vite build`, as #42 and #43 did.

Where a claim rests on PrimeReact's internals, it is checked against the installed
source and the file and line recorded in the commit message — the practice that caught
the `defaultPrevented` mistake in #10 before it was written.

## Out of scope

- **jsdom and component tests.** The DOM-shaped findings (#5, #8, #10, #11) would
  benefit, but the setup cost and PrimeReact's behaviour under jsdom are a separate
  decision.
- **Batching cell writes behind a "Save Changes" button.** Considered earlier and
  rejected: it fixes the self-race but not multi-user races, and introduces
  unsaved-client-state problems. The row lock in PR #42 addresses the actual defect.
- **The remaining `["items", listUuid]` invalidation call sites.** A dozen files spell
  the namespace literally. They are correct as written; converting them to a shared
  helper is churn unrelated to these findings.

## Risks

- **#8's render cost** is the only unknown that could change the design. Mitigation is
  stated above: measure, and fall back if visible.
- **#5 changes what a link cell does** while edit mode is on. This is deliberate and
  reversible by toggling the mode off, but it is a behaviour change a user could notice
  and should be called out in the PR body.
- **#4 now sends a write for a row that has scrolled or repaged away.** This is the
  intended fix — the user typed the value and committed it — but it means a commit can
  succeed against a record no longer visible on screen. The debounced refetch settles
  the display.
