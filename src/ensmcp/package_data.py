"""Reading the data files shipped inside the installed package.

The two corpora the server serves — the Anexo II snapshot and the CCN-STIC 808
extraction — live in ``ensmcp/data/`` and are read the exact same way, so the
lookup is written once here rather than in each loader.
"""

from __future__ import annotations

from importlib import resources

PACKAGE = "ensmcp"
DATA_DIRECTORY = "data"


def read(filename: str) -> str:
    """Return the text of one of the package's shipped data files.

    ``importlib.resources`` rather than a path relative to ``__file__``: the
    wheel may be a zip, and this is the only lookup that works either way.
    """
    return (resources.files(PACKAGE) / DATA_DIRECTORY / filename).read_text(encoding="utf-8")
