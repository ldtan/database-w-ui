import enum


class FormType(str, enum.Enum):
    create = "create"
    update = "update"
    custom = "custom"
