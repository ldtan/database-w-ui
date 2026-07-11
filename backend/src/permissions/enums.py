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
    """

    view = "view"
    create = "create"
    edit = "edit"
    delete = "delete"
    configure = "configure"
    share = "share"
    manage = "manage"


ALL_ACTIONS: frozenset[Action] = frozenset(Action)

# A granted action confers itself plus everything it implies. ``manage`` implies
# every action; the rest imply at least ``view`` so a grantee can always read what
# they can act on.
_IMPLIES: dict[Action, frozenset[Action]] = {
    Action.manage: ALL_ACTIONS,
    Action.configure: frozenset({Action.configure, Action.view}),
    Action.share: frozenset({Action.share, Action.view}),
    Action.edit: frozenset({Action.edit, Action.view}),
    Action.create: frozenset({Action.create, Action.view}),
    Action.delete: frozenset({Action.delete, Action.view}),
    Action.view: frozenset({Action.view}),
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
    "viewer": {Action.view},
    "editor": {Action.view, Action.create, Action.edit, Action.delete},
    "manager": set(ALL_ACTIONS),
}
