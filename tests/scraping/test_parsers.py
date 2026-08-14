"""Tests for the pure text-to-domain parsers. No network, no browser.

Fixtures mirror real rows captured from ens-navegable's #tablaResumen (via
a HAR from the live site — see scripts/capture_dom.py), not fabricated
data: e.g. mp.if.1 (a whole-category measure) and mp.s.4 (a single-
dimension measure with a "n.a." level and reinforcement notes).
"""

from __future__ import annotations

import pytest

from ensmcp.domain.models import (
    ApplicabilityLevel,
    CategoryGroup,
    Reinforcement,
    SecurityDimension,
)
from ensmcp.scraping.parsers import (
    parse_category,
    parse_category_group,
    parse_dimension,
    parse_dimension_labels,
    parse_levels,
    parse_measure,
    parse_reinforcements,
)
from tests.support import check


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("C", SecurityDimension.CONFIDENCIALIDAD),
        ("confidencialidad", SecurityDimension.CONFIDENCIALIDAD),
        ("i", SecurityDimension.INTEGRIDAD),
        (" D ", SecurityDimension.DISPONIBILIDAD),
        ("Autenticidad", SecurityDimension.AUTENTICIDAD),
        ("t", SecurityDimension.TRAZABILIDAD),
    ],
)
def test_parse_dimension_accepts_letters_and_words(label: str, expected: SecurityDimension) -> None:
    check(parse_dimension(label) is expected)


def test_parse_dimension_rejects_unknown_label() -> None:
    with pytest.raises(ValueError, match="Unknown security dimension label"):
        parse_dimension("x")


def test_parse_dimension_labels_rejects_an_empty_column() -> None:
    # The real table never leaves the dimension column blank, so an empty/
    # whitespace-only cell is a missing value, not "zero dimensions": raising
    # surfaces a malformed row instead of silently building a dimensionless
    # measure that no dimension filter could ever match.
    with pytest.raises(ValueError, match="dimension column is empty"):
        parse_dimension_labels("")
    with pytest.raises(ValueError, match="dimension column is empty"):
        parse_dimension_labels("   ")


def test_parse_dimension_labels_categoria_means_every_dimension() -> None:
    check(parse_dimension_labels("Categoría") == frozenset(SecurityDimension))
    check(parse_dimension_labels(" categoría ") == frozenset(SecurityDimension))


def test_parse_dimension_labels_splits_letter_combinations() -> None:
    check(
        parse_dimension_labels("C I T A")
        == frozenset(
            {
                SecurityDimension.CONFIDENCIALIDAD,
                SecurityDimension.INTEGRIDAD,
                SecurityDimension.TRAZABILIDAD,
                SecurityDimension.AUTENTICIDAD,
            }
        )
    )
    check(parse_dimension_labels("D") == frozenset({SecurityDimension.DISPONIBILIDAD}))


def test_parse_levels_treats_na_as_not_applicable_and_the_rest_as_applies() -> None:
    check(
        parse_levels(["aplica", "aplica", "aplica"])
        == frozenset({ApplicabilityLevel.BASICO, ApplicabilityLevel.MEDIO, ApplicabilityLevel.ALTO})
    )


def test_parse_levels_treats_reinforcement_notes_as_applies() -> None:
    check(
        parse_levels(["n.a.", "aplica", "+ R1"])
        == frozenset({ApplicabilityLevel.MEDIO, ApplicabilityLevel.ALTO})
    )


def test_parse_levels_all_not_applicable() -> None:
    check(parse_levels(["n.a.", "N.A.", " n.a. "]) == frozenset())


def test_parse_levels_rejects_an_empty_cell() -> None:
    # The real table never leaves a Bajo/Medio/Alto cell blank, so an empty/
    # whitespace-only cell is a missing value, not "applies": raising surfaces
    # a malformed row instead of silently claiming a level applies when its
    # data is missing — the same contract parse_dimension_labels enforces for
    # the dimension column.
    with pytest.raises(ValueError, match="level cell is empty"):
        parse_levels(["aplica", "", "aplica"])
    with pytest.raises(ValueError, match="level cell is empty"):
        parse_levels(["aplica", "   ", "n.a."])


