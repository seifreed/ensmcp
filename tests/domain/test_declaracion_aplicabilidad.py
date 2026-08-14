"""Tests for the Declaración de Aplicabilidad rule (RD 311/2022, anexos I y II).

Run against the **real** corpus in the shipped snapshot, not fabricated rows:
this is the one piece of logic whose output a consultant signs, so what it has
to be right about is the actual 73 measures, their real "n.a." cells and their
real reinforcement notes.
"""

from __future__ import annotations

import itertools

import pytest

from ensmcp.domain.models import (
    ApplicabilityLevel,
    ApplicableMeasure,
    AuditRequirement,
    SecurityDimension,
)
from ensmcp.domain.queries import (
    DimensionLevels,
    applicable_measures,
    required_audit_requirements,
    required_level,
    required_maturity_level,
    system_category,
)
from ensmcp.snapshot.repository import SnapshotRepository
from tests.support import check, require

_C = SecurityDimension.CONFIDENCIALIDAD
_I = SecurityDimension.INTEGRIDAD
_D = SecurityDimension.DISPONIBILIDAD
_A = SecurityDimension.AUTENTICIDAD
_T = SecurityDimension.TRAZABILIDAD
_ALL = (_C, _I, _D, _A, _T)

_MEASURES = SnapshotRepository.from_package_data().measures


def _uniform(level: ApplicabilityLevel) -> dict[SecurityDimension, ApplicabilityLevel]:
    return dict.fromkeys(_ALL, level)


def _codes(levels: dict[SecurityDimension, ApplicabilityLevel]) -> set[str]:
    return {item.measure.code for item in applicable_measures(_MEASURES, levels)}


def _line(
    levels: dict[SecurityDimension, ApplicabilityLevel], code: str
) -> ApplicableMeasure | None:
    return next(
        (item for item in applicable_measures(_MEASURES, levels) if item.measure.code == code), None
    )


def test_system_category_is_the_highest_dimension_level() -> None:
    # Anexo I, ap. 4: ALTA si alguna dimensión alcanza ALTO; MEDIA si el máximo
    # es MEDIO; BÁSICA si el máximo es BAJO.
    check(system_category({_C: ApplicabilityLevel.BASICO}) is ApplicabilityLevel.BASICO)
    check(
        system_category({_C: ApplicabilityLevel.BASICO, _I: ApplicabilityLevel.ALTO})
        is ApplicabilityLevel.ALTO
    )
    check(
        system_category({_C: ApplicabilityLevel.MEDIO, _D: ApplicabilityLevel.BASICO})
        is ApplicabilityLevel.MEDIO
    )


def test_system_category_needs_at_least_one_valued_dimension() -> None:
    with pytest.raises(ValueError, match="al menos una dimensión"):
        system_category({})


def test_applicable_measures_needs_at_least_one_valued_dimension() -> None:
    # An empty valuation is a caller mistake, not "a system with no measures":
    # returning [] would read as a valid, empty DdA.
    with pytest.raises(ValueError, match="al menos una dimensión"):
        applicable_measures(_MEASURES, {})


def test_a_higher_category_never_drops_a_measure() -> None:
    # The RD's strong invariant: the measures are cumulative up the scale, so a
    # bug that mis-picks a cell would almost certainly break this.
    basico = _codes(_uniform(ApplicabilityLevel.BASICO))
    medio = _codes(_uniform(ApplicabilityLevel.MEDIO))
    alto = _codes(_uniform(ApplicabilityLevel.ALTO))

    check(basico < medio, f"básico ({len(basico)}) no está contenido en medio ({len(medio)})")
    check(medio < alto, f"medio ({len(medio)}) no está contenido en alto ({len(alto)})")
    check(len(alto) == len(_MEASURES), "un sistema todo-alto debe exigir las 73 medidas")


def test_a_na_cell_keeps_a_measure_out_until_its_level_is_reached() -> None:
    # mp.info.4 protects only trazabilidad and its cells are n.a. / n.a. /
    # aplica, so it is demanded at ALTO and at no other level.
    check("mp.info.4" not in _codes({_T: ApplicabilityLevel.MEDIO}))
    check("mp.info.4" in _codes({_T: ApplicabilityLevel.ALTO}))


