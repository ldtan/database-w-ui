"""Run a column type conversion: preview it, then apply it in one transaction.

Two steps, and the first one **writes nothing**. `preview` converts every record's
cell in memory and reports what would happen; `apply` does the whole job — read,
convert, write, retype — inside a single transaction. Between them the list is
untouched, because there is nothing between them: no scratch column, no in-flight
state, nothing to abandon.

**Why it is shaped this way, and what it replaces.** This used to stage the work:
`start` built a *hidden* target column, backfilled it, and returned a report for a
person to read; `commit` then swapped the two columns' values and retyped the
source. The staging column was invisible to every writer by design — `in_shape()`
excludes it — which meant a record created or edited while the report sat on screen
got its value written under the *source* key and nothing under the target's. The
swap then moved that fresh value into the column being binned and wrote JSON null
over the live one. A record edited in the window was reverted to whatever the
backfill had computed before the edit. Neither was reported; the conversion
announced success.

The gap was human-scale, because a person was reading a report inside it. So the
gap is gone rather than defended: nothing is written until the moment everything is
written, and that moment holds a lock and takes a few seconds at the row ceiling.
Airtable can retype a field with no such window because a base has one in-memory
writer and the change is one more action in its ordered log; SharePoint can because
a field is a physical typed column and the remap is one statement, refused outright
on lists past its threshold. Neither of those is available here — this is stateless
FastAPI over uuid-keyed JSONB — but the property both of them get is, and this is it.

**The preview is deliberately not a commitment.** It is a dry run over live data,
so its counts can be stale by the time somebody presses the button. That is fine
and is the point: `apply` re-derives everything from the records as they are, and
refuses outright if they moved under it (see `_fingerprint`). A report that was
binding would be a staging column by another name.

**The old values survive in the bin.** A conversion writes the converted value under
the column's own key and the original under a second, already-binned column named
`<name>__before_conversion`, carrying the old type and the old config. Restoring it
within the retention window is a real undo. That is what the key swap used to buy,
bought here by writing two keys instead of exchanging them — which also means a key
that was absent stays absent, where the swap turned it into an explicit JSON null.

**The cutover keeps the column's identity.** The converted values go under the
source column's *own* uuid and the source row is retyped in place — same uuid, same
name, same position — so views, forms, formulas and `primary_column_id` keep
pointing exactly where they pointed. Only the values move, and only into a column
nobody was reading.

**A duplicate is the same code with a different ending.** Given a name, the pass
writes the converted values under a *new* visible column's key and leaves the source
completely alone: no backup column, no retype. It gets the same atomicity for free,
which the staged design did not give it — a duplicate published a copy that was
missing exactly the edits made while its report was on screen.
"""
import json
from datetime import datetime, timezone
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import (
    ConflictError,
    UnknownReferenceError,
    ValidationError,
)

from ..events.broker import broker
from ..items.models import ListItem
from ..models import List
from .conversion import CellConverter, check_supported, mint_choice_options
from .enums import ColumnType
from .models import Column
from .resanitize import resanitize_column
from .richtext import is_rich
from .schemas import ColumnConfig
from .services import ColumnService
from .validation import resolve_choice_options

# Records read, converted and written per statement batch. Matches the import's
# batch rather than the reconcile's 5,000: every row here costs Python work and
# possibly a reference lookup. Unlike before, these no longer commit individually —
# the whole apply is one transaction — so this bounds memory and statement size
# rather than lock duration.
CHUNK = 500

# Above this many records a conversion is refused. It was always a hard cap rather
# than a routing decision (nothing ever called the service from a maintenance
# workflow), and it matters more now: the apply holds row locks on the list until it
# commits, so this is also the bound on how long writers to this list can be blocked.
# Free Postgres is 1GB, so it remains well beyond any list that fits.
ROW_CEILING = 50_000

# Failures named individually in the report. Past this the count still climbs, but
# a person reading 5,000 identical messages has already learned what they need.
MAX_REPORTED_ERRORS = 100

# Namespace for the per-list advisory lock that admits one conversion at a time.
# Distinct from `_UNIQUE_LOCK_NS` (items) and `RECOMPUTE_LOCK_NS` (backfill) so the
# three never collide on a shared list id and block each other for unrelated reasons.
CONVERT_LOCK_NS = 0x434F4E56  # "CONV"

_BACKUP_SUFFIX = "__before_conversion"

