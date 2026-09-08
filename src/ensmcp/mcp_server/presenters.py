"""Map ENS domain values to stable MCP payloads."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any

from ensmcp.domain.crosswalk import DataPack
from ensmcp.domain.models import (
    ApplicableMeasure,
    ArticleCheck,
    AuditRequirement,
    Category,
    DimensionLevel,
    MaturityLevel,
    MeasureEvidence,
    SecurityDimension,
    SecurityMeasure,
)
from ensmcp.domain.profiles import (
    ApplicabilityReason,
    EffectiveDimension,
    MeasureApplicability,
    ProfileScopeEvaluation,
)
from ensmcp.domain.queries import code_order, required_audit_requirements

_LEVEL_SORT_ORDER = tuple(DimensionLevel)
_LEVEL_COLUMN_NAMES = tuple(level.value for level in _LEVEL_SORT_ORDER)

# Y lo mismo para las dimensiones, que también tienen orden propio: la columna
# de la tabla las escribe "C I D A T", que es el del RD y el que declara
# ``SecurityDimension``. Alfabéticamente saldría "autenticidad,
# confidencialidad, disponibilidad, integridad, trazabilidad", que no es el de
# ninguna fuente.
_DIMENSION_SORT_ORDER = tuple(SecurityDimension)


def _ordered[E: Enum](members: frozenset[E], order: tuple[E, ...]) -> list[str]:
    """Los valores de un conjunto de enums, en el orden en que el RD los nombra.

    Un ``sorted`` pelado sobre los valores es lo que había, y es justo lo que el
    comentario de ``_LEVEL_SORT_ORDER`` dice que no vale: dejaba ``levels`` como
    ``["alto", "bajo", "medio"]`` en **cada** medida de **cada** payload, o sea
    la escala del ENS puesta del revés en el campo que más se lee. La regla ya
    estaba escrita ahí arriba y sólo la cumplían los refuerzos.
    """
    return [member.value for member in order if member in members]


def _category_to_dict(category: Category) -> dict[str, str]:
    return {"code": category.code, "name": category.name, "group": category.group.value}


def _measure_to_dict(
    measure: SecurityMeasure, *, compact: bool = False, include_norm_text: bool = True
) -> dict[str, Any]:
    if compact:
        payload: dict[str, Any] = {
            "code": measure.code,
            "title": measure.title,
            "category_code": measure.category_code,
            "dimensions": _ordered(measure.dimensions, _DIMENSION_SORT_ORDER),
            "levels": _ordered(measure.levels, _LEVEL_SORT_ORDER),
        }
        if include_norm_text:
            payload["norm_text"] = measure.norm_text
        return payload

    payload = {
        "code": measure.code,
        "title": measure.title,
        "description": measure.description,
        # Lo que exige el RD, junto a lo que pregunta la 808 (``description``).
        # Los dos, y no uno: quien prepara una auditoría quiere el cuestionario,
        # quien implanta la medida quiere el requisito, y la pregunta no permite
        # reconstruir el requisito.
        "norm_text": measure.norm_text,
        "category_code": measure.category_code,
        "dimensions": _ordered(measure.dimensions, _DIMENSION_SORT_ORDER),
        "levels": _ordered(measure.levels, _LEVEL_SORT_ORDER),
        "reinforcements": [
            {
                "code": reinforcement.code,
                "level": reinforcement.level.value,
                # True = "apply one of the alternatives flagged this way at
                # this level"; False = required outright. Collapsing the two
                # would misstate what a system actually has to implement.
                "alternative": reinforcement.alternative,
                "text": reinforcement.text,
            }
            for reinforcement in sorted(
                measure.reinforcements,
                # ``alternative`` closes the key, as it does in snapshot.codec:
                # reinforcements come out of a frozenset, whose iteration order
                # is arbitrary, so a key that does not separate two otherwise
                # equal entries would let the payload's order wobble between
                # runs. Nothing in the real table produces such a pair, which is
                # exactly why the guard belongs in the key rather than in a test.
                # ``code_order`` on the code, as in both codecs: plain string
                # order would put an "R10" ahead of "R2". And ``text`` closes
                # the key — the reasoning above enumerated three fields of a
                # ``Reinforcement`` and it has four, so two entries differing
                # only in their wording tied and came out in the frozenset's
                # arbitrary order, which is the wobble this key exists to stop.
                key=lambda item: (
                    _LEVEL_SORT_ORDER.index(item.level),
                    code_order(item.code),
                    item.alternative,
                    item.text,
                ),
            )
        ],
        # zip without strict= on purpose: a measure built outside the scraper
        # carries no raw cells at all, and {} says exactly that. From the live
        # table the tuple always has the three cells.
        "raw_levels": dict(zip(_LEVEL_COLUMN_NAMES, measure.raw_levels, strict=False)),
    }
    if not include_norm_text:
        payload.pop("norm_text")
    return payload


def _applicable_to_dict(
    applicable: ApplicableMeasure, *, compact: bool = False, include_norm_text: bool = True
) -> dict[str, Any]:
    """One Declaración de Aplicabilidad line: the measure, plus what it demands.

    The measure keeps the exact shape every other tool returns, so a client
    parses one kind of measure object. ``required_reinforcements`` drops the
    per-reinforcement ``level``, which would only repeat ``required_level``.
    """
    return {
        **_measure_to_dict(
            applicable.measure, compact=compact, include_norm_text=include_norm_text
        ),
        "required_level": applicable.required_level.value,
        "required_reinforcements": [
            {
                "code": reinforcement.code,
                "alternative": reinforcement.alternative,
                "text": reinforcement.text,
            }
            # ``alternative`` closes the key here for the same reason it does in
            # ``_measure_to_dict`` and in ``snapshot.codec``, and this was the
            # one of the three that lacked it. Every reinforcement in this set
            # already shares one level (``applicable_measures`` filtered them to
            # it), so the code is the *only* other field — and a cell naming one
            # both inside and outside its brackets yields the pair
            # (R1, alternative) and (R1, required), which the code alone cannot
            # separate. Out of a frozenset, whose iteration order is arbitrary,
            # that pair would then come out in a different order between runs.
            for reinforcement in sorted(
                applicable.reinforcements,
                key=lambda item: (code_order(item.code), item.alternative, item.text),
            )
        ],
    }


def _requirement_to_dict(measure_code: str, requirement: AuditRequirement) -> dict[str, Any]:
    """One audit question, carrying the measure it belongs to.

    The list is flat and each item names its measure, so a client asking for a
    whole level does not have to walk a nested structure to know what it is
    looking at.
    """
    return {
        "measure_code": measure_code,
        "position": requirement.position,
        "code": requirement.code,
        "level": requirement.level.value,
        "essential": requirement.essential,
        "question": requirement.question,
        "note": requirement.note,
    }


def _audited_to_dict(
    applicable: ApplicableMeasure,
    *,
    compact: bool = False,
    include_norm_text: bool = True,
    include_questions: bool = True,
) -> dict[str, Any]:
    """One ``alcance_auditoria`` line: the measure, and what it gets asked.

    The sibling of ``_applicable_to_dict`` — same measure shape, same
    ``required_level``, but carrying the questionnaire for that level instead of
    the reinforcements. Both live here rather than inline in their tool so the
    domain-to-wire flattening stays in one place in this module.
    """
    payload = {
        **_measure_to_dict(
            applicable.measure, compact=compact, include_norm_text=include_norm_text
        ),
        "required_level": applicable.required_level.value,
    }
    if include_questions:
        payload["audit_requirements"] = [
            _requirement_to_dict(applicable.measure.code, requirement)
            for requirement in required_audit_requirements(
                applicable.measure, applicable.required_level
            )
        ]
    return payload


def _article_to_dict(article: ArticleCheck) -> dict[str, Any]:
    return {
        "reference": article.reference,
        "title": article.title,
        "evidence": list(article.evidence),
        "questions": [
            {"reference": question.reference, "question": question.question}
            for question in article.questions
        ],
    }


def _evidence_to_dict(item: MeasureEvidence) -> dict[str, Any]:
    return {"measure_code": item.measure_code, "evidence": list(item.evidence)}


def _data_pack_to_dict(pack: DataPack) -> dict[str, Any]:
    return {
        "schema_version": pack.schema_version,
        "pack_id": pack.pack_id,
        "title": pack.title,
        "framework": pack.framework,
        "framework_version": pack.framework_version,
        "version": pack.version,
        "status": pack.status.value,
        "coverage": pack.coverage.value,
        "authority": pack.authority,
        "source_url": pack.source_url,
        "source_date": pack.source_date,
        "reviewed_at": pack.reviewed_at,
        "notes": pack.notes,
        "mapping_count": len(pack.mappings),
    }


def _maturity_to_dict(level: MaturityLevel) -> dict[str, str]:
    """The CMM level with its name, never the bare code.

    Every other code this server puts on the wire travels with what it means: a
    reinforcement with its ``text``, a requirement with its ``question``. This
    one used to be the exception — a plain ``"L4"``, which is only useful to
    someone who already has the guide open.
    """
    return {"code": level.code, "name": level.name}


def _effective_dimensions_to_dict(
    dimensions: Mapping[SecurityDimension, EffectiveDimension],
) -> dict[str, Any]:
    return {
        dimension.value: {
            "level": value.level.value,
            "evidence": [
                {
                    "source": item.source,
                    "level": item.level.value,
                    "justification": item.justification,
                }
                for item in value.evidence
            ],
        }
        for dimension in SecurityDimension
        if (value := dimensions.get(dimension)) is not None
    }


def _applicability_reason_to_dict(reason: ApplicabilityReason) -> dict[str, Any]:
    return {
        "basis": reason.basis,
        "dimensions": [dimension.value for dimension in reason.dimensions],
        "system_level": reason.system_level.value if reason.system_level is not None else None,
        "table_cell": reason.table_cell,
        "profile_chain": list(reason.profile_chain),
        "evidence": [
            {
                "dimension": item.dimension.value,
                "source": item.source,
                "level": item.level.value,
                "justification": item.justification,
            }
            for item in reason.evidence
        ],
    }


def _profile_scope_to_dict(evaluated: ProfileScopeEvaluation) -> dict[str, Any]:
    return {
        "scope_id": evaluated.scope_id,
        "scope": evaluated.scope,
        "categoria_sistema": evaluated.category.value,
        "dimensions": _effective_dimensions_to_dict(evaluated.dimensions),
        "compliance_profile_chain": list(evaluated.compliance_profile_chain),
        "applicable_measure_codes": list(evaluated.applicable_measure_codes),
    }


def _profile_measure_to_dict(explanation: MeasureApplicability) -> dict[str, Any]:
    return {
        "scope_id": explanation.scope_id,
        "measure_code": explanation.measure_code,
        "applicable": explanation.applicable,
        "reason": _applicability_reason_to_dict(explanation.reason),
        "required_reinforcements": [
            {
                "code": reinforcement.code,
                "alternative": reinforcement.alternative,
                "text": reinforcement.text,
            }
            for reinforcement in explanation.required_reinforcements
        ],
    }
