"""Imports every model module, so ``Base.metadata`` is complete.

Importing a model module is what registers its table on ``Base.metadata``. Three
places need the full picture, and each of them fails *silently* and differently if
the list falls behind:

- ``main.py`` lifespan — the drift check compares metadata against the live database;
  a missing model means its table is simply never checked.
- ``tests/conftest.py`` — ``create_all`` would not create the table.
- ``migrations/env.py`` — worst of all: autogenerate sees the table as absent from the
  models and emits a **DROP TABLE** for it.

So the list lives here once, and those three import this module. Adding a model means
adding one line, here.
"""

import src.auth.tokens.models  # noqa: F401
import src.auth.users.models  # noqa: F401
import src.lists.columns.models  # noqa: F401
import src.lists.forms.models  # noqa: F401
import src.lists.items.models  # noqa: F401
import src.lists.metrics.models  # noqa: F401
import src.lists.models  # noqa: F401
import src.lists.views.models  # noqa: F401
import src.people.models  # noqa: F401
import src.permissions.models  # noqa: F401
import src.workspaces.models  # noqa: F401
