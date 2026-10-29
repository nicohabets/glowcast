"""The repair issues a source or output type raises (architecture 4.4).

Each type brings its own: the generic code only raises and removes them.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Issue:
    """A repair issue of one source or output type, and its line in the log."""

    # The issue id is f"{key}_{entry_id}". Never rename a key: an issue raised
    # by an older version would then not be removed.
    key: str
    # Its title and description, under "issues" in translations/*.json.
    translation_key: str
    # Why Glowcast cannot sync, for the log (no secrets).
    message: str
