import enum


class FormType(str, enum.Enum):
    create = "create"
    update = "update"
    # Display-only: lays out a record for *reading*, so a view can stay narrow and
    # still leave every field reachable. Unlike create/update it may include
    # calculated columns (they are computed on read but rejected on write), and its
    # fields' ``enabled`` flag means nothing — nothing on it is editable.
    view = "view"
    custom = "custom"
