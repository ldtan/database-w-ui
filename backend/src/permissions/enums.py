from enum import Enum


class ResourceType(str, Enum):
    """Kinds of resource a permission grant can target.

    ``item``/``view``/``form`` inherit from their ``list``; ``list`` inherits from
    its ``workspace``; ``workspace`` is the root.
    """

    workspace = "workspace"
    list = "list"
    view = "view"
    form = "form"
    item = "item"


class Action(str, Enum):
    """What a principal may do on a resource.

    ``view/create/edit/delete`` act on records and on reading/deleting the resource
    itself; ``configure`` covers list settings, columns, forms, and views;
    ``share`` manages a resource's own grants; ``manage`` is full control.

    ``lookup`` is the one action that does *not* open the resource: it lets a
    principal resolve the list's row labels from a lookup column pointed at it,
    while the list itself stays out of their "Shared with me" and cannot be
    opened. It is what a shared reference list ("Countries", "Departments")
    wants — see ``ROLE_PRESETS['lookup only']`` and the collection filters in
    ``PermissionService``, which deliberately ignore a grant that confers only
    this.
    """

    view = "view"
    create = "create"
    edit = "edit"
    delete = "delete"
    configure = "configure"
    share = "share"
    manage = "manage"
    lookup = "lookup"


ALL_ACTIONS: frozenset[Action] = frozenset(Action)

# Anything that lets you read the resource lets you read a label out of it, so
# ``view`` carries ``lookup`` — every existing grant keeps resolving labels exactly
# as it did before ``lookup`` existed.
_READING: frozenset[Action] = frozenset({Action.view, Action.lookup})

# A granted action confers itself plus everything it implies. ``manage`` implies
# every action; the rest imply at least ``view`` so a grantee can always read what
# they can act on. ``lookup`` implies only itself — that is the whole point of it.
_IMPLIES: dict[Action, frozenset[Action]] = {
    Action.manage: ALL_ACTIONS,
    Action.configure: _READING | {Action.configure},
    Action.share: _READING | {Action.share},
    Action.edit: _READING | {Action.edit},
    Action.create: _READING | {Action.create},
    Action.delete: _READING | {Action.delete},
    Action.view: _READING,
    Action.lookup: frozenset({Action.lookup}),
}

# ``_IMPLIES`` inverted: which granted actions would confer a given one. The
# collection filters are SQL and cannot call ``expand_actions``, so they test the
# stored action list against this set instead. Filtering on ``CONFERRED_BY[view]``
# rather than on the existence of a grant row is what keeps a lookup-only list out
# of its grantee's collections — and settles the degenerate case that predates
# ``lookup``: a grant with no actions at all used to list the resource while
# permitting nothing on it.
CONFERRED_BY: dict[Action, frozenset[Action]] = {
    action: frozenset(
        granted for granted, implied in _IMPLIES.items() if action in implied
    )
    for action in Action
}


def expand_actions(granted) -> set[Action]:
    """Expand a collection of granted actions (Action or str) into the full set
    they confer, following the implication map."""
    result: set[Action] = set()
    for raw in granted:
        action = raw if isinstance(raw, Action) else Action(raw)
        result |= _IMPLIES.get(action, frozenset({action}))
    return result


# Named presets for the future sharing UI / convenience.
ROLE_PRESETS: dict[str, set[Action]] = {
    "lookup only": {Action.lookup},
    "viewer": {Action.view},
    "editor": {Action.view, Action.create, Action.edit, Action.delete},
    "manager": set(ALL_ACTIONS),
}