def test_a_measure_is_demanded_at_the_level_of_the_dimensions_it_protects() -> None:
    # op.exp.8 protects trazabilidad only, so a system whose T is medio owes it
    # at medio — regardless of the other four dimensions being alto.
    levels = _uniform(ApplicabilityLevel.ALTO) | {_T: ApplicabilityLevel.MEDIO}

    line = require(_line(levels, "op.exp.8"))
    check(line.required_level is ApplicabilityLevel.MEDIO)
    # The cell is self-contained: medio already lists R1..R4, and alto R1..R5.
    check({r.code for r in line.reinforcements} == {"R1", "R2", "R3", "R4"})
    check(not any(r.alternative for r in line.reinforcements))


def test_reading_one_cell_does_not_accumulate_reinforcements_across_levels() -> None:
    alto = require(_line(_uniform(ApplicabilityLevel.ALTO), "op.exp.8"))

    check({r.code for r in alto.reinforcements} == {"R1", "R2", "R3", "R4", "R5"})
    # Every reinforcement reported belongs to the level demanded, and no other.
    check(all(r.level is ApplicabilityLevel.ALTO for r in alto.reinforcements))


def test_a_choice_group_survives_into_the_declaration() -> None:
    # op.acc.5 protects C, I, A and T. At básico the RD asks for one of
    # R1..R4; at medio for one of R2..R4 *plus* R5 outright.
    basico = require(_line(_uniform(ApplicabilityLevel.BASICO), "op.acc.5"))
    check({r.code for r in basico.reinforcements} == {"R1", "R2", "R3", "R4"})
    check(all(r.alternative for r in basico.reinforcements), "básico debería ser todo elección")

    medio = require(_line(_uniform(ApplicabilityLevel.MEDIO), "op.acc.5"))
    check(
        {r.code: r.alternative for r in medio.reinforcements}
        == {"R2": True, "R3": True, "R4": True, "R5": False}
    )


def test_a_category_row_is_demanded_at_the_system_category() -> None:
    # The equivalence the implementation leans on: "Categoría" rows are modelled
    # as all five dimensions, and max over the five *is* the system category —
    # so no special case is needed. Checked over every such row at once.
    levels = {_C: ApplicabilityLevel.ALTO, _I: ApplicabilityLevel.BASICO}
    category = system_category(levels)

    rows = [m for m in _MEASURES if len(m.dimensions) == len(SecurityDimension)]
    check(len(rows) == 45, f"esperaba 45 filas 'Categoría', hay {len(rows)}")
    check(all(required_level(row, levels) is category for row in rows))


def test_an_unvalued_dimension_excludes_the_measures_that_only_protect_it() -> None:
    # A system that does not value trazabilidad owes nothing that protects only
    # trazabilidad, and its category is unaffected by the absence.
    levels = {_C: ApplicabilityLevel.ALTO}

    codes = _codes(levels)
    check("op.exp.8" not in codes, "op.exp.8 sólo protege trazabilidad, sin valorar aquí")
    check("org.1" in codes, "una fila 'Categoría' sí se exige")
    check(system_category(levels) is ApplicabilityLevel.ALTO)


def test_every_line_reports_a_level_the_measure_actually_has() -> None:
    # Nothing may be reported as demanded at a level whose cell reads "n.a.".
    for item in applicable_measures(_MEASURES, _uniform(ApplicabilityLevel.MEDIO)):
        check(
            item.required_level in item.measure.levels,
            f"{item.measure.code} exigida en {item.required_level} pero su celda es n.a.",
        )


def _scope(levels: dict[SecurityDimension, ApplicabilityLevel]) -> list[AuditRequirement]:
    return [
        requirement
        for item in applicable_measures(_MEASURES, levels)
        for requirement in required_audit_requirements(item.measure, item.required_level)
    ]


def test_audit_requirements_accumulate_up_to_the_required_level() -> None:
    # CCN-STIC 808 §5: los de "Categoría Básica" se exigen a todas las
    # categorías, los de "Media" a MEDIA y ALTA. Un sistema medio responde
    # ambos. op.pl.1 tiene 7 preguntas de básico y 5 de medio.
    op_pl_1 = require(next((m for m in _MEASURES if m.code == "op.pl.1"), None))

    medio = required_audit_requirements(op_pl_1, ApplicabilityLevel.MEDIO)
    check(len(medio) == 12, f"esperaba 7+5=12 preguntas, hay {len(medio)}")
    check({r.level for r in medio} == {ApplicabilityLevel.BASICO, ApplicabilityLevel.MEDIO})
    # Y nada del tramo alto, que a un sistema medio no se le exige.
    check(all(r.level is not ApplicabilityLevel.ALTO for r in medio))


