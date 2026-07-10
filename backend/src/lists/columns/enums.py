import enum


class ColumnType(str, enum.Enum):
    text = "text"
    long_text = "long_text"
    number = "number"
    checkbox = "checkbox"
    choice = "choice"
    lookup = "lookup"
    date = "date"
    time = "time"
    datetime = "datetime"