@pytest.mark.parametrize("cell", ["aplicacion rota", "+ texto perdido"])
def test_level_parsers_reject_an_unrecognized_applies_prefix(cell: str) -> None:
    for parse in (parse_levels, parse_reinforcements):
        with pytest.raises(ValueError, match=r"neither 'aplica' nor 'n\.a\.'"):
            parse(["n.a.", cell, "aplica"])


def test_parse_levels_rejects_a_wrong_cell_count_with_a_clear_message() -> None:
    # A malformed measure row with fewer than six <td> cells yields a short
    # slice (e.g. texts[3:6] of length 2). That must surface as a clear
    # boundary error naming the expected Bajo/Medio/Alto cell count, not as
    # an opaque internal zip() message.
    with pytest.raises(ValueError, match="expected 3 level cells"):
        parse_levels(["aplica", "aplica"])


def test_parse_reinforcements_reads_the_note_parse_levels_discards() -> None:
    # Real row: mp.s.4 | ... | n.a. / aplica / + R1 — the same three cells
    # parse_levels() collapses to "applies at medio and alto".
    check(
        parse_reinforcements(["n.a.", "aplica", "+ R1"])
        == frozenset({Reinforcement(code="R1", level=ApplicabilityLevel.ALTO)})
    )


def test_parse_reinforcements_reads_several_notes_in_one_cell() -> None:
    # Real op.exp.8 row: "+ R1 + R2 + R3 + R4" means all four are required.
    check(
        parse_reinforcements(["aplica", "+ R1 + R2 + R3 + R4", "+ R3"])
        == frozenset(
            {
                Reinforcement(code="R1", level=ApplicabilityLevel.MEDIO),
                Reinforcement(code="R2", level=ApplicabilityLevel.MEDIO),
                Reinforcement(code="R3", level=ApplicabilityLevel.MEDIO),
                Reinforcement(code="R4", level=ApplicabilityLevel.MEDIO),
                Reinforcement(code="R3", level=ApplicabilityLevel.ALTO),
            }
        )
    )


def test_parse_reinforcements_marks_a_bracketed_group_as_a_choice() -> None:
    # Real op.acc.5 Bajo cell: "+ [R1 o R2 o R3 o R4]" asks for *one* of the
    # four, not all four. Reading it as a conjunction would tell a DdA to
    # implement four authentication mechanisms where the ENS asks for one.
    check(
        parse_reinforcements(["+ [R1 o R2 o R3 o R4]", "n.a.", "n.a."])
        == frozenset(
            Reinforcement(code=code, level=ApplicabilityLevel.BASICO, alternative=True)
            for code in ("R1", "R2", "R3", "R4")
        )
    )


def test_parse_reinforcements_separates_a_choice_from_what_is_required() -> None:
    # Real op.acc.5 Medio/Alto cell: "+ [R2 o R3 o R4] + R5" — one of the
    # three, and R5 on top of it.
    parsed = parse_reinforcements(["n.a.", "+ [R2 o R3 o R4] + R5", "n.a."])

    check(
        parsed
        == frozenset(
            {
                Reinforcement(code="R2", level=ApplicabilityLevel.MEDIO, alternative=True),
                Reinforcement(code="R3", level=ApplicabilityLevel.MEDIO, alternative=True),
                Reinforcement(code="R4", level=ApplicabilityLevel.MEDIO, alternative=True),
                Reinforcement(code="R5", level=ApplicabilityLevel.MEDIO, alternative=False),
            }
        )
    )
    # The bracketed codes must not *also* show up as required: that would ask
    # for the choice and every one of its options at the same time.
    check(not any(r.code != "R5" and not r.alternative for r in parsed))


def test_parse_reinforcements_is_case_insensitive_but_normalizes_the_code() -> None:
    # "r1" and "R1" are the same reinforcement: upper-casing is what stops a
    # lower-case spelling from yielding a second, distinct one.
    check(
        parse_reinforcements(["aplica", "+ r1 + R1", "n.a."])
        == frozenset({Reinforcement(code="R1", level=ApplicabilityLevel.MEDIO)})
    )


