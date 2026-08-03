# Database with UI — Frontend

React + TypeScript SPA for the lists/tables app, talking to the FastAPI backend.

## Stack

| Concern | Choice |
|---------|--------|
| Framework | React 19 + TypeScript |
| Build/dev | Vite |
| UI + data grid | PrimeReact (DataTable, form controls) + PrimeIcons |
| Layout utilities | Tailwind CSS v4 (utilities only, no preflight) |
| Server state | TanStack Query |
| Routing | React Router |
| Forms | React Hook Form + Zod |
| Drag & drop | dnd-kit (for future column/field reordering) |
| QR scanning | html5-qrcode (camera, with an image fallback) |
| API types | `openapi-typescript` (generate from the backend's OpenAPI schema) |

## Getting started

```bash
npm install
npm run dev      # http://localhost:5173
```

The dev server proxies `/api` to the backend at `http://localhost:5000`, so run the
backend (see `../backend/README.md`) alongside it. Log in with the backend's seeded
user (`FIRST_SUPERUSER_USERNAME` / `FIRST_SUPERUSER_PASSWORD`).

Other scripts:

```bash
npm run build          # typecheck (tsc --noEmit) + production build
npm run preview        # serve the production build
npm run gen:api        # regenerate src/lib/api-schema.ts from the backend's OpenAPI document
npm run capture:guide  # re-photograph the guide's screenshots from the running app
```

## API types are generated

`src/lib/types.ts` re-exports from `src/lib/api-schema.ts`, which `npm run gen:api`
generates from the backend's own OpenAPI document — so the frontend's idea of a
`ColumnRead` *is* the Pydantic model. **Run it after any backend schema change**; whatever
the frontend now gets wrong stops compiling instead of failing at runtime.

It needs neither a running server nor a database: the script asks the app for its schema
directly (`src.infrastructure.export_openapi`), which is why refreshing types is one
command and not a ceremony.

Two things stay hand-written, on purpose:

- **The `config` blobs.** A column's/view's/form's `config` is a `dict` to the backend —
  its shape depends on the column type — so OpenAPI can only say "object". `ColumnConfig`,
  `ViewConfig` and `FormConfig` in `types.ts` say what the UI actually reads, and are the
  one thing here that can still drift.
- **`Response<T>`.** OpenAPI marks a field optional when its model gives it a default, but
  FastAPI serializes response models in full, so those fields are always present. The
  helper makes them required rather than littering the app with `?? false`.

## Structure

```
src/
  lib/            # api client (fetch + auth/refresh), auth + theme + toast contexts,
                  # themeSync (ties the theme to the account), query client, hand-written
                  # API types, and small shared helpers: links (safeHref), temporal, slug,
                  # people, useMediaQuery, useDebounced
  components/     # app shell: AppLayout, AppSidebar (the rail — beside the page above
                  # 1024px, inside a drawer below it), NavTree (what it renders),
                  # AccountMenu + UserAvatar (the account control), AppDialog,
                  # NameFields, Breadcrumbs, ThemeSwitcher
                  # page furniture: PageHeader, EmptyState, Skeleton, SearchInput,
                  # CreateNameDialog, RowActions
  features/
    auth/         # login (the only unauthenticated page)
    workspaces/   # workspaces + the lists shared with you directly
    lists/        # lists of a workspace; a list's records; the List settings dialog;
                  # DeletedConfigDialog, the bin its four config kinds share
    columns/      # add/edit a column, and the columns panel in List settings
    records/      # the record form, the mobile card layout, renderCell, sortRows,
                  # and the QR scanner (QrScanDialog + qrFill)
    views/        # the view bar, filter/group/column controls, filter conditions
    forms/        # per-list form layouts
    metrics/    # the aggregate cards above the grid, and their settings panel
    permissions/  # Share dialog/panel and the effective-actions hooks
    people/       # accounts and groups (identity administration), their bin,
                  # and batch account creation from a CSV
    profile/      # your own account and profile
    transfer/     # CSV/JSON import + export, for a list's records
    lookups/      # lookup cells, fields and filter inputs
    principals/   # user/group cells, fields and filter inputs
    references/   # ReferencePicker — the typeahead all of the above share
```

A column's `type` and `config` drive everything about it: `renderCell` picks how it
displays, `RecordFormDialog` picks its input, `FilterConditionsEditor` picks its filter
control. Adding a column type means teaching those three, not writing per-list code.

## The guide's screenshots are taken from the running app

`features/guide/GuidePage.tsx` is a written walkthrough of the whole app, and the pictures
in it are real: `scripts/capture-guide.mjs` drives a Playwright browser through the app and
writes one PNG per screen into `public/guide/`, twice — once light, once dark, since the
guide is read in whichever mode the account chose. `<Figure name="…">` picks between them
and opens the full-size copy on click.

```bash
cd ../backend && uv run python -m src.demo.guide   # the Sales/Contacts tenant it photographs
npm run dev                                        # with the backend on :5000
npm run capture:guide                              # or `… capture:guide records filter` for two
```

Two things follow from it that are worth knowing:

- **The captions live in `features/guide/figures.ts`, which is generated.** The capture
  script writes it from the same list that took the pictures, so a caption cannot come
  loose from its image. Edit `SHOTS` in the script, not that file.
- **It is the only check on prose the guide has.** Each shot clicks controls by the labels
  the guide's own sentences quote, so a renamed button fails the run instead of leaving the
  page confidently describing something that is gone. Re-run it after any UI change —
  `docs/known-gaps.md` has the longer version of why.

The images and the manifest are committed: the guide has to render for anyone who checks
the repo out, and rebuilding them needs a database.

## Adding a record from a QR code

"Add record" is a `SplitButton`: the button adds a record by hand, and its menu offers
**Fill using QR code** and **Submit using QR code**. Both read one QR code holding a JSON
object, keyed by **column name** — the same key a CSV/JSON import uses, so one convention
covers every way a record arrives from outside the app, and a code stays writable by a
person (a column uuid works as a key too, for a code a machine generated):

```json
{ "full_name": "Ana Cruz", "age": 30, "status": "Active", "owner": "admin" }
```

- **Fill** reads one code and opens the Add-record form with those values in it, for the
  user to check.
- **Submit** opens a *scanning session*: see below.

### The submitting session

Submitting exists for attendance — a queue of people, each holding a badge — and that shape
drives everything about it (`QrSubmitSession.tsx`).

A badge's code says *who* someone is. It cannot say *which event they are attending*: the
badge was printed before the event existed and will be used at the next one too. So a
session asks first for the values that hold for every scan, using the same fields the
Add-record form would show, and then scans. Each code supplies only what is particular to
it, over the top of those presets.

**A code outranks a preset.** Where both have a value for a field, the scanned one wins —
presets are what to assume in the absence of an answer, and a code carrying one has
answered. This falls out of the merge order rather than being enforced anywhere, because
`fillFromQr` already drops the keys a code left blank: a code cannot erase a preset by
carrying `""`. A code that sets a *parent* lookup clears any preset chosen under the old
parent, exactly as editing that parent in the form would.

**The scanner stays open**, and a rejected scan does not close it. The one-at-a-time flow
can afford to drop you into the record form to fix a rejection by hand; a queue cannot,
because there are people waiting behind the bad code. The server's message appears under
the preview and in the session's log, the camera stays live, and the fix is to scan again.
A running tally and the last few scans are the feedback — **not** a toast per record, which
at a scan every few seconds would be a wall of them over the camera.

**The same code is ignored for five seconds.** A camera reports a code ten times a second
for as long as it stays in frame, so without this one badge held up would submit a dozen
records; the first sighting counts and the rest of the visit is dropped. The window
expires rather than latching, because the same person scanning in again later is a real
second record.

A rejected scan is suppressed by that window like any other, and **the one-shot re-arm on
error must not be extended to continuous mode**: the code that failed is still in front of
the camera, so forgetting it would resubmit it ten times a second for as long as it stayed
there. Waiting the window out costs nothing, either — a code rejected for what it contains
will be rejected again on sight.

Presets live on `ListDetailPage`, not in the session, so closing the scanner and reopening
it keeps the event you are taking attendance for. They are not persisted further: this is
the answer to "what is happening right now", not a setting.

**`qrFill.ts` does not validate, deliberately.** The backend validates every value on write
(`src/lists/columns/validation.py`) and is the only thing that does — restating "what a
link's scheme may be" or "what this choice column's options are" in the browser would be
two rulebooks to keep in step, and the frontend's is the one that would silently fall
behind. A bad value is therefore submitted and comes back as the server's error, which is
better worded than anything the client would have invented.

What `qrFill` *does* do is translate JSON's types into the form's, which is a shape and not
a rule: `"30"` into a number for the `InputNumber`, `"yes"` into a boolean for the
`Checkbox`, an ISO string into the `Date` a `Calendar` holds (via the same `deserialize`
the form uses for a stored value), and a person's name into the uuid a reference cell
stores, so its picker can render it. The first two mirror the backend's *importer*
(`_parse_cell`), which is where a QR's laxness comes from; the last is only for display,
since the item service resolves reference names on write by itself. **Every translation
falls back to the raw value** rather than dropping it — an unresolvable name is still
submitted, because the server resolves names too and will say precisely why it cannot.

The one thing a code is refused is a field this form does not have: it cannot reach a
column the **create form** excludes, or write one it marks read-only. That is a fact about
the form, not a judgement about a value.

A **choice** column is written by the option itself — the same label the UI shows
("In progress") — since a choice cell stores the label and the backend validates by
exact membership. A QR can therefore be authored straight off the screen.

Scanning uses `html5-qrcode`, with **an image fallback that is not a nicety**:
`getUserMedia` needs a camera and a secure context (HTTPS, or localhost), so on a desktop
— or on a LAN deployment served over plain HTTP — the camera path does not exist, and a
scanner that only scanned would be a dead button. Both paths end in the same place: the
code's text, handed to `qrFill`.

`QrScanDialog` is **lazy-loaded** (`React.lazy` + `Suspense` in `ListDetailPage`): the
decoder is ~110 kB gzipped, a quarter of the whole bundle, and most sessions never open
the scanner. Import it that way, not directly — and note that `QrSubmitSession` renders it,
so that must be lazy too or the decoder returns to the main bundle by the back door.
`qrFill` is small and pure, and stays in the main bundle.

The record inputs themselves are in `RecordFields.tsx` — `RecordField` (one editor, chosen
by column type) and `RecordFields` (all of a form's fields, labelled, clearing nested
lookups when their parent changes). They live apart from `RecordFormDialog` because two
surfaces now edit a record's fields: the Add/Edit dialog and a session's presets.

## The shell

`AppLayout` is a navigation rail and the page beside it.

The rail is `AppSidebar`: the wordmark, the tree, and — in its footer — the account
and the theme controls. On a desktop (`DESKTOP_SCREEN`, 1024px) it sits beside the
page, and there is no header bar, because a strip repeating what the rail already
carries would be chrome saying nothing. Below that width the *same component* is
mounted inside a `<Sidebar>` overlay behind a slim header's burger, with `enabled`
holding its fetches until the panel is opened and `onNavigate` closing it behind a
tap.

There used to be a second component for the narrow case. It carried the same tree in
a different shell, which meant anything added to the rail's own bands silently did
not exist on a phone — so the header's account and theme controls moved into the
rail's footer (a 17rem drawer leaves the header visible beside it, and a second copy
of each would have sat next to the original), and the drawer's profile band and Log
out row folded into `AccountMenu`, whose menu already carried the same three lines
and both actions.