def test_reading_only_the_required_levels_section_would_lose_most_of_the_scope() -> None:
    # El fallo que esta regla evita: quedarse con el tramo suelto deja fuera la
    # mayor parte del temario.
    op_pl_1 = require(next((m for m in _MEASURES if m.code == "op.pl.1"), None))

    acumulado = required_audit_requirements(op_pl_1, ApplicabilityLevel.MEDIO)
    solo_el_tramo = [r for r in op_pl_1.audit_requirements if r.level is ApplicabilityLevel.MEDIO]
    check(len(acumulado) > len(solo_el_tramo), "el acumulado tiene que ser mayor")
    check(len(solo_el_tramo) == 5)


def test_the_audit_scope_of_a_real_system() -> None:
    # El sistema de ejemplo del README, medido contra el corpus real.
    levels = {
        _C: ApplicabilityLevel.ALTO,
        _I: ApplicabilityLevel.MEDIO,
        _D: ApplicabilityLevel.BASICO,
        _A: ApplicabilityLevel.MEDIO,
        _T: ApplicabilityLevel.MEDIO,
    }
    scope = _scope(levels)

    check(len(applicable_measures(_MEASURES, levels)) == 66)
    check(len(scope) == 382, f"esperaba 382 preguntas, hay {len(scope)}")
    check(sum(1 for r in scope if r.essential) == 136)


def test_a_basic_system_is_only_asked_the_first_tier() -> None:
    scope = _scope(_uniform(ApplicabilityLevel.BASICO))

    check(len(scope) > 0)
    check({r.level for r in scope} == {ApplicabilityLevel.BASICO})


def test_a_higher_category_never_drops_an_audit_question() -> None:
    # La misma invariante que en la DdA, ahora sobre el temario.
    def texts(level: ApplicabilityLevel) -> set[tuple[str, int]]:
        return {
            (item.measure.code, requirement.position)
            for item in applicable_measures(_MEASURES, _uniform(level))
            for requirement in required_audit_requirements(item.measure, item.required_level)
        }

    check(texts(ApplicabilityLevel.BASICO) < texts(ApplicabilityLevel.MEDIO))
    check(texts(ApplicabilityLevel.MEDIO) < texts(ApplicabilityLevel.ALTO))


def test_a_measure_that_does_not_apply_contributes_no_questions() -> None:
    # mp.info.4 sólo se exige con trazabilidad en alto, así que con T=medio no
    # debe aportar ni una pregunta al temario.
    levels = {_T: ApplicabilityLevel.MEDIO}
    aportan = {
        item.measure.code
        for item in applicable_measures(_MEASURES, levels)
        if required_audit_requirements(item.measure, item.required_level)
    }
    check("mp.info.4" not in aportan)


@pytest.mark.parametrize(
    ("category", "code", "name"),
    [
        (ApplicabilityLevel.BASICO, "L2", "Reproducible, pero intuitivo"),
        (ApplicabilityLevel.MEDIO, "L3", "Proceso definido"),
        (ApplicabilityLevel.ALTO, "L4", "Gestionado y medible"),
    ],
)
def test_required_maturity_level_per_category(
    category: ApplicabilityLevel, code: str, name: str
) -> None:
    # CCN-STIC 808 §6, con los nombres literales de su tabla. El código solo no
    # le dice nada a quien lo recibe, y la guía sí lo dice.
    nivel = required_maturity_level(category)

    check(nivel.code == code, f"código: {nivel.code!r}")
    check(nivel.name == name, f"nombre: {nivel.name!r}")


# Las cuatro valoraciones posibles de una dimensión: sin valorar, o en uno de los
# tres niveles. Cinco dimensiones dan 1024 combinaciones, 1023 valorables.
_VALUATIONS = (None, ApplicabilityLevel.BASICO, ApplicabilityLevel.MEDIO, ApplicabilityLevel.ALTO)
_RANK = {value: rank for rank, value in enumerate(_VALUATIONS)}


def _every_system() -> list[tuple[ApplicabilityLevel | None, ...]]:
    """Las 1023 valoraciones con al menos una dimensión valorada."""
    return [
        combo
        for combo in itertools.product(_VALUATIONS, repeat=len(_ALL))
        if any(level is not None for level in combo)
    ]


