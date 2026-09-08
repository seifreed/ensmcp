"""Validation and normalization at the MCP trust boundary."""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable, Sequence
from enum import Enum
from importlib import resources
from typing import Any

from ensmcp.domain.models import DimensionLevel, SecurityDimension, SecurityMeasure
from ensmcp.domain.profiles import (
    ResolvedComplianceProfile,
    SystemProfile,
    resolve_compliance_profile,
)
from ensmcp.domain.queries import find_measure_by_code, fold


def _normalize(value: str) -> str:
    """Strip, casefold and unaccent one boundary string.

    Real ENS codes and enum values are lowercase ASCII, so this lets ``"MP"`` /
    ``"Confidencialidad"`` / ``"ORG.1"`` match without altering any valid value
    — and ``level="Básico"`` resolve to ``bajo`` instead of failing as an
    unknown enum member, which is what a Spanish speaker will type. The one
    transform every free-text tool argument needs before it reaches domain code.
    """
    return fold(value.strip())


def _normalize_filter_value(value: str | None) -> str | None:
    """Normalize an optional string filter at the tool boundary.

    An empty or whitespace-only value means "no filter" (return ``None``), the
    same as if the argument had not been supplied at all. This keeps the three
    ``list_measures`` filters consistent: ``dimension=""`` and ``dimension="   "``
    both behave like ``dimension=None``.
    """
    if value is None:
        return None
    return _normalize(value) or None


def _category_vocabulary(measures: Iterable[SecurityMeasure]) -> set[str]:
    """Every code ``category_code`` could match, taken from the measures.

    Not from category headers: a table can legitimately carry measure rows
    with no category header above them — rows are recognised by their own class,
    never by what precedes them, and ``_reject_a_measureless_corpus`` says so in
    as many words. Validating against the header rows therefore refused every
    filter on a corpus that has none, which is a real shape and one the fixtures
    rely on.

    Taking it from the measures instead makes the vocabulary exactly what the
    filter operates on: a measure's own ``category_code`` and each of its dotted
    prefixes, because ``_matches_category`` accepts a group ("mp") as readily as
    a subcategory ("mp.if"). On the live corpus that is the same 18 codes
    ``list_categories`` serves, derived rather than assumed.
    """
    codes: set[str] = set()
    for measure in measures:
        parts = measure.category_code.split(".")
        codes.update(".".join(parts[: depth + 1]) for depth in range(len(parts)))
    return codes


def _known_code(raw: str | None, known: Collection[str], argument: str, no_such: str) -> str | None:
    """One argument naming something the corpus has, refusing what it does not.

    Blank still means "no filter", exactly as ``_normalize_filter_value`` says.
    What changes is the unknown code, which used to answer with an empty list —
    and an empty list is *also* a real answer here, which is the whole problem:
    the CCN-STIC 808 writes no questions at all for ``mp.com.2`` below nivel
    alto, so ``requisitos_auditoria(code="mp.com.2", level="basico")`` is
    legitimately empty. The two were the same bytes on the wire, so a typo came
    back indistinguishable from a fact about the ENS.

    And it reads as the fact, not as the typo. ``op.acc`` stops at 6, so a
    caller that asks about "op.acc.9" was told, in effect, that the ENS defines
    no audit requirements for it — a false statement about a Real Decreto,
    produced by a compliance tool, with nothing in the payload to hint that the
    code was never real.

    ``list_measures``' ``category_code`` has the same defect and gets the same
    answer: every one of the 18 categories has at least one measure, so an empty
    result from that filter *alone* never meant "this category is empty" — it
    only ever meant the argument was not a category. (With a dimension or a
    level alongside it, empty is real: ``op.cont`` protects only disponibilidad,
    so pairing it with any other dimension is legitimately nothing. Checking the
    category against the vocabulary on its own keeps those apart.)

    Same reasoning as ``_parse_optional_enum`` below, and the same shape of
    answer: the callers are language models that will guess a code as readily as
    they guess an enum value, and a client cannot correct itself from silence.
    ``raw`` is quoted as the caller typed it, not folded, for the reason given
    there.
    """
    wanted = _normalize_filter_value(raw)
    if wanted is None or wanted in known:
        return wanted
    raise ValueError(f"{argument}={raw!r} {no_such}")


