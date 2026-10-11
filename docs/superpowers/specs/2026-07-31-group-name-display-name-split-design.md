# A group gets a slug `name` and a human `display_name`

**Date:** 2026-07-31
**Scope:** `UserGroup.name` becomes a `NameSlug` code identifier like the one every
table, column, view and form already carries, and a new `display_name` takes over the
job of labelling the group on screen.

---

## Why this exists

`UserGroup.name` is free text (`String(50)`, globally unique) doing three jobs at
once: it is the label the UI shows, the string search matches against, and — the
problem — the machine key two importers resolve against.

Two wire formats already depend on it:

- The batch-user CSV's `groups` column resolves rows by exact name
  (`auth/users/batch/services.py:resolve_groups`).
- Record `group` cells export the group's `name` as the cell value, and import
  resolves that value back to a uuid (`tables/transfer/services.py:_LabelResolver`).

So renaming a group for cosmetic reasons silently invalidates every saved roster CSV
and every record export anyone is holding. That is exactly the failure the
`name`/`display_name` split already prevents for tables — groups are simply the
principal that never got it.

The split gives the rename a place to land: `display_name` changes freely, `name` is
the contract.

## Decisions taken before designing

Four choices shaped everything below. The first two were confirmed with the user;
the second two follow from them.

1. **A record `group` cell exports the slug, and `display_name` carries no unique
   constraint.** These are one decision, because import resolves the exported label
   back to a uuid and so the label must be unambiguous. Exporting the slug keeps the
   round-trip honest without constraining `display_name`, which matches how tables
   already behave. It is also what the export format already does everywhere else:
   `_stream_csv` writes `[c.name for c in columns]` as the header row, so the file is
   slugs throughout rather than gaining an exception for one cell type.

   It is also what `user` cells already do — they export `username`, not the
   connected person's real name — so `group` cells exporting a slug puts the two
   principal types in the same shape instead of inventing a third.

   The cost is that two groups may share a display name, and the SharePanel picker is
   where that costs something real: picking the wrong group hands out access. So the
   picker and the grant rows render the slug beneath the display name as a
   disambiguator rather than the schema forbidding the collision.

2. **The batch-user CSV accepts slugs only, with an error that names the right one.**
   Existing roster files carrying `Authenticated Users` in the `groups` cell stop
   working. The alternative — falling back to `display_name` when a slug misses —
   would reintroduce precisely the ambiguity the split removes, and a compatibility
   path added for one cutover does not get removed later. Instead, a miss looks up
   the display name and suggests the slug:

   ```
   Row 4: Unknown group(s): Authenticated Users. Did you mean: authenticated_users?
   ```

3. **Search covers both columns.** `name` becoming a slug would otherwise silently
   break two search paths: the group listing's `q` (`UserGroupService._apply_search`)
   and record filtering on `group` cells (`TableRecordService._reference_matches`, which
   resolves what a person types to uuids). Someone typing "Registrar" must still find
   `registrar_office`, so both match `name` **or** `display_name`.

4. **In-app display uses `display_name`; the wire uses `name`.** Record cells,
   pickers, dialog headers, toasts and the bin all show the human label. Exports,
   imports and the reserved-system-group check use the slug. `PrincipalControls`
   labels `user` cells by `username` for a privacy reason that does not apply to
   groups, so groups take the display name there.

## What the migration has to do

Nothing stores a group *name* in JSONB, which bounds this sharply. Record `group`
cells store uuids, saved view filter conditions on principal columns store uuids
(`PrincipalPicker` hands back `row.uuid`), and permission grants reference
`group_id`. So there are no cell rewrites and no view-config rewrites — unlike
`a9c3f5e7d2b8`, which had to walk every record and every filter.

The table work, in order:

1. Add `display_name VARCHAR(255)` nullable.
2. Backfill `display_name = name`. Today's `name` is the human label, so this is a
   straight copy and it inherits the existing global uniqueness for free.
3. Widen `name` to `VARCHAR(63)` to fit `NameSlug`'s `max_length`.
4. Rewrite `name` to its slug, deduplicating collisions.
5. Set `display_name NOT NULL`.

**Slugification rules.** `NameSlug` is `^[a-z][a-z0-9_]*$`, max 63.

The rules are not invented here: `frontend/src/lib/slug.ts` already implements this
transform for every other resource's create dialog, and the backend copy mirrors it
so a group named in the browser and a group named by the migration slugify
identically.

