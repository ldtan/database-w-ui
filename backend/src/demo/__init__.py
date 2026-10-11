"""Demo scenarios: a whole plausible tenant, built through the real services.

Not fixtures and not migrations — these scripts exist so that a fresh database can
be turned into something worth looking at: users, groups, workspaces, tables,
columns, records, views, forms, metrics and permission grants that hang together
the way a real deployment's would.

    uv run python -m src.demo.school

See `school.py` for the scenario it builds and `_builders.py` for the idempotent
helpers every scenario is made of.
"""