def _paginate[T](
    items: Sequence[T], limit: int | None, cursor: str | None
) -> list[T] | dict[str, Any]:
    """Keep legacy list responses until a caller explicitly asks for a page."""
    if limit is None and cursor is None:
        return list(items)
    if limit is None:
        limit = 50
    if not 1 <= limit <= 500:
        raise ValueError("limit debe estar entre 1 y 500")
    try:
        start = 0 if cursor is None else int(cursor)
    except ValueError as exc:
        raise ValueError("cursor debe ser un índice entero") from exc
    if start < 0 or start > len(items):
        raise ValueError("cursor fuera del rango de resultados")
    end = min(start + limit, len(items))
    return {
        "items": list(items[start:end]),
        "next_cursor": str(end) if end < len(items) else None,
    }


def _filter_measure_codes(
    measures: Sequence[SecurityMeasure], codes: list[str] | None
) -> list[SecurityMeasure]:
    if codes is None:
        return list(measures)
    known = {measure.code for measure in measures}
    wanted = {_known_code(code, known, "measure_codes", _NO_SUCH_MEASURE) for code in codes}
    return [measure for measure in measures if measure.code in wanted]


_NO_SUCH_MEASURE = "no es ninguna medida del Anexo II; use list_measures para ver las que existen"
_NO_SUCH_CATEGORY = (
    "no es ninguna categoría del Anexo II; use list_categories para ver las que existen"
)


def _require_measure(measures: Sequence[SecurityMeasure], raw: str) -> SecurityMeasure:
    measure = find_measure_by_code(measures, _normalize(raw))
    if measure is None:
        raise ValueError(f"code={raw!r} {_NO_SUCH_MEASURE}")
    return measure


def _load_schema_catalog() -> dict[str, Any]:
    path = resources.files("ensmcp") / "schemas" / "v1" / "tools.json"
    catalog: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return catalog


def _profile_controls(
    profile: SystemProfile, measures: Sequence[SecurityMeasure]
) -> ResolvedComplianceProfile:
    controls = resolve_compliance_profile(
        profile.compliance_profiles, profile.active_compliance_profile
    )

    def normalize(codes: Iterable[str]) -> frozenset[str]:
        return frozenset(_require_measure(measures, code).code for code in codes)

    additional = normalize(controls.additional_measures)
    excluded = normalize(controls.excluded_measures)
    conflicting = additional & excluded
    if conflicting:
        raise ValueError(
            "un perfil no puede añadir y excluir las mismas medidas: " f"{sorted(conflicting)}"
        )
    return ResolvedComplianceProfile(
        chain=controls.chain,
        overrides=controls.overrides,
        additional_measures=additional,
        excluded_measures=excluded,
    )


def _parse_optional_enum[E: Enum](enum_type: type[E], raw: str | None, argument: str) -> E | None:
    """Resolve one enum-valued tool argument, or say what would have worked.

    Blank still means "no filter", exactly as ``_normalize_filter_value`` says.
    What changes is the failure: this is the only place input from outside the
    process arrives, and its callers are language models that will guess.

    Every guess in the ENS's own vocabulary is wrong. The Anexo II table heads
    its first level column "Bajo"; the RD names the categories "BÁSICA / MEDIA
    / ALTA"; this very server answers ``categoria_sistema: "alta"``. Yet the
    enum's own ``ValueError`` said only "'bajo' is not a valid
    ApplicabilityLevel" — naming a Python class that appears in no tool schema,
    no docstring and no payload, while withholding the three words that would
    have worked. A client cannot correct itself from that.

    ``raw`` is quoted as the caller typed it, not folded. Being told "'alta' is
    not a valid..." after sending ``"ALTA"`` hides the normalisation and reads
    like the server mangled the argument.
    """
    normalized = _normalize_filter_value(raw)
    if normalized is None:
        return None
    try:
        return enum_type(normalized)
    except ValueError:
        accepted = ", ".join(member.value for member in enum_type)
        raise ValueError(
            f"{argument}={raw!r} no es un valor válido; use uno de: {accepted}"
        ) from None


def _parse_dimension_levels(**by_name: str | None) -> dict[SecurityDimension, DimensionLevel]:
    """Turn the five optional tool arguments into the domain's level mapping.

    An omitted (or blank) dimension is one the system does not value, so it is
    left out of the mapping entirely rather than defaulted to a level — a
    defaulted "bajo" would silently pull in measures nobody asked for.
    """
    levels: dict[SecurityDimension, DimensionLevel] = {}
    for name, raw in by_name.items():
        # ``name`` is this function's own keyword, never client input, so only
        # the level needs the boundary's message.
        level = _parse_optional_enum(DimensionLevel, raw, name)
        if level is None:
            continue
        levels[SecurityDimension(name)] = level
    return levels
