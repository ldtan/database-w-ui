# Known gaps, gotchas, and follow-ups

Things that are deliberately unfinished, behave in a way that surprises people, or will
bite whoever touches the code next. Ordered by how much they matter.

---

## 1. There is no public registration

`POST /auth/users/` is **administrative** — superuser-only — and it is the only way to
create an account. The first account exists solely because `seed.py` seeds it into an
empty users table. So today a superuser onboards everyone by hand.

Adding public self-signup means a **new** unauthenticated route (`POST /auth/register`),
not loosening the admin one. It needs its own narrower schema: `UserCreate` extends
`UserBase`, so the caller can already set `is_active`, and the model now carries
`is_superuser` — a public route accepting those fields would let a stranger self-promote.
It would also want rate limiting and email verification. Note `UserService.create` already
auto-provisions a personal workspace for every new user, which is the right behavior for
signup.

## 2. A record shared without its list can't be viewed in the UI

The API supports this on purpose — item routes use `resolve_list_or_404` (existence only)
rather than `get_list_or_404` (which requires list `view`). Verified: a user with `edit`
on one record and *no* grant on the list gets 200 on that record and sees only it in the
list's records.

The UI can't render it. `ListDetailPage` is the only place a record appears, and on mount
it fetches `/lists/{uuid}` and `/lists/{uuid}/columns/`, both gated on list `view`. Both
403 and the page blanks.

The blocker is the **column schema**: records are JSONB keyed by column uuid, so a record
is unrenderable without the column definitions — and that endpoint requires exactly the
permission an item-only grantee lacks. Closing this needs a backend change either way:
let an item grant confer read access to the list's column schema (field names and types,
not its records). That is a deliberate widening, not a no-op.

On top of that, pick one:
- **Degrade `ListDetailPage`** when `/lists/{uuid}` 403s (smaller, one page to maintain).
- **A dedicated record page/permalink** (cleaner separation, a second page to maintain).

Both also need two fallbacks that are easy to miss: `RecordFormDialog` renders from a Form
(`/lists/{uuid}/forms/` is *also* gated on list `view`), and `LookupCell` resolves a value
by fetching a **different** list — so lookups must degrade to an unresolved value when the
target list isn't visible.

## 3. Grants are not scrubbed on hard delete

`permission_grants` points at resources polymorphically via `(resource_type, resource_id)`
with **no foreign key**, so nothing cleans up a grant when its resource goes away. This is
harmless today because resources only ever soft-delete and the resolver walks the live
hierarchy (a deleted resource resolves to no context, so its grants are inert). It becomes
a real problem the moment anything hard-deletes, or if ids are ever reused.

---

## Behavior that surprises people

**`is_superuser` and the permission model are orthogonal.** A superuser administers
*identities* — creating users, promoting them, editing group membership. It does **not**
grant access to anyone else's workspaces, lists, or records; that is entirely the grant
system's business. The reason identity administration has to be locked down at all is that
it sits *underneath* permissions: groups are principals in the grant table, so whoever can
edit membership can hand themselves any grant a group holds, and whoever can reset a
password can simply become the workspace owner. (Both were open to any authenticated user
until this was fixed; `tests/test_account_security.py` keeps them shut.)

**The last superuser cannot be deleted or demoted.** Superusers are only ever minted two
ways — seeded into an empty users table, or promoted by an existing superuser — so losing
the last one locks account administration out permanently, with no migration tool to undo
it by hand. `UserService._guard_last_superuser` refuses; promote a replacement first.

**Changing your own password requires `current_password`; a superuser resetting someone
else's does not.** The first stops a stolen access token becoming a permanent takeover.
The second is a deliberate admin capability — a superuser genuinely *can* take over any
account, which is inherent to the role, not a hole.

**The workspace owner is an implicit superuser** *of their own data* (unrelated to the
`is_superuser` flag above). `effective_actions` short-circuits to all
actions when `user.id == workspace.owner_id`, before any grant lookup. Owners cannot lock
themselves out of their own data no matter how the grants are arranged, and no grant can
*reduce* an owner's access. This is also why the whole pre-existing test suite stayed green
when permissions landed: `DISABLE_AUTH=True` resolves the caller to the seeded owner.

**"Unique" permissions are opt-out, not opt-in.** A record leaves the list's permission
umbrella only when someone flips its `permissions_inherit` to `false`. New records default
to inheriting, so a row added later to a list with carefully-locked-down records is
immediately visible to everyone holding list `view`. There is no "every new row in this
list starts unique" mode. If per-record access is meant to be a list's default posture,
that needs building.

**Breaking inheritance is reversible.** Flipping `permissions_inherit` back to `true`
re-joins the parent chain, and grants made while the child was independent are kept and
simply re-union with the inherited ones.

**`ListRead.workspace` exposes only a uuid, deliberately.** A list can be granted without
its workspace, so serializing the workspace's `display_name` would leak the name of a
workspace the caller was specifically not given access to. This is why the "Shared with me"
section shows the list's own slug as its subtitle rather than "in «Workspace»".