It renders **one** component, `components/NavTree.tsx` — the app's sections, your
workspaces with their lists (fetched lazily, per expanded workspace), and the lists
shared with you. Two copies would be two ideas of what the app contains, and they
would drift the first time a section was added to one of them.

## Two breakpoints, and what each decides

They answer different questions, so they are different numbers — both in
`lib/useMediaQuery.ts`:

| Constant | Width | Question |
|----------|-------|----------|
| `SMALL_SCREEN` | ≤ 640px | Does a data grid fit? |
| `DESKTOP_SCREEN` | ≥ 1024px | Does a permanent 17rem rail fit beside it? |

A tablet answers yes to the first and no to the second, which is why one number
could not serve both.

Below `SMALL_SCREEN`:

- **Records become cards** (`features/records/RecordCards.tsx`) instead of a `DataTable`.
  Twelve columns across a phone is twelve slivers; a card is one record, label beside
  value. It is a second *layout*, not a second implementation — both render values with
  `renderCell` and order them with `sortRows`, and they share selection, page size and
  row actions. A value or an order that differed between them is a bug.
- **The toolbar's buttons lose their labels** (CSS, in `themes.css`) and stand as
  icons. The label stays in the DOM, so it is still what a screen reader announces.
- **Dialogs go near-fullscreen.** Every dialog is `components/AppDialog.tsx`, which
  applies the breakpoints and caps the body height so its buttons stay reachable —
  import it, not `primereact/dialog`.