- Lowercase; every run of non-alphanumeric characters becomes a single `_`; leading
  and trailing `_` are trimmed. `"Registrar's Office"` → `registrar_s_office`.
- A result that does not start with a lowercase letter is prefixed `x_`. `"7th
  Grade"` → `x_7th_grade`, and an empty result (a name that was all punctuation)
  becomes `record`. Both match `slug.ts`.
- Truncate to 63 before deduplication so the suffix cannot push it over.
- **Collisions are real** and must be handled: `"Grade-7 Teachers"` and `"Grade 7
  teachers"` are two distinct rows today that both slugify to `grade_7_teachers`.
  The second and later get `_2`, `_3`, … Ordering is by `id`, so the oldest group
  keeps the clean slug and the result is deterministic. Soft-deleted groups are
  included, because `name` is a plain unique constraint here rather than a partial
  index (see `bin.py` — a deleted group keeps its name reserved).

**The system group.** `AUTHENTICATED_USERS_NAME = "Authenticated Users"` is the seed
identity, the reserved-name check and the immutability message. It splits into two
constants — `authenticated_users` and `"Authenticated Users"` — and the migration
pins that row explicitly rather than letting the generic slugifier produce it, so the
resolver's grant-to-everyone group cannot drift on a rerun.

`downgrade()` restores `name` from `display_name` and drops the column. The slugs are
recoverable this way, unlike `a9c3f5e7d2b8`, so this one is not one-way.

## Components

**`auth/users/models.py`** — `name` becomes `String(63)`, `display_name String(255)`
is added, and the module constant splits in two.

**`auth/users/schemas.py`** — `UserGroupBase.name: NameSlug` plus
`display_name: str`; `UserGroupUpdate` gains an optional `display_name`.
`BinnedPrincipalRead` is unchanged in shape — the group branch of the bin route just
passes `row.display_name` where it passed `row.name`.

**`auth/users/services.py`** — `_apply_search` covers `display_name`; `create`
persists it and checks the reserved slug; `_guard_system_group`'s message uses the
display constant. The `ConflictError` text stays "Group name is already in use"
because `name` remains the only unique column.

**`auth/seed.py`** — seeds both fields.

**`auth/users/batch/services.py`** — `resolve_groups` keeps keying on `name`, now a
slug. A new lookup resolves misses against `display_name` to build the "did you mean"
suggestion, in one query for the whole file rather than one per bad row.

**`tables/records/services.py`** — `_reference_matches` matches a group by `name` or
`display_name`.

**`tables/transfer/services.py`** — no change. The label is `UserGroup.name`, which is
now the slug, which is what decision 1 wants.

**Frontend** — `GroupDialog` gains a display-name field and slug validation on the
name field; `SharePanel`, `PeoplePage` and `PrincipalControls` show the display name,
with `SharePanel` adding the slug as a secondary line; `BatchUserDialog`'s template
row and hint text move to slugs; `GuidePage` describes the pair. `api-schema.ts` is
regenerated via `npm run gen:api`.

**`demo/school.py` and `demo/_builders.py`** — `ensure_group` takes both fields. The
`GROUPS` dict is already keyed by a slug-shaped string, so the keys become the real
`name` and the tuple's first element becomes `display_name`; `GROUP_OF` moves to
those keys.

## Error handling

A `name` that is not a slug is a 422 from Pydantic, the same as for a table. A
duplicate `name` is the existing 409. An unknown group in a CSV row is the existing
per-row error, now carrying a suggestion when one exists; the row is skipped and the
rest of the file proceeds, unchanged from today.

The migration raises rather than guessing if a slug still collides after 999 dedupe
attempts — a condition that cannot occur with any plausible data, but which would
otherwise loop.

## Testing

- **Slugification is a pure function and gets unit tests directly**: apostrophes,
  leading digits, all-punctuation names, over-length truncation, and the collision
  sequence. This is where the migration's real risk lives, so it is tested away from
  Postgres.
- **Round-trip**: export a table with a `group` column, re-import the file, assert the
  cells resolve to the same uuids.
- **Search**: a group is findable by a fragment of either its slug or its display
  name, in both the group listing and a record filter.
- **Batch import**: a slug resolves; a display name does not, and the error names the
  slug.
- **System group**: the reserved slug is refused at create; the seeded row has both
  fields; the group stays immutable.
- Existing group tests move to the new field names.