---

## Gotchas for whoever edits this next

**Alembic owns the schema — but autogenerate is blind to new enum values.** The app no
longer creates tables (`init_db` only *verifies*); run `alembic upgrade head` first. The
workflow is in the README. The trap:

> `alembic revision --autogenerate` diffs **tables and columns, not enum labels**. Adding
> a member to `ColumnType` produces a migration whose `upgrade()` is literally `pass`.
> This is exactly how `invalid input value for enum column_type: "time"` reached a running
> server. Verified: add an enum member and autogenerate emits nothing.

So an enum change must be hand-written, and `ALTER TYPE ... ADD VALUE` cannot run inside a
transaction that then uses the value — it needs Alembic's autocommit escape:

```python
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE column_type ADD VALUE IF NOT EXISTS 'time'")
```

Postgres has no `DROP VALUE`, so these are effectively one-way; a downgrade would have to
rebuild the type.

**The startup drift guard is the backstop for exactly that.**
`src/infrastructure/schema_check.py`, run from `init_db`, compares the models against the
live database and **refuses to start**, printing the SQL to run. It catches a forgotten
migration, an incomplete one, and the enum hole above. Drift is reported, never applied —
schema is not mutated behind your back, and with several workers that would race.

**`use_alter` foreign keys need hand-holding in migrations.**
`lists.primary_column_id → columns.id` is circular (`columns.list_id → lists.id`), so the
model marks it `use_alter=True`. SQLAlchemy omits such keys from `CREATE TABLE`, and
autogenerate does **not** emit the follow-up `ALTER` — so the generated baseline silently
had no such constraint until it was added by hand. `tests/test_migrations.py` pins it.

**Every model must be listed in `src/infrastructure/model_registry.py`.** That one module
is imported by `main.py`, `tests/conftest.py`, and `migrations/env.py`. A model missing
from it fails three different silent ways — worst of all in `env.py`, where autogenerate
sees the table as absent from the models and emits a **`DROP TABLE`**.

**Never lazy-load a relationship after a commit.** The session factory sets
`expire_on_commit=False` precisely because a lazy reload under asyncpg raises
`MissingGreenlet`. In particular, `PermissionService` never touches `user.groups` — it
queries `user_group_association` directly, because the `DISABLE_AUTH` user isn't loaded
with groups eager-loaded and the lazy load would blow up outside the greenlet.

**`PermissionService` is imported lazily inside functions, on purpose.** `permissions.services`
imports from `auth.users.services` → `workspaces.services`, so a module-level
`from src.permissions.services import ...` in `workspaces/services.py` or `lists/services.py`
creates an import cycle. The deferred function-local imports in `list_visible`, `ListService.list`,
`ListItemService.list`/`query`, `ViewService.list`, and `FormService.list` are load-bearing.

**The PrimeReact theme hardcodes its colors.** `lara-light-cyan/theme.css` defines
`--primary-color` / `--surface-*` and then never references them (zero `var(--primary-*)`
usages in the file), so overriding those variables recolors nothing. Theming works only
because `themes.css` is a custom, **unlayered** override sheet imported **last** in
`main.tsx` — unlayered rules beat `@layer`ed Tailwind utilities, and last-import wins on
source order. Reordering those imports silently breaks every theme.

**`frontend/src/lib/types.ts` is maintained by hand.** Generated API types were considered
and deferred, so backend schema changes do not propagate — update `types.ts` yourself.

**Permission-gated controls are hidden while their query is in flight.** `useEffectiveActions`
returns an empty set until the request resolves, so buttons appear a beat after page load
rather than showing and then vanishing (which would let someone click a control they don't
have). Grant and inheritance changes invalidate the cached `["effective"]` queries.

**A request must never hold two pooled connections at once.** Export streams rows on a
server-side cursor *and* resolves reference labels — the obvious way to write that is a second
session for the label queries, because "you can't query a session mid-stream". You can: the
label queries run **between** fetches from the cursor, never during one, and asyncpg serves
them on the same connection (`test_export_larger_than_a_batch_resolves_labels_mid_stream`
drives 1200 rows through it). Taking a second session instead would have each export hold two
connections for its whole duration, and enough concurrent exports — each holding one, each
waiting on a second — exhaust the pool and stall until `pool_timeout`. Reordering the
acquisitions does not help: a session grabs its connection at its *first query*, not at its
first stream.

**Every read path over records must apply `item_view_filter`.** Per-record permissions are
only as strong as the weakest path that returns records. Export used to miss this — it
streamed rows filtered on `list_id` alone, so a list-viewer could export records whose
inheritance was broken and were never granted to them, even though the grid hid them
correctly. Fixed, and `test_http_export_respects_item_permissions` guards it. Any *new*
endpoint that returns record data needs the same filter; a list-level `view` check is not
sufficient.