## Dialogs and forms

A dialog is three bands with rules between them: a header saying what it is, a
scrolling body, and a **footer** carrying the buttons. Pass those buttons as
`footer`, never as the last row of the children — the body is a scrollport, so an
action row inside it scrolls away, which is how the column dialog ended up with
Save underneath thirty fields.

Inside the body, forms are built from `components/Field.tsx`:

| Component | What it is |
|-----------|------------|
| `Field` | one labelled control, with an optional hint under it and an "Optional" flag on the label |
| `FieldRow` | two or three short fields side by side, stacking when they don't fit |
| `CheckboxField` | a checkbox whose label is part of its hit area, with a hint |
| `FormSection` | a titled group, separated by a hairline |

The point of `FormSection` is hierarchy: before it, a section heading and a field
label were both `text-sm font-medium`, so a dialog of thirty fields had thirty
identically-weighted rows and no shape to skim. A dialog should read as two or
three things to decide.

Two dialogs are **tabbed** rather than stacked, for the same reason: list settings
(columns / permissions / views / forms) and import-export. Getting data out and
putting data in are different jobs, and only one of them is ever why you opened
the dialog. A tabbed dialog passes `contentStyle={{ padding: 0 }}` so the tab
strip runs the full width under the header rule.

Above it the grid still has to survive a narrow window: its table carries a minimum
width of ~8rem per column so it *scrolls* rather than crushing them. That scrolling
is `.p-datatable > .p-datatable-wrapper` in `themes.css` — PrimeReact ships no
overflow rule for that element, so before it was added the extra columns simply
overflowed the page and `body { overflow-x: hidden }` clipped them away.