def _levels(combo: tuple[ApplicabilityLevel | None, ...]) -> DimensionLevels:
    return {
        dimension: level for dimension, level in zip(_ALL, combo, strict=True) if level is not None
    }


def _demanded(
    combo: tuple[ApplicabilityLevel | None, ...],
) -> tuple[set[str], set[tuple[str, int]]]:
    """Las medidas exigidas y las preguntas que se le harán a ese sistema."""
    applicable = applicable_measures(_MEASURES, _levels(combo))
    return (
        {line.measure.code for line in applicable},
        {
            (line.measure.code, requirement.position)
            for line in applicable
            for requirement in required_audit_requirements(line.measure, line.required_level)
        },
    )


def test_the_declaration_holds_its_invariants_for_every_possible_system() -> None:
    """Las 1023 valoraciones, no sólo las uniformes.

    Los demás tests de este fichero fijan casos concretos —todo-bajo, todo-alto,
    una dimensión suelta—. Éste recorre el espacio entero, que es donde viven las
    combinaciones mixtas que nadie escribe a mano y un consultario sí tiene: un
    sistema ALTO en confidencialidad y BAJO en disponibilidad no se parece a
    ninguno de los uniformes, y es la forma real de casi cualquier sistema.
    """
    for combo in _every_system():
        levels = _levels(combo)
        category = system_category(levels)

        check(
            category == max(levels.values(), key=lambda level: _RANK[level]),
            f"{combo}: la categoría no es el máximo de los niveles valorados",
        )
        for line in applicable_measures(_MEASURES, levels):
            # Nunca se exige una medida en un nivel cuya celda dice "n.a.".
            check(
                line.required_level in line.measure.levels,
                f"{combo}: {line.measure.code} exigida en {line.required_level}, que es n.a.",
            )
            # El nivel exigible es el de las dimensiones que la medida protege, y
            # sólo las valoradas: no la categoría del sistema.
            check(
                line.required_level == required_level(line.measure, levels),
                f"{combo}: {line.measure.code} con un nivel exigible inconsistente",
            )
            # Los refuerzos servidos son exactamente los de esa celda.
            check(
                line.reinforcements
                == {r for r in line.measure.reinforcements if r.level is line.required_level},
                f"{combo}: {line.measure.code} con refuerzos de otro nivel",
            )


def test_raising_any_dimension_never_takes_away_a_measure_or_a_question() -> None:
    """Monotonía en el retículo completo, no sólo en la diagonal uniforme.

    Subir **una** dimensión un escalón sólo puede añadir: la del README ("lo de
    un nivel está contenido en lo del siguiente") es el caso uniforme, y esto es
    el general, 7665 pares. Es lo que caza un ``required_level`` que dejara de
    ser un máximo, o un orden de ``_LEVEL_RANK`` invertido, en el caso mixto —
    donde ninguno de los tests uniformes nota la diferencia.

    Los **refuerzos** quedan fuera a propósito, y no por comodidad: el RD
    sustituye variantes débiles por fuertes al subir de nivel, así que su
    conjunto legítimamente encoge. ``op.pl.1`` pide "+ R1" en media y "+ R2" en
    alta —R1 es el análisis de riesgos *semiformal* y R2 el *formal*, que lo
    subsume— y ``op.acc.5`` ofrece elegir entre R1..R4 en básica pero sólo entre
    R2..R4 en media, porque la opción más débil deja de valer. Las tres celdas
    de las 73 medidas están comprobadas contra la prosa del propio RD en
    ``tests/scraping/test_norm_texts.py``, que es donde eso se afirma.
    """
    demanded = {combo: _demanded(combo) for combo in _every_system()}

    compared = 0
    for combo, (measures, questions) in demanded.items():
        for index, current in enumerate(combo):
            for higher in _VALUATIONS[_RANK[current] + 1 :]:
                raised = demanded[(*combo[:index], higher, *combo[index + 1 :])]
                compared += 1
                check(
                    measures <= raised[0],
                    f"{combo} -> subir {_ALL[index].value} a {higher} pierde "
                    f"{sorted(measures - raised[0])}",
                )
                check(
                    questions <= raised[1],
                    f"{combo} -> subir {_ALL[index].value} a {higher} pierde preguntas de "
                    f"{sorted({code for code, _ in questions - raised[1]})}",
                )
    check(compared == 7665, f"esperaba 7665 pares comparados, fueron {compared}")