def test_parse_reinforcements_finds_none_in_a_plain_row() -> None:
    check(parse_reinforcements(["aplica", "aplica", "aplica"]) == frozenset())
    check(parse_reinforcements(["n.a.", "n.a.", "n.a."]) == frozenset())


@pytest.mark.parametrize(
    "cell",
    ["+ [R1 o R2", "+ R1 o R2]", "+ ] R1 ["],
    ids=["no-cierra", "no-abre", "al-reves"],
)
def test_parse_reinforcements_rejects_an_unclosed_choice_group(cell: str) -> None:
    # El patrón sólo casa un grupo **cerrado**, así que un corchete que no
    # cierra no casaba nada y la celda entera se leía como "lo de fuera": una
    # elección entre dos se convertía en dos obligaciones. En silencio y en la
    # dirección peligrosa — una DdA exigiendo más de lo que el ENS pide, que es
    # justo la confusión que el grupo de elección existe para evitar. Un
    # desequilibrio se rechaza, como cualquier otra fila malformada aquí.
    with pytest.raises(ValueError, match="unclosed choice group"):
        parse_reinforcements(["n.a.", cell, "n.a."])


def test_parse_reinforcements_rejects_a_wrong_cell_count() -> None:
    # Same contract as parse_levels: both read these cells positionally, so a
    # short slice from a malformed row must fail the same way in both.
    with pytest.raises(ValueError, match="expected 3 level cells"):
        parse_reinforcements(["aplica", "+ R1"])


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("org", CategoryGroup.MARCO_ORGANIZATIVO),
        ("org.1", CategoryGroup.MARCO_ORGANIZATIVO),
        ("op.pl", CategoryGroup.MARCO_OPERACIONAL),
        ("mp.if.3", CategoryGroup.MEDIDAS_PROTECCION),
    ],
)
def test_parse_category_group_derives_prefix(code: str, expected: CategoryGroup) -> None:
    check(parse_category_group(code) is expected)


def test_parse_category_group_rejects_unknown_prefix() -> None:
    with pytest.raises(ValueError, match="Unknown category group prefix"):
        parse_category_group("zz.1")


def test_parse_category_builds_full_category() -> None:
    category = parse_category("mp.if", "Protección de las instalaciones e infraestructuras")

    check(category.code == "mp.if")
    check(category.name == "Protección de las instalaciones e infraestructuras")
    check(category.group is CategoryGroup.MEDIDAS_PROTECCION)


def test_parse_measure_for_a_whole_category_measure() -> None:
    # Real row: mp.if.1 | Áreas separadas y con control de acceso | Categoría | aplica x3
    measure = parse_measure(
        code="mp.if.1",
        title="Áreas separadas y con control de acceso",
        dimension_column="Categoría",
        level_cells=["aplica", "aplica", "aplica"],
    )

    check(measure.code == "mp.if.1")
    check(measure.category_code == "mp.if")
    check(measure.dimensions == frozenset(SecurityDimension))
    check(
        measure.levels
        == frozenset({ApplicabilityLevel.BASICO, ApplicabilityLevel.MEDIO, ApplicabilityLevel.ALTO})
    )
    check(measure.description == "")


def test_parse_measure_for_a_single_dimension_measure_with_reinforcements() -> None:
    # Real row: mp.s.4 | Protección frente a denegación de servicio | D | n.a. / aplica / + R1
    measure = parse_measure(
        code="mp.s.4",
        title="Protección frente a denegación de servicio",
        dimension_column="D",
        level_cells=["n.a.", "aplica", "+ R1"],
    )

    check(measure.category_code == "mp.s")
    check(measure.dimensions == frozenset({SecurityDimension.DISPONIBILIDAD}))
    check(measure.levels == frozenset({ApplicabilityLevel.MEDIO, ApplicabilityLevel.ALTO}))
    check(
        measure.reinforcements
        == frozenset({Reinforcement(code="R1", level=ApplicabilityLevel.ALTO)})
    )
    # The verbatim cells survive alongside the parsed answer, so a spelling
    # this parser did not anticipate is still readable downstream.
    check(measure.raw_levels == ("n.a.", "aplica", "+ R1"))