**A `user` cell must never resolve to a person's real name.** `/auth/users` is readable by
any authenticated user (the Share dialog needs to list principals), but person records are
private — owner or superuser only. So `user` columns label by **username**, in both
`PrincipalControls.tsx` and the export formatter. Swapping in "first + last name" because it
reads nicer would hand every viewer of a list the personal details of everyone referenced
from it.

**Every reference column exports its label, and the two sides must agree on what the label
is.** `lookup`, `user` and `group` all store a uuid and export the human-readable label
(row label / username / group name); import resolves it back. The load-bearing detail is
that export and import both call `display_column_key` to decide *which* column of the target
list supplies a row's label — if they ever picked differently, a file this app exported would
not import back into it. A label matching two rows is rejected rather than guessed (row
labels, unlike usernames, are not unique), and a row whose label is empty exports as its uuid,
because that is the only thing that identifies it. One consequence to know about: a
multi-valued cell is serialized to CSV as `a;b`, so a label containing `;` will not survive a
CSV round-trip (it does in JSON).

**Reference pickers search server-side; two stragglers still fetch `?limit=1000`.** Every
picker over an unbounded set has the same two jobs, and they need different answers:

- *Search* — a typeahead (`q=…`), because a dropdown listing every option silently stops
  offering the ones past its fetch limit.
- *Resolve* — turn the uuids a page already holds into labels, batched: `?uuid=…&uuid=…`
  (capped at 500), **one request per column per page, never one per cell**.

`PrincipalControls` (users/groups) and `LookupControls` (target rows) both do this now; the
lookup one goes through `GET /lists/{list}/columns/{column}/options`, which is server-side
precisely because only the server knows a row's label (`display_column_key`) — and it applies
`view` on the target list plus the per-record filter, so a picker can never offer a row its
user cannot read. Still to convert: `GroupDialog` (member picker) and `PeoplePage`
(person-name annotations).

**Resolving a label means reading the target list, so it obeys the *target's* permissions,
not the exported/edited list's.** `view` on a list must not hand over the contents of a
different list it happens to point at. The options endpoint enforces this, and so does export
(`_lookup_row_labels`) — a caller who cannot read the target gets uuids instead of labels,
which is what they saw before labels existed. Any new surface that resolves a lookup label
needs the same check; `test_export_does_not_leak_labels_from_an_unreadable_target` pins it.

**A row's label falls back: the lookup's `display_column_uuid`, else the target's primary
column, else its *first* column.** That last step is load-bearing — `primary_column_id` is
only ever set by an explicit PATCH and is nulled when that column is deleted, so most lists
have none. Drop it and every lookup at such a list silently degrades to uuid stubs *and* its
picker's search matches nothing.

Three traps worth knowing if you touch these hooks.

**A cell must subscribe to its own label query — never take a resolved map as a prop.**
PrimeReact's `DataTable` re-renders its body only when `value` (the rows) changes. Labels
arrive *after* that, so a map computed in the page and passed down freezes at whatever was
resolved when the rows last changed: assign a user to a record and the cell sits on `…`
forever, even though the page-level hook has the name. `PrincipalCell`/`LookupCell` therefore
take the page's *uuid list* and call the hook themselves. Every cell of a column passes the
same list, so react-query serves them all from one request — the batching survives.

**The query key is the uuid set**, so it changes whenever the records do — including the blip
where a view swap empties the grid mid-refetch. Without `placeholderData: keepPreviousData`
the labels drop out and every cell falls back to a stub.

**"Not resolved yet" must never render like "row/account is gone".** An in-flight uuid shows
`…`; only a settled request with no match shows `unknown user` / the row stub. Note this must
key off `isFetching`, **not** `isPending` — with `placeholderData` the status is already
`success` while the new uuid set is still in flight.

**Record search matches reference columns by label, and that means it obeys the *target's*
permissions.** A `lookup`/`user`/`group` cell stores a uuid while a person types the label, so
`?q=` resolves the label to uuids first and then matches cells with `?|` (true for a single
value and for one element of a multi-valued cell alike). Resolving a lookup's label is a read
of the target list, so it takes `view` on that list plus the per-record filter — otherwise
search becomes a way to probe the contents of a list you were never given. A caller without
that access simply gets no match on those columns, and
`test_search_by_lookup_label_obeys_the_target_lists_permissions` pins it. A label resolving to
more than `_SEARCH_REF_MAX` (200) rows stops widening there — a search matching thousands of
target rows is not a search anyone meant to run.

**A `link` value must never reach an `href` unchecked.** The backend only stores URLs whose
scheme is in the column's `allowed_schemes`, itself capped at `SAFE_LINK_SCHEMES`
(`http`, `https`, `mailto`, `tel`) — a column cannot opt back into `javascript:`. But rows
predating a config change, or an odd import, mean the frontend re-checks: build every anchor
through `safeHref` in `lib/links.ts`, which falls back to rendering plain text. Any *new*
surface that renders link cells needs the same call; a raw `href={value}` is stored XSS.

**`POST /permissions/effective` is capped at 500 resources per call** (`max_length` on
`EffectiveActionsQuery.resources`). A page showing more rows than that needs to chunk.