# The three writes a row can need, kept as SQL rather than ORM updates for one
# load-bearing reason: `updated_at` is a Python-side `onupdate`, so a Core/ORM update
# would stamp every row the conversion touches and make `_fingerprint` below report
# the conversion's own work as a concurrent write. Raw SQL leaves it alone.
_SET_BOTH = sa.text(
    "UPDATE list_items SET data = "
    "  jsonb_set(jsonb_set(data, ARRAY[:bkey], CAST(:old AS jsonb)), "
    "            ARRAY[:key], CAST(:new AS jsonb)) "
    "WHERE id = :row_id"
)
# A cell that would not convert: the live key goes away (the report said it would be
# left empty) and the original is preserved under the backup column.
_MOVE_ONLY = sa.text(
    "UPDATE list_items SET data = "
    "  jsonb_set(data - :key, ARRAY[:bkey], CAST(:old AS jsonb)) "
    "WHERE id = :row_id"
)
# A duplicate: only the new column's key is written, and the source is not touched.
_SET_ONE = sa.text(
    "UPDATE list_items SET data = "
    "  jsonb_set(data, ARRAY[:key], CAST(:new AS jsonb)) "
    "WHERE id = :row_id"
)


class ConversionService:
    # -- shared -----------------------------------------------------------

    @staticmethod
    async def _row_count(db: AsyncSession, list_id: int) -> int:
        # Soft-deleted and archived records included, for the reason
        # `reconcile_shapes` includes them: a restore or an unarchive must not bring
        # back a value in the old column's type.
        return await db.scalar(
            select(sa.func.count())
            .select_from(ListItem)
            .where(ListItem.list_id == list_id)
        )

    @staticmethod
    async def _fingerprint(db: AsyncSession, list_id: int) -> tuple[int, Optional[datetime]]:
        """Enough of the list's state to tell whether anyone else wrote to it.

        `(count, max(updated_at))` rather than a timestamp comparison, because it
        needs no agreement between the app's clock and the database's and it catches
        both shapes of interference: an insert moves the count, and any update moves
        the maximum. Deletes and archives move both — they are ordinary updates that
        stamp `updated_at`.

        Read once before the pass and once after, inside the same transaction. Under
        READ COMMITTED a later statement sees a newer snapshot, so the second read
        genuinely observes anything committed while the pass was running.

        Three things it does not see, none of them currently reachable in a way that
        loses data, all of them worth knowing before relying on this:

        - It closes at the second read, not at `COMMIT`. A row inserted in that last
          sliver is missed, and would then hold a value validated against the old type
          under a column that has just been retyped. Same insert race `resanitize.py`
          documents.
        - It covers `list_items` only. A concurrent `PATCH` of the source column's own
          config is neither detected nor preserved — `apply` overwrites it.
        - It is blind to every other raw-SQL writer of `data`, since none of them
          stamps `updated_at` either: `backfill`, `resanitize`, `reconcile_shapes`,
          `_references`, `purge`, and the item service's cell patch. A calculated
          re-index is the live one — its advisory lock is deliberately a different
          namespace, so it can run on this list at the same time. That is safe only
          because both sides merge inside SQL against the current row version under
          row locks. A future writer that read a record into Python and wrote the
          whole blob back would lose this conversion's work silently.
        """
        row = (
            await db.execute(
                select(func.count(), func.max(ListItem.updated_at)).where(
                    ListItem.list_id == list_id
                )
            )
        ).one()
        return (row[0], row[1])

    @classmethod
    async def _resolve_target(
        cls,
        db: AsyncSession,
        lst: List,
        source: Column,
        target_type: ColumnType,
        config: ColumnConfig,
        user,
        name: Optional[str] = None,
    ) -> dict:
        """Check the pair is convertible and return the config to store.

        Shared by `preview` and `apply` so the two cannot disagree about what is
        being built. The minting below is why the report echoes the resolved config
        back and the apply sends it again: `text` -> `choice` with no options given
        mints one per distinct value in the *source data*, so re-deriving it at apply
        time would silently answer a different question if a record changed. Once
        `options` is populated the mint is skipped, which makes the apply a pure
        function of what the person actually saw.
        """
        probe = Column(type=target_type, name=source.name, config={})
        check_supported(source, probe)

        if name is not None:
            # A duplicate's name, checked up front so the preview reports the clash
            # rather than letting somebody read a report and only then discover the
            # column cannot be created. This is a courtesy and not the guard: the
            # unique index is, via the IntegrityError the apply catches, because
            # nothing here can stop the name being taken in between. Under the old
            # design the name was *reserved* by the staging column from the moment
            # the report was built, which is why abandoning had to release it.
            clash = await db.scalar(
                select(func.count())
                .select_from(Column)
                .where(
                    Column.list_id == lst.id,
                    Column.name == name,
                    Column.deleted_at.is_(None),
                )
            )
            if clash:
                raise ConflictError(
                    f"A column named '{name}' already exists on this list"
                )

        rows = await cls._row_count(db, lst.id)
        if rows > ROW_CEILING:
            raise ValidationError(
                f"This list has {rows:,} records, more than the {ROW_CEILING:,} a "
                "conversion will run through in one transaction."
            )

        supplied = config.model_dump(mode="json", exclude_unset=True)
        if target_type == ColumnType.choice and source.type == ColumnType.text:
            # Either spelling counts as "the caller said what the choices are" —
            # `choices` is the shorthand the column dialog sends, and treating only
            # `options` as supplied would mint over the top of what they typed.
            supplied["options"] = await mint_choice_options(
                db,
                lst.id,
                source,
                supplied.get("options") or resolve_choice_options(supplied, None),
            )
            supplied.pop("choices", None)

        return await ColumnService._validate_config(
            db, lst, target_type, ColumnConfig(**supplied), user
        )

    @classmethod
    async def _run(
        cls,
        db: AsyncSession,
        lst: List,
        source: Column,
        target: Column,
        user,
        keys: Optional[tuple[str, Optional[str]]],
    ) -> dict:
        """One pass over every record, converting the source cell.

        ``keys`` is ``None`` for a dry run — nothing is written and only the counts
        come back. Otherwise it is ``(live_key, backup_key)``: the key the converted
        value lands under, and the key the original is preserved under (``None`` for
        a duplicate, which displaces nothing and so has nothing to preserve).

        Iterated by primary key in chunks so a large list does not become one
        enormous result set in Python. For the writing pass every chunk is part of
        the caller's transaction — nothing commits here, deliberately: a partially
        converted column is the state the whole design exists to make impossible.
        """
        converter = CellConverter(db, lst, source, target, user)
        await converter.prepare()

        skey = str(source.uuid)
        live_key, backup_key = keys if keys else (None, None)
        converted = 0
        failed = 0
        errors: list[dict] = []
        after = 0

        while True:
            rows = (
                await db.execute(
                    select(ListItem.id, ListItem.uuid, ListItem.data)
                    .where(ListItem.list_id == lst.id, ListItem.id > after)
                    .order_by(ListItem.id)
                    .limit(CHUNK)
                )
            ).all()
            if not rows:
                break
            after = rows[-1].id

            # One round trip for every label this chunk needs, rather than one per
            # cell — the same reason the export resolves in batches.
            await converter.load([row.data or {} for row in rows])

            both: list[dict] = []
            moved: list[dict] = []
            one: list[dict] = []
            for row in rows:
                value = (row.data or {}).get(skey)
                if value is None:
                    # Nothing to carry over, and nothing to preserve. No key is
                    # written under either column, which every reader already treats
                    # as empty — where the old swap turned an absent key into an
                    # explicit JSON null on both sides.
                    continue
                try:
                    new_value = await converter.convert(value)
                except (ValidationError, UnknownReferenceError) as exc:
                    # `UnknownReferenceError` too, and it is not a nicety: a text
                    # cell naming a user or a row that does not exist is the single
                    # most likely failure of a text -> reference conversion, and it
                    # has to cost that cell rather than the whole run.
                    failed += 1
                    if len(errors) < MAX_REPORTED_ERRORS:
                        errors.append(
                            {"record_uuid": str(row.uuid), "message": str(exc)}
                        )
                    if keys and backup_key is not None:
                        # The report promised this cell would be left empty. Clear
                        # the live key and keep the original in the bin column, so
                        # "left empty" never means "thrown away".
                        moved.append(
                            {
                                "row_id": row.id,
                                "key": live_key,
                                "bkey": backup_key,
                                "old": json.dumps(value),
                            }
                        )
                    continue
                converted += 1
                if not keys:
                    continue
                if backup_key is None:
                    one.append(
                        {
                            "row_id": row.id,
                            "key": live_key,
                            "new": json.dumps(new_value),
                        }
                    )
                else:
                    both.append(
                        {
                            "row_id": row.id,
                            "key": live_key,
                            "bkey": backup_key,
                            "new": json.dumps(new_value),
                            "old": json.dumps(value),
                        }
                    )

            for statement, params in (
                (_SET_BOTH, both),
                (_MOVE_ONLY, moved),
                (_SET_ONE, one),
            ):
                if params:
                    await db.execute(statement, params)

        return {
            "converted": converted,
            "failed": failed,
            "errors": errors,
            "errors_truncated": failed > len(errors),
        }

    # -- 1. preview -------------------------------------------------------

    @classmethod
    async def preview(
        cls,
        db: AsyncSession,
        lst: List,
        source: Column,
        target_type: ColumnType,
        config: ColumnConfig,
        user,
        name: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> dict:
        """Convert every cell in memory and report what would happen.

        Writes nothing and creates nothing, so there is no state to clean up if the
        person closes the dialog, no 409 waiting for their next attempt, and no
        window in which the list is half-committed to anything.

        The resolved config comes back in the report so `apply` can be handed the
        same one rather than re-deriving it — see `_resolve_target`.
        """
        duplicate = name is not None
        stored = await cls._resolve_target(
            db, lst, source, target_type, config, user, name=name
        )

        # A stand-in for the column that would be built. It is never added to the
        # session: the converter only reads its `type` and `config` to decide how a
        # cell converts, and a dry run must not leave a row behind.
        probe = Column(
            list_id=lst.id,
            name=name or source.name,
            display_name=display_name or name or source.display_name,
            type=target_type,
            config=stored,
        )
        report = await cls._run(db, lst, source, probe, user, keys=None)
        report["new_type"] = target_type.value
        report["duplicate"] = duplicate
        report["config"] = stored
        report["name"] = name
        report["display_name"] = display_name
        return report

    # -- 2. apply ---------------------------------------------------------

    @classmethod
    async def apply(
        cls,
        db: AsyncSession,
        lst: List,
        source: Column,
        target_type: ColumnType,
        config: ColumnConfig,
        user,
        name: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> Column:
        """Do the whole conversion in one transaction. The point of no return.

        Everything below happens or none of it does, which is the entire reason this
        is not two calls. Ordering matters in two places and is commented where it
        does.
        """
        duplicate = name is not None

        # One conversion per list at a time. `try` rather than a blocking wait: the
        # caller is a web request, and a second person arriving mid-run should be
        # told so rather than left holding a connection until the first finishes.
        # Transaction-scoped, so it is released by the commit or the rollback below
        # with nothing to unstick.
        if not await db.scalar(
            select(func.pg_try_advisory_xact_lock(CONVERT_LOCK_NS, lst.id))
        ):
            raise ConflictError(
                f"Another conversion is already running on '{lst.name}'. Wait for "
                "it to finish and try again."
            )

        before = await cls._fingerprint(db, lst.id)
        stored = await cls._resolve_target(
            db, lst, source, target_type, config, user, name=name
        )

        old_type, old_config = source.type, dict(source.config or {})
        if duplicate:
            # Goes live immediately: it displaces nothing, so there is no moment
            # where showing it would be wrong.
            target = Column(
                list_id=lst.id,
                name=name,
                display_name=display_name or name,
                # Inherited, not computed. Columns order by `position, id` and
                # nothing in the UI ever sets `position`, so in practice every column
                # is 0 and a duplicate sorts last by id rather than beside its source.
                position=source.position,
                type=target_type,
                config=stored,
            )
        else:
            # The undo. Created already binned, carrying the *old* type and config,
            # so restoring it from the bin gives back exactly the column that was
            # there. The unique index on name is partial on `deleted_at IS NULL`, so
            # this name cannot collide with anything live.
            target = Column(
                list_id=lst.id,
                name=f"{source.name[:100]}{_BACKUP_SUFFIX}",
                # Both halves are truncated against their column widths. `name` is
                # String(120) and the suffix is 18; `display_name` is String(255) and
                # the suffix is 20. Leaving the second uncapped raised
                # StringDataRightTruncationError, which is a `DataError` and not an
                # `IntegrityError` — so the handler below would not catch it and a
                # conversion of a column with a very long display name answered 500.
                #
                # `ColumnCreate.display_name` is `DisplayName` now, so a stored one is
                # at most 255 rather than unbounded — but this stays, because 255 plus
                # a 20-character suffix is still 20 too many. What changed is that the
                # cap here is the *only* thing standing between this and the error,
                # rather than a patch over a schema that let anything through.
                display_name=f"{source.display_name[:235]} (before conversion)",
                position=source.position,
                type=old_type,
                config=old_config,
                deleted_at=datetime.now(timezone.utc),
            )
        db.add(target)
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError(
                f"A column named '{name}' already exists on this list"
                if duplicate
                else "A column is already using the name this conversion needs"
            ) from exc

        skey, tkey = str(source.uuid), str(target.uuid)
        # For a conversion the converted value lands under the source's *own* key —
        # which is what keeps every view, form and formula pointing at it — and the
        # original moves to the backup column. For a duplicate only the new column's
        # key is written and the source is untouched.
        keys = (tkey, None) if duplicate else (skey, tkey)
        # The converter is told what the values are becoming, which is *not* the row
        # created above when this is a conversion: that row is the backup and carries
        # the **old** type, and handing it over would convert text to text and write
        # the source's values back unchanged. It exists for its uuid. For a duplicate
        # the created row is the new column, so it is the spec as well.
        #
        # Never added to the session, so it has no `uuid` — that is a flush-time
        # default. Safe because the converter reads only `type` and `config`, and the
        # keys written below come from the flushed `target`; but it is one
        # `_validate_value` change away from being a `None` where a uuid is expected.
        spec = target if duplicate else Column(
            list_id=lst.id,
            name=source.name,
            display_name=source.display_name,
            type=target_type,
            config=stored,
        )
        report = await cls._run(db, lst, source, spec, user, keys=keys)

        # After the values are written, because they are what the gates are about,
        # and before the retype, because a failure here has to roll back a column
        # that never changed type.
        await cls._check_gates(db, lst, stored, keys[0])

        if not duplicate:
            # Retype the source row in place, so the column keeps its uuid, name and
            # position and nothing referring to it needs repointing. `type` is
            # immutable everywhere else in the service by design; this is the one
            # operation whose whole purpose is to change it.
            source.type = target_type
            source.config = stored

        # `text -> text` is a supported pair, so a conversion is one of the ways a
        # column becomes rich — over values this pass produced and never sanitised.
        live = target if duplicate else source
        if live.type == ColumnType.text and is_rich(stored):
            await resanitize_column(db, lst.id, live.uuid)

        # The last thing before committing. Anything that wrote to this list while
        # the pass was running is a record whose value this conversion either did not
        # see or has just overwritten with a stale one — exactly the failure the
        # staged design shipped with. Refuse rather than guess; nothing has been
        # committed, so the rollback is total and the retry is safe.
        if await cls._fingerprint(db, lst.id) != before:
            await db.rollback()
            raise ConflictError(
                "Records changed while the conversion was running, so it was not "
                "applied. Nothing was altered — run it again."
            )

        await db.commit()
        # A conversion rewrites the cell of every record that had a value, so every
        # open grid is showing something out of date. After the commit, and after the
        # fingerprint re-check above, so a refused conversion signals nothing.
        broker.notify(lst.id)
        await db.refresh(live)
        return live

    @classmethod
    async def _check_gates(
        cls, db: AsyncSession, lst: List, config: dict, key: str
    ) -> None:
        """`required` and `unique`, which the pass deliberately did not enforce.

        Per cell they would be the wrong question: a blank source cell is ordinary,
        and a duplicate only matters once the column is real. Here they are exactly
        right, because this is the moment it becomes part of the list — and because
        this runs inside the transaction, a failure leaves nothing behind at all.
        """
        params = {"lid": lst.id, "key": key}

        if config.get("required"):
            missing = await db.scalar(
                sa.text(
                    "SELECT count(*) FROM list_items "
                    " WHERE list_id = :lid AND deleted_at IS NULL "
                    "   AND (NOT (data ? :key) "
                    "        OR jsonb_typeof(data -> :key) = 'null' "
                    "        OR data ->> :key = '')"
                ),
                params,
            )
            if missing:
                raise ConflictError(
                    f"{missing:,} record(s) would have no value for a required "
                    "column. Fix them, or drop `required` from the conversion."
                )

        if config.get("unique"):
            duplicated = await db.scalar(
                sa.text(
                    "SELECT count(*) FROM ("
                    "  SELECT data ->> :key AS v FROM list_items "
                    "   WHERE list_id = :lid AND deleted_at IS NULL "
                    "     AND data ->> :key IS NOT NULL AND data ->> :key <> '' "
                    "   GROUP BY 1 HAVING count(*) > 1) d"
                ),
                params,
            )
            if duplicated:
                raise ConflictError(
                    f"{duplicated:,} value(s) would appear more than once in a "
                    "unique column."
                )