def test_parse_measure_for_a_top_level_org_measure() -> None:
    # Real row: org.1 | Política de seguridad | Categoría | aplica x3
    measure = parse_measure(
        code="org.1",
        title="Política de seguridad",
        dimension_column="Categoría",
        level_cells=["aplica", "aplica", "aplica"],
        description="La política de seguridad se aprueba por el órgano competente.",
    )

    check(measure.category_code == "org")
    check(measure.description.startswith("La política"))


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        # El typo que el sitio comete de verdad, en la frase del RD que enumera
        # las alternativas de mp.com.4: "+ [R1o R2 o R3]". Con el `\b` de cierre,
        # "R1" dentro de "R1o" no casaba y el refuerzo desaparecía en silencio.
        ("+ [R1o R2 o R3]", {"R1", "R2", "R3"}),
        # En una lista obligatoria lo perdido no es una opción entre varias, es
        # algo que el sistema tiene que implantar.
        ("+ R1o + R2", {"R1", "R2"}),
        # Y quitar ese `\b` no puede romper un código de dos cifras: `\d+` es
        # codicioso, así que "R10" sigue siendo R10 y nunca R1.
        ("+ R10 + R11", {"R10", "R11"}),
    ],
    ids=["typo-en-eleccion", "typo-en-lista", "dos-cifras"],
)
def test_parse_reinforcements_survives_the_sites_own_missing_space(
    cell: str, expected: set[str]
) -> None:
    parsed = parse_reinforcements(["n.a.", cell, "n.a."])

    check({r.code for r in parsed} == expected, f"salieron {sorted(r.code for r in parsed)}")


def test_parse_reinforcements_does_not_invent_a_code_mid_word() -> None:
    # La otra mitad de lo mismo: el `\b` de apertura sigue puesto, así que una
    # palabra que acabe en R<n> no aporta un refuerzo de la nada.
    with pytest.raises(ValueError, match="level cell"):
        parse_reinforcements(["n.a.", "+ VAR12", "n.a."])


@pytest.mark.parametrize(
    ("cell", "applies"),
    [
        ("n.a.", False),
        ("N.A.", False),
        # El punto comido. Es exactamente el tipo de errata que este sitio
        # comete —"R1o", "[mp.s. 4.r1.2]"— y antes decía lo contrario de lo que
        # quería decir: la medida pasaba a aplicar en ese nivel.
        ("n.a", False),
        ("N/A", False),
        ("N. A.", False),
        ("no aplica", False),
        ("aplica", True),
        ("+ R1", True),
        ("+ [R1 o R2]", True),
        ("aplica + R1", True),
    ],
    ids=[
        "na",
        "na-mayusculas",
        "na-sin-punto-final",
        "na-con-barra",
        "na-con-espacio",
        "no-aplica",
        "aplica",
        "refuerzo",
        "eleccion",
        "aplica-con-refuerzo",
    ],
)
def test_parse_levels_recognises_both_answers_including_the_sites_typos(
    cell: str, applies: bool
) -> None:
    parsed = parse_levels([cell, "n.a.", "n.a."])

    check(
        (ApplicabilityLevel.BASICO in parsed) is applies,
        f"{cell!r} se leyó como {'aplica' if not applies else 'n.a.'}",
    )


def test_parse_levels_refuses_a_cell_it_does_not_recognise() -> None:
    # La regla era "aplica salvo que ponga exactamente 'n.a.'", así que toda
    # grafía en la que nadie pensó caía del lado de "aplica" — en silencio, y en
    # la dirección que mete una medida en una Declaración de Aplicabilidad de la
    # que el Anexo II la excluye. Ahora se niega, como cualquier otra forma
    # malformada de este módulo: el servidor sigue con el snapshot y una persona
    # mira la tabla.
    with pytest.raises(ValueError, match="neither 'aplica' nor"):
        parse_levels(["pendiente", "aplica", "aplica"])
