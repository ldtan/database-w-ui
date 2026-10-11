"""Sanitising and de-tagging rich text cells.

A `text` column in `rich` mode stores HTML, which the UI renders as HTML. That
makes the stored value a security boundary, exactly as a `link` column's scheme
is (see SAFE_LINK_SCHEMES): whatever survives this module is what a browser will
be handed. So sanitising happens on the way *in*, in TableRecordService, and not in
the editor — the editor is one client of an API that also takes direct PUTs and
CSV imports, and a check it can skip is not a check.

The allowlist is the formatting the editor's toolbar can produce and nothing
else. It has no `style`, no `class`, no `img`, and no `id`: those are how a
sanitised document still ends up carrying a payload or overlaying the page.
"""
import html
import re

import nh3

from .validation import SAFE_LINK_SCHEMES

# What the toolbar offers, and nothing else. No `span`: with this toolbar Quill
# only emits one for its own table-marker furniture (`<span class="ql-ui">`),
# which is meaningless outside the editor, and stripping the class would leave an
# empty element in every table record. A disallowed tag is unwrapped rather than
# deleted, so nothing a person typed is lost by leaving it out.
ALLOWED_TAGS = {
    "p",
    "br",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "s",
    "sub",
    "sup",
    "a",
    "ul",
    "ol",
    "li",
    "h1",
    "h2",
    "h3",
    "blockquote",
    "pre",
    "code",
}

# Only what a link needs, plus one piece of Quill's data model. Quill 2 wraps
# *both* kinds of table in `<ol>` and records which one it is as `data-table` on
# each `li`, so dropping that attribute would silently turn every bullet table
# into a numbered one. The `.rich-text-cell` rules in themes.css read it back.
ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "li": {"data-table"},
}

# The same schemes a link column may use. A rich cell must not become the way to
# smuggle in a `javascript:` URL that the link column refuses.
ALLOWED_URL_SCHEMES = set(SAFE_LINK_SCHEMES)

# Where one block ends and the next begins, so stripping tags does not run the
# last word of a paragraph into the first word of the next.
_BLOCK_BREAK = re.compile(
    r"(?i)</(?:p|div|li|h[1-6]|blockquote|pre|tr)\s*>|<br\s*/?>"
)


def sanitize(value: str) -> str:
    """The value as it is safe to store and later render as HTML."""
    return nh3.clean(
        value,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer",
    )


def to_text(value: str) -> str:
    """The visible text, with the markup taken out.

    What `min_length`/`max_length` count for a rich column: a limit of 200 is
    about how much someone may write, not how much markup their formatting
    happened to generate.
    """
    return html.unescape(nh3.clean(_BLOCK_BREAK.sub(" ", value), tags=set())).strip()


def is_rich(config: dict) -> bool:
    """Whether this column's config asks for rich text. Callers must already
    know the column is `text` — no other type reads `text_mode`."""
    return (config or {}).get("text_mode") == "rich"
