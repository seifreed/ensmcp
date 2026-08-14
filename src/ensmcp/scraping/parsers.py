"""Pure text-to-domain conversion, independent of how the text was found.

These functions never touch Patchright or the network — they take plain
strings already extracted from the DOM and build domain objects. The shape
of those strings (a "Categoría"/dimension-letters column, three Bajo/Medio/
Alto cells) mirrors the real ``#tablaResumen`` table on ens-navegable,
captured via a real HAR from the live site (see scripts/capture_dom.py).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ensmcp.domain.applicability import parse_levels as parse_levels
from ensmcp.domain.applicability import parse_reinforcements as parse_reinforcements
from ensmcp.domain.models import (
    AuditRequirement,
    Category,
    CategoryGroup,
    SecurityDimension,
    SecurityMeasure,
)

_DIMENSION_LABELS: dict[str, SecurityDimension] = {
    "c": SecurityDimension.CONFIDENCIALIDAD,
    "confidencialidad": SecurityDimension.CONFIDENCIALIDAD,
    "i": SecurityDimension.INTEGRIDAD,
    "integridad": SecurityDimension.INTEGRIDAD,
    "d": SecurityDimension.DISPONIBILIDAD,
    "disponibilidad": SecurityDimension.DISPONIBILIDAD,
    "a": SecurityDimension.AUTENTICIDAD,
    "autenticidad": SecurityDimension.AUTENTICIDAD,
    "t": SecurityDimension.TRAZABILIDAD,
    "trazabilidad": SecurityDimension.TRAZABILIDAD,
}

_APPLIES_TO_WHOLE_CATEGORY_LABEL = "categoría"


def parse_dimension(label: str) -> SecurityDimension:
    """Map a single dimension label (letter or full word, any case)."""
    key = label.strip().casefold()
    if key not in _DIMENSION_LABELS:
        raise ValueError(f"Unknown security dimension label: {label!r}")
    return _DIMENSION_LABELS[key]


def parse_dimension_labels(column_text: str) -> frozenset[SecurityDimension]:
    """Parse the "Por categoría o dimensión(es)" column of a measure row.

    "Categoría" means the measure applies across every dimension; anything
    else is a whitespace-separated list of dimension letters (e.g. "C I").
    The real table never leaves this column blank, so an empty/whitespace-only
    cell is a missing value, not "zero dimensions": raising surfaces a
    malformed row instead of silently building a dimensionless measure that no
    dimension filter could ever match.
    """
    normalized = column_text.strip()
    if not normalized:
        raise ValueError("measure dimension column is empty")
    if normalized.casefold() == _APPLIES_TO_WHOLE_CATEGORY_LABEL:
        return frozenset(SecurityDimension)
    return frozenset(parse_dimension(part) for part in normalized.split())


def parse_category_group(category_code: str) -> CategoryGroup:
    """Derive the org/op/mp group a category belongs to from its code."""
    prefix = category_code.split(".")[0]
    try:
        return CategoryGroup(prefix)
    except ValueError as exc:
        raise ValueError(f"Unknown category group prefix: {prefix!r}") from exc


def parse_category(code: str, name: str) -> Category:
    """Build a Category from its raw code and display name."""
    return Category(code=code, name=name, group=parse_category_group(code))


def parse_measure(
    code: str,
    title: str,
    dimension_column: str,
    level_cells: Sequence[str],
    description: str = "",
    norm_text: str = "",
    reinforcement_texts: Mapping[str, str] | None = None,
    audit_requirements: tuple[AuditRequirement, ...] = (),
) -> SecurityMeasure:
    """Build a SecurityMeasure from one ``#tablaResumen`` measure row.

    ``category_code`` is derived from ``code`` (e.g. "mp.if.1" -> "mp.if"),
    matching the real table's structure instead of requiring the caller to
    track it separately. The summary table row itself carries no free-text
    description — ``NavegableRepository`` looks it up separately (by code,
    from ``requisitos.js``) and passes it in; ``description`` defaults to
    empty only for callers, such as tests, with no such lookup available.

    Reinforcements and the verbatim level cells need no extra argument: both
    are read from the ``level_cells`` this already receives, so no caller has
    to pass or thread anything new. ``audit_requirements`` does arrive from
    outside, like ``description``: it comes from the same requisitos.js the
    repository already fetches, keyed by measure code. ``norm_text`` likewise,
    from the norms/ens.js the ``reinforcement_texts`` already come out of.
    """
    return SecurityMeasure(
        code=code,
        title=title,
        description=description,
        norm_text=norm_text,
        category_code=code.rsplit(".", 1)[0],
        dimensions=parse_dimension_labels(dimension_column),
        levels=parse_levels(level_cells),
        reinforcements=parse_reinforcements(level_cells, reinforcement_texts),
        raw_levels=tuple(level_cells),
        audit_requirements=audit_requirements,
    )