## The records toolbar

Everything that changes *what you are looking at* is on one bar; everything that
changes *what the list is* is behind Settings or its overflow. The bar's controls are
buttons rather than dropdowns sitting at their placeholder text, and each says whether
it is currently doing anything — "Filter" against "Filter ②", "Group" against "Grouped
by Status", "Sort" against "Score ↑". A control holding a non-default setting takes the
`is-on` class, which tints it.

**"Save view" appears only when there is something to save.** `ListDetailPage`
fingerprints the page's live config against the active view's stored one (defaults
applied to both sides, since a view saved before `group_by` existed has no key for it)
and passes `dirty` to `ViewBar`.

Run `npm run gen:api` to keep the TypeScript types in sync with the Python models
instead of the hand-written `src/lib/types.ts`.

## Bins

Nothing in this app is deleted outright — a delete sets `deleted_at`, and a nightly job
destroys the row once it is past the retention window. Every kind that behaves that way
has a bin, and each bin sits **beside the thing it holds** rather than on one "deleted
things" screen: you go looking for a missing view where the views are listed.

| Component | Where it is offered | Gated on |
|-----------|--------------------|----------|
| `DeletedRecordsDialog` | the list page's overflow menu | list-level `delete` |
| `DeletedListsDialog` | the lists page | the workspace's `delete` |
| `DeletedWorkspacesDialog` | the workspaces page | ownership (the backend scopes it) |
| `DeletedConfigDialog` | each tab of List settings | list-level `configure` |
| `DeletedPrincipalsDialog` | the People page, per section | superuser |

