"""Live records: the server tells an open table *that* it changed.

Two halves, deliberately small:

- `broker` — fan-out from a write to whoever is watching that table.
- `routers` — `GET /tables/{uuid}/events`, the stream the browser holds open.

The one rule everything here exists to keep: **an event carries no record data.**
Per-record permissions differ per subscriber (`PermissionService.record_view_filter`),
so a fan-out of rows would hand every watcher the same rows regardless of what each
is entitled to. The channel says "look again"; the looking happens through
`GET /records/`, which already knows what that caller may see.
"""
