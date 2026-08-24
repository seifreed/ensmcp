"""Parse the three ENS applicability cells into domain values."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from ensmcp.domain.models import DimensionLevel, Reinforcement

_LEVEL_ORDER = (DimensionLevel.BAJO, DimensionLevel.MEDIO, DimensionLevel.ALTO)
_NOT_APPLICABLE_KEYS = frozenset({"na", "noaplica"})
_REINFORCEMENT_PATTERN = re.compile(r"\bR\d+", re.IGNORECASE)
_CHOICE_GROUP_PATTERN = re.compile(r"\[[^\]]*\]")


def _require_level_cell_count(level_cells: Sequence[str]) -> None:
    if len(level_cells) != len(_LEVEL_ORDER):
        raise ValueError(
            f"expected {len(_LEVEL_ORDER)} level cells (Bajo/Medio/Alto), got {len(level_cells)}"
        )


def _applies(cell: str) -> bool:
    key = "".join(cell.split()).replace(".", "").replace("/", "").casefold()
    if key in _NOT_APPLICABLE_KEYS:
        return False
    reinforcement = _REINFORCEMENT_PATTERN.search(cell)
    if key == "aplica" or (
        reinforcement is not None and (key.startswith("+") or key.startswith("aplica+"))
    ):
        return True
    raise ValueError(f"level cell is neither 'aplica' nor 'n.a.': {cell!r}")


def parse_levels(level_cells: Sequence[str]) -> frozenset[DimensionLevel]:
    """Return the levels whose Bajo/Medio/Alto cells say they apply."""
    _require_level_cell_count(level_cells)
    applies: list[DimensionLevel] = []
    for level, text in zip(_LEVEL_ORDER, level_cells, strict=True):
        stripped = text.strip()
        if not stripped:
            raise ValueError(f"level cell is empty (expected 'aplica' or 'n.a.'): {text!r}")
        if _applies(stripped):
            applies.append(level)
    return frozenset(applies)


def parse_reinforcements(
    level_cells: Sequence[str], texts: Mapping[str, str] | None = None
) -> frozenset[Reinforcement]:
    """Return every reinforcement named by the applicability cells."""
    parse_levels(level_cells)
    found: set[Reinforcement] = set()
    for level, cell in zip(_LEVEL_ORDER, level_cells, strict=True):
        for group in _CHOICE_GROUP_PATTERN.finditer(cell):
            found.update(_reinforcements_in(group.group(), level, texts, alternative=True))
        outside = _CHOICE_GROUP_PATTERN.sub(" ", cell)
        if "[" in outside or "]" in outside:
            raise ValueError(f"level cell has an unclosed choice group: {cell!r}")
        found.update(_reinforcements_in(outside, level, texts, alternative=False))
    return frozenset(found)


def _reinforcements_in(
    fragment: str,
    level: DimensionLevel,
    texts: Mapping[str, str] | None,
    *,
    alternative: bool,
) -> set[Reinforcement]:
    codes = {match.group().upper() for match in _REINFORCEMENT_PATTERN.finditer(fragment)}
    return {
        Reinforcement(
            code=code,
            level=level,
            alternative=alternative,
            text="" if texts is None else texts.get(code, ""),
        )
        for code in codes
    }
