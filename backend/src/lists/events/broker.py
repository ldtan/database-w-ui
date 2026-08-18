"""Fan-out from a record write to the streams watching that list.

Two methods, `subscribe` and `notify`, and the whole point of naming them that
narrowly is that the implementation underneath is replaceable. This one keeps its
subscribers in a dict in this process, which is exactly right while the API runs a
single uvicorn worker (`numInstances` is unset in deploy/render/render.yaml, and
there is no `--workers`). Two situations end that, and both are the same swap —
Postgres `LISTEN`/`NOTIFY` behind this interface, with each worker holding one
dedicated listener connection created at startup, outside the request pool:

  - **More than one process serving the API.** A writer on instance A cannot set an
    asyncio.Event that lives on instance B, so half the watchers would go quiet with
    nothing to show for it.
  - **Wanting the maintenance jobs to signal.** `reconcile_shapes`,
    `purge_deleted_items` and `_references` run from .github/workflows/maintenance.yml
    on GitHub's runners — a wholly separate process, which this broker cannot hear
    and never will. That is accepted rather than worked around: they run nightly and
    they are maintenance, not somebody's edit.

**A subscriber's mailbox is an `asyncio.Event`, not a queue, and that is the design
rather than a shortcut.** An event carries no content (see the package docstring), so
"three records changed" and "a record changed" are the same message — there is
nothing to lose by collapsing them. An Event coalesces by construction: it has one
bit of state, so a 200-row import that notified per row wakes its watchers once, and
there is no queue size to pick, no drop policy to get wrong, and no way for a slow
reader to grow memory without bound. A queue would have to be given all three, and
would then spend them delivering duplicates of a message with no content in it.
"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager


class ListEventBroker:
    """Wakes every stream watching a list when that list's records change."""

    def __init__(self) -> None:
        # list id -> the events of everyone currently watching it. Keyed by the
        # integer id rather than the uuid because that is what a write path has in
        # hand (`lst.id`), and a notify must never have to go and look something up.
        self._watchers: dict[int, set[asyncio.Event]] = {}

    @contextmanager
    def subscribe(self, list_id: int) -> Iterator[asyncio.Event]:
        """Watch one list. Yields the event that `notify` will set.

        A context manager so that a stream removes itself however it ends —
        client hung up, shutdown, max lifetime, or an exception — because a
        subscriber that never unsubscribes is a leak that also gets woken forever.
        """
        event = asyncio.Event()
        self._watchers.setdefault(list_id, set()).add(event)
        try:
            yield event
        finally:
            watchers = self._watchers.get(list_id)
            if watchers is not None:
                watchers.discard(event)
                # Drop the key with its last watcher, so an app that has served a
                # million lists is not still holding a set for each of them.
                if not watchers:
                    del self._watchers[list_id]

    def notify(self, list_id: int) -> None:
        """Tell everyone watching this list to look again.

        Call it **after the write has committed**, never before: a rolled-back
        write must signal nothing, and a watcher woken early re-reads the old rows
        and then believes it is current.

        Synchronous, allocation-free in the common case, and it cannot raise for
        any reason a caller could act on — which is what makes it safe to put on the
        end of a write path. It also never awaits, so it cannot be the thing that
        yields control between a commit and the response.
        """
        for event in self._watchers.get(list_id, ()):
            event.set()

    def watcher_count(self, list_id: int) -> int:
        """How many streams are watching this list. For tests and diagnostics."""
        return len(self._watchers.get(list_id, ()))


# One per process, imported directly by the write paths. A module-level singleton
# rather than app state because the writers are service classmethods that are handed
# a session and nothing else — threading a broker down to them would mean a
# parameter on every write in the app to serve one line at the end of each.
broker = ListEventBroker()


# Set once, from the shutdown half of the lifespan in main.py. Every open stream
# races on it, so a redeploy ends them at once instead of uvicorn's graceful
# shutdown waiting out each connection's full lifetime — which it would, because an
# SSE stream is an in-flight request that has simply not finished answering yet.
#
# Created lazily: this module is imported at definition time, before there is a
# running loop, and an asyncio.Event bound to the wrong loop is a bug that only
# shows up under the test suite (which runs many loops) rather than under uvicorn
# (which runs one).
_shutdown: asyncio.Event | None = None


def shutdown_event() -> asyncio.Event:
    """The process-wide "stop streaming" flag, created on first use."""
    global _shutdown
    if _shutdown is None:
        _shutdown = asyncio.Event()
    return _shutdown


def begin_shutdown() -> None:
    """End every open stream. Called from the lifespan's shutdown half."""
    shutdown_event().set()


def reset_shutdown() -> None:
    """Forget the shutdown flag, so the next stream starts from a clear one.

    For tests, which build and tear down the app repeatedly inside one session and
    would otherwise inherit a set event — and a loop it no longer belongs to — from
    whichever test ran last.
    """
    global _shutdown
    _shutdown = None