The last two are each **one component covering several kinds**, because the API behind
them is one module behind several prefixes: same row shape, same three routes
(`/deleted`, `/{uuid}/restore`, `/{uuid}/permanent`), same gate. What differs per kind is
wording and which queries a restore invalidates, so that is a lookup table at the top of
the file rather than four near-identical components to keep in step.

Two things are worth copying if a sixth kind ever gets one:

- **The bin's query key is nested under its subject's live key** — `["views", listUuid,
  "deleted", first, rows]`. Every `invalidateQueries({ queryKey: ["views", listUuid] })`
  already in the app therefore refreshes the bin by prefix match, so deleting something
  leaves the bin correct without the delete having to know a bin exists.
- **A restore can legitimately fail, and the message matters.** A name is unique only
  among live rows, so something created meanwhile may hold it; and destroying an account
  is refused while it still owns workspaces. Both come back as the backend's own text
  through the error toast — the 409 that names the workspaces *is* the useful part.

## Notes

- Auth: access + refresh tokens are stored in `localStorage`; the API client attaches the
  bearer token and refreshes once on a 401. (For a hardening pass, consider moving the
  access token to memory.)
- Tailwind is imported without its preflight reset so it doesn't fight PrimeReact's
  component styles; use it for layout utilities only.
- Colors come from the theme, never from Tailwind: `themes.css` defines every palette
  against `data-theme` / `data-mode` on `<html>`, so reach for `var(--brand)`,
  `var(--text-muted)`, `var(--border)` — a `text-blue-500` will not follow the theme.
- **So do depth, roundness, motion and type.** A palette sets `--radius` and
  `--shadow-hsl`; the derived block builds `--radius-sm/-lg/-pill` and
  `--shadow-xs/-sm/-md/-lg` from them, plus `--ease`, `--dur`, `--transition` and
  `--font-family`. Themes that reject the defaults override the derived tokens
  *after* that block — brutalist does, swapping soft light for a hard offset in the
  border colour and setting a monospace heading face — because `[data-theme=…]` and
  `:root` carry the same specificity and the later one wins.
- **The shapes every page repeats are classes, not inline styles.** `.surface-card`,
  `.card-grid`, `.toolbar`, `.empty-state`, `.skeleton`, `.badge`, `.entity-icon`,
  `.selection-bar` and the `.nav-*` rows live in the "App patterns" block at the end
  of `themes.css`. They were `style={{ background: "var(--surface)", border: … }}`
  objects copied into four files; reach for the class so "what a panel looks like"
  stays one decision.
- The base PrimeReact theme already ships **Inter var** and its `@font-face`, so
  `--font-family` names it at no download cost. Do not add a web font.
- The theme is stored **against the account** (`users.settings`), so it follows the user
  between devices. `localStorage` is still written, but only as this device's cache: the
  inline script in `index.html` needs something synchronous to read before first paint,
  and a fetched value cannot arrive that early. `lib/themeSync.tsx` reconciles the two —
  the account's copy is applied once on sign-in, and every later change here is pushed
  up. It lives outside `ThemeProvider` because that provider wraps `AuthProvider` and so
  cannot ask who is signed in.
- Server state is TanStack Query with **shared keys** (`["items", listUuid]`,
  `["columns", listUuid]`, `["person", "me"]`…). Two components asking the same question
  make one request, and a dialog's `invalidateQueries` refreshes the page behind it.
  Retries and refetch-on-focus are off on purpose — see `lib/queryClient.ts`.
- A `link` cell's URL must go through `safeHref` (`lib/links.ts`) before it reaches an
  `href`. The backend already restricts schemes, but rows predating that check, or
  arriving by import, mean a raw `href={value}` is stored XSS.
- **Next step for type safety: a typed client.** The *schemas* are now generated (above),
  but the *endpoints* are not: `api.get<ListItem[]>(\`/lists/${uuid}/items/\`)` is a string
  and a type argument the caller supplies, so a wrong path or a stale response type still
  compiles. Adopting `openapi-fetch` over `api-schema.ts` would check the path, its
  parameters and its response against the OpenAPI document at compile time. It touches
  every call site, which is why it has not been done yet.
