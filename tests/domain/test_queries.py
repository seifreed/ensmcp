"""Tests for the pure ENS domain query functions."""

from __future__ import annotations

import pytest

from ensmcp.domain.models import ApplicabilityLevel, SecurityDimension, SecurityMeasure
from ensmcp.domain.queries import (
    code_order,
    filter_measures,
    find_measure_by_code,
    fold,
    search_measures_by_text,
)
from ensmcp.snapshot.repository import SnapshotRepository
from tests.support import check

ORG_1 = SecurityMeasure(
    code="org.1",
    title="Política de seguridad",
    description="La política de seguridad es aprobada por el órgano superior competente.",
    category_code="org",
    dimensions=frozenset(SecurityDimension),
    levels=frozenset(ApplicabilityLevel),
)
MP_IF_3 = SecurityMeasure(
    code="mp.if.3",
    title="Protección de las instalaciones",
    description="Identificación de las áreas restringidas del centro de proceso de datos.",
    category_code="mp.if",
    dimensions=frozenset({SecurityDimension.DISPONIBILIDAD}),
    levels=frozenset({ApplicabilityLevel.MEDIO, ApplicabilityLevel.ALTO}),
)
MP_EQ_1 = SecurityMeasure(
    code="mp.eq.1",
    title="Puesto de trabajo despejado",
    description="Ausencia de información sensible en el puesto de trabajo desatendido.",
    category_code="mp.eq",
    dimensions=frozenset({SecurityDimension.CONFIDENCIALIDAD}),
    levels=frozenset({ApplicabilityLevel.BASICO}),
)
MEASURES = [ORG_1, MP_IF_3, MP_EQ_1]


def test_filter_measures_without_filters_returns_everything() -> None:
    check(filter_measures(MEASURES) == MEASURES)


def test_filter_measures_by_exact_category_code() -> None:
    check(filter_measures(MEASURES, category_code="org") == [ORG_1])


def test_filter_measures_by_category_group_prefix() -> None:
    check(filter_measures(MEASURES, category_code="mp") == [MP_IF_3, MP_EQ_1])


def test_filter_measures_by_category_code_with_no_match() -> None:
    check(filter_measures(MEASURES, category_code="op") == [])


def test_filter_measures_by_dimension() -> None:
    check(
        filter_measures(MEASURES, dimension=SecurityDimension.CONFIDENCIALIDAD) == [ORG_1, MP_EQ_1]
    )
    check(filter_measures(MEASURES, dimension=SecurityDimension.AUTENTICIDAD) == [ORG_1])


def test_filter_measures_by_level() -> None:
    check(filter_measures(MEASURES, level=ApplicabilityLevel.BASICO) == [ORG_1, MP_EQ_1])
    check(filter_measures(MEASURES, level=ApplicabilityLevel.ALTO) == [ORG_1, MP_IF_3])


def test_filter_measures_combines_every_filter() -> None:
    result = filter_measures(
        MEASURES,
        category_code="mp",
        dimension=SecurityDimension.DISPONIBILIDAD,
        level=ApplicabilityLevel.ALTO,
    )
    check(result == [MP_IF_3])


def test_find_measure_by_code_returns_match_after_skipping_earlier_items() -> None:
    check(find_measure_by_code(MEASURES, "mp.eq.1") is MP_EQ_1)


def test_find_measure_by_code_returns_none_when_absent() -> None:
    check(find_measure_by_code(MEASURES, "op.pl.1") is None)


def test_search_measures_by_text_matches_code() -> None:
    check(search_measures_by_text(MEASURES, "MP.EQ") == [MP_EQ_1])


def test_search_measures_by_text_matches_title() -> None:
    check(search_measures_by_text(MEASURES, "instalaciones") == [MP_IF_3])


def test_search_measures_by_text_matches_description() -> None:
    check(search_measures_by_text(MEASURES, "órgano superior") == [ORG_1])


def test_search_measures_by_text_returns_empty_when_nothing_matches() -> None:
    check(search_measures_by_text(MEASURES, "criptografía cuántica") == [])


def test_search_measures_by_text_ignores_accents_in_the_query() -> None:
    # The corpus is Spanish and its readers type Spanish in a hurry. Before
    # this, "politica" matched nothing at all while "política" matched org.1.
    check(search_measures_by_text(MEASURES, "politica") == [ORG_1])
    check(search_measures_by_text(MEASURES, "organo superior") == [ORG_1])
    check(
        search_measures_by_text(MEASURES, "politica")
        == search_measures_by_text(MEASURES, "política")
    )


@pytest.mark.parametrize(
    "query",
    [
        "",
        "   ",
        # Escapados a propósito: escritos tal cual son invisibles, y una prueba
        # sobre caracteres invisibles no puede depender de verlos en el fuente.
        "\N{SOFT HYPHEN}",  # el que el org.1 real lleva dentro
        "\N{ZERO WIDTH SPACE}",
        "\N{ZERO WIDTH NO-BREAK SPACE}",  # el BOM que arrastra un pegado
        "\N{COMBINING ACUTE ACCENT}",  # una tilde suelta, sin su letra
        "\N{NO-BREAK SPACE}",  # NFKD lo convierte en un espacio normal
        " \N{SOFT HYPHEN}\N{ZERO WIDTH SPACE} ",
    ],
)
def test_search_measures_by_text_returns_nothing_for_a_query_that_folds_away(query: str) -> None:
    # Una consulta que no deja nada tras plegarla no puede contestar con el
    # corpus entero, que es lo que hacía: ``fold`` quita los caracteres de
    # formato (categoría "Cf") porque son invisibles, así que un U+00AD suelto
    # —no vacío, no espacio: ``strip()`` no lo ve— se convertía en ``""``, y
    # ``"" in x`` es cierto para todo. Comprobarlo antes de plegar no alcanza a
    # verlo; por eso la guarda está sobre la aguja ya plegada.
    check(search_measures_by_text(MEASURES, query) == [], f"{query!r} devolvió medidas")


def test_search_measures_by_text_ignores_accents_in_the_corpus_too() -> None:
    # Folding only the query would still miss an accented title searched for
    # with its accent typed differently, so both sides go through fold().
    check(search_measures_by_text(MEASURES, "PolÍtIcA") == [ORG_1])


def test_fold_leaves_ascii_untouched() -> None:
    # The property that makes it safe to fold values headed for an enum or a
    # code lookup: every valid one is ASCII, and folding cannot change those.
    for value in ("org.1", "mp.if", "basico", "medio", "alto", "confidencialidad"):
        check(fold(value) == value, f"fold changed {value!r}")


def test_fold_drops_invisible_formatting_characters() -> None:
    # Un carácter de formato no tiene glifo: el lector no puede teclearlo ni ver
    # que está ahí, así que dejarlo pasar hace que ni copiar el texto tal y como
    # se muestra sirva para encontrarlo, y sin ninguna pista de por qué.
    check(fold("artáculo") == fold("artá­culo"), "el guion blando no se cayó")
    check(fold("a​b") == "ab", "espacio de anchura cero")
    check(fold("﻿hola") == "hola", "BOM")
    # Y sigue sin igualar lo que de verdad es distinto: la errata del sitio
    # cambia además la vocal, y eso `fold` no lo puede ni lo debe arreglar.
    check(fold("artículo") != fold("artáculo"), "fold no puede inventarse la vocal")


def test_fold_strips_diacritics_and_case() -> None:
    check(fold("Criptografía") == "criptografia")
    check(fold("AUTENTICACIÓN") == "autenticacion")
    check(fold("Básico") == "basico")


# The measures below come from the shipped snapshot, not from the three
# hand-built ones above: the reinforcement wording only exists in the real
# corpus, and that is precisely where the gap showed.
_REAL = SnapshotRepository.from_package_data().measures


def _codes(query: str) -> set[str]:
    return {measure.code for measure in search_measures_by_text(_REAL, query)}


def test_search_reaches_obligations_that_only_exist_in_a_reinforcement() -> None:
    # Before this, both returned nothing at all, so a consultant searching
    # "OTP" concluded the ENS never mentions it. It does — in the reinforcement
    # that is exactly what they would have to implement.
    check(_codes("OTP") == {"op.acc.5", "op.acc.6"}, f"OTP -> {_codes('OTP')}")
    check(_codes("doble factor") == {"op.acc.6"}, f"doble factor -> {_codes('doble factor')}")


def test_a_reinforcement_match_cannot_come_from_the_other_fields() -> None:
    # Proves the new field is what does the work: none of the matches has the
    # needle in its code, title or description.
    for measure in search_measures_by_text(_REAL, "OTP"):
        haystack = fold(f"{measure.code} {measure.title} {measure.description}")
        check("otp" not in haystack, f"{measure.code} ya casaba sin mirar los refuerzos")
        check(any("otp" in fold(r.text) for r in measure.reinforcements))


def test_search_reaches_what_only_the_rd_wording_of_a_measure_says() -> None:
    # El otro hueco, el simétrico: quien busca el requisito por como lo enuncia
    # el RD ("proceso formal de autorizaciones") no lo encontraba, porque lo
    # único que la medida traía era el cuestionario de la 808, que lo pregunta
    # con otras palabras. La redacción del refuerzo tampoco alcanzaba: org.4 no
    # tiene ninguno.
    check(_codes("proceso formal de autorizaciones") == {"org.4"})
    check(_codes("Reglamento General de Protección de Datos") == {"op.exp.7"})


def test_a_measure_wording_match_cannot_come_from_the_other_fields() -> None:
    # Igual que el de los refuerzos: el campo nuevo es lo que hace el trabajo,
    # ninguna de las coincidencias tenía la aguja en los otros campos.
    needle = fold("proceso formal de autorizaciones")
    for measure in search_measures_by_text(_REAL, "proceso formal de autorizaciones"):
        haystack = fold(f"{measure.code} {measure.title} {measure.description}")
        check(needle not in haystack, f"{measure.code} ya casaba sin mirar la norma")
        check(all(needle not in fold(r.text) for r in measure.reinforcements))
        check(needle in fold(measure.norm_text))


def test_searching_reinforcement_text_still_ignores_accents() -> None:
    # fold() applies to the new field too, so the ñ is not a wall either.
    check(_codes("contrasenas") == _codes("contraseñas"))
    check("op.acc.5" in _codes("contrasenas"), "op.acc.5 pide contraseñas en su R1")


def test_the_corpus_is_searchable_despite_the_sites_own_invisible_characters() -> None:
    # Regresión sobre el corpus real. CCN-CERT escribe en org.1 una palabra como
    # `art` + `á` + U+00AD SOFT HYPHEN + `culo` (verificado en el asset crudo:
    # los bytes son art\xc3\xa1\xc2\xadculo). Se muestra como "artáculo", pero
    # antes ni copiando eso literalmente se encontraba nada: el guion blando no
    # se ve, no se teclea, y sobrevivía al fold.
    #
    # Lo que esto NO arregla, y no debe: la errata cambia además la vocal, así
    # que "artículo" no casa **con la descripción**. El dato es el que sirve el
    # sitio.
    check("org.1" in _codes("artáculo 12"), f"artáculo 12 -> {_codes('artáculo 12')}")
    check(_codes("artaculo 12") == _codes("artáculo 12"), "con y sin tilde deben coincidir")
    org_1 = next(m for m in _REAL if m.code == "org.1")
    check("articulo 12" not in fold(org_1.description), "fold no corrige la vocal, y no lo intenta")
    # Y el texto servido sigue siendo el del sitio, sin limpiar.
    check("artá\xadculo" in org_1.description, "la descripción ya no es literal")


def test_the_rd_spells_correctly_what_the_questionnaire_mistyped() -> None:
    # La errata es de la CCN-STIC 808, no del RD: la misma referencia que
    # `description` escribe "artáculo 12" el `norm_text` la escribe bien. Buscar
    # "artículo 12" encuentra org.1 desde que la redacción del RD viaja con la
    # medida — y lo hace por el dato, no porque fold haya corregido nada.
    org_1 = next(m for m in _REAL if m.code == "org.1")

    check("artículo 12 de este real decreto" in org_1.norm_text, "el RD no dice eso en org.1")
    check("org.1" in _codes("artículo 12"), f"artículo 12 -> {_codes('artículo 12')}")


def test_the_audit_questionnaire_stays_searchable_through_the_description() -> None:
    # Regression guard for what is already covered: description is the
    # questionnaire flattened, so questions and their notes are reachable. If
    # that ever stops being true, this fails instead of silently losing them.
    check(_codes("¿Se dispone de evidencias de que el usuario reconoce") == {"op.acc.5"})
    check(_codes("NOTA: En algunos organismos") == {"org.1"})


@pytest.mark.parametrize(
    ("codes", "expected"),
    [
        # El caso real: op.exp.10 es la única medida de dos cifras del Anexo II.
        (
            ["op.exp.1", "op.exp.10", "op.exp.2", "op.exp.9"],
            ["op.exp.1", "op.exp.2", "op.exp.9", "op.exp.10"],
        ),
        # El mismo fallo en los refuerzos, que la tabla no usa todavía pero el
        # parser acepta a propósito ("R10" se lee R10, nunca R1).
        (["R1", "R10", "R2", "R9"], ["R1", "R2", "R9", "R10"]),
        # Códigos sin número y de longitud distinta no revientan la clave.
        (["org", "org.1", "op.acc.5", "mp.s.4"], ["mp.s.4", "op.acc.5", "org", "org.1"]),
    ],
    ids=["medidas", "refuerzos", "mezcla"],
)
def test_code_order_sorts_by_the_number_and_not_by_its_spelling(
    codes: list[str], expected: list[str]
) -> None:
    check(sorted(codes, key=code_order) == expected, f"salió {sorted(codes, key=code_order)}")


def test_code_order_never_compares_a_number_against_a_word() -> None:
    # La clave es segura por construcción: re.split con grupo de captura alterna
    # texto, número, texto..., así que una posición dada lleva siempre el mismo
    # tipo. Sin eso, ordenar una lista mixta lanzaría TypeError.
    every = ["org", "1", "a1", "1a", "", "op.exp.10", "R7", "mp.info.6", "a.b.c.4.1"]

    sorted(every, key=code_order)

    check(all(isinstance(part, str) for code in every for part in code_order(code)[::2]))
    check(all(isinstance(part, int) for code in every for part in code_order(code)[1::2]))


def test_a_category_filter_does_not_leak_into_a_sibling_that_shares_its_prefix() -> None:
    """El punto de ``_matches_category`` no es decorativo.

    El Anexo II tiene ``mp.s`` (Protección de los servicios), ``mp.si``
    (soportes de información) y ``mp.sw`` (aplicaciones informáticas): tres
    categorías distintas en las que el código de una es prefijo de las otras
    dos. Filtrando por prefijo pelado, ``mp.s`` devuelve 11 medidas en vez de 4
    y se trae las de las otras dos categorías — un filtro que dice servicios y
    contesta soportes.

    Y por el otro lado: un prefijo que no es una categoría entera no vale.
    ``mp.i`` no es nada, así que no puede devolver las de ``mp.if``.
    """
    measures = SnapshotRepository.from_package_data().measures

    servicios = filter_measures(measures, category_code="mp.s")

    check(len(servicios) == 4, f"mp.s devolvió {len(servicios)} medidas")
    check(
        all(m.category_code == "mp.s" for m in servicios),
        f"se colaron {sorted({m.category_code for m in servicios})}",
    )
    check(not filter_measures(measures, category_code="mp.i"), "mp.i no es una categoría")
    check(not filter_measures(measures, category_code="op.ex"), "op.ex no es una categoría")
    # El grupo sí, que es lo que el punto tiene que seguir permitiendo.
    check(len(filter_measures(measures, category_code="mp")) == 36)


def test_a_measure_is_found_by_its_own_name_with_a_word_dropped() -> None:
    """El fallo que esto arregla, con las tres medidas reales que lo sufrían.

    El corpus es prosa y quien lo consulta lo cita de memoria, así que se deja
    un artículo por el camino. Buscando como una sola tirada de caracteres, eso
    devolvía **nada**: `op.exp.8` se titula "Registro de la actividad" y la
    consulta "registro de actividad" —su propio nombre, menos una palabra— no lo
    encontraba. Una tool cuyo trabajo es encontrar medidas contestaba que no hay
    ninguna con el nombre casi exacto de una que existe.
    """
    check("op.exp.8" in _codes("registro de actividad"), f"-> {_codes('registro de actividad')}")
    check("org.1" in _codes("politica seguridad"), f"-> {_codes('politica seguridad')}")
    check("op.acc.3" in _codes("segregacion funciones"), f"-> {_codes('segregacion funciones')}")


def test_every_measure_is_found_by_its_own_title() -> None:
    # La propiedad sobre el corpus entero, no sobre tres ejemplos.
    for measure in _REAL:
        check(
            measure.code in _codes(measure.title), f"{measure.code} no se encuentra por su título"
        )


def test_matching_by_words_never_loses_what_matched_as_one_run() -> None:
    """Sólo puede ensanchar, nunca estrechar.

    Si la consulta entera aparece como una tirada, cada una de sus palabras
    aparece también, así que lo que casaba antes sigue casando. Se afirma sobre
    las 73 medidas: para cada título, lo que encuentra la subcadena tiene que
    estar contenido en lo que encuentran las palabras.
    """
    for measure in _REAL:
        title = fold(measure.title)
        as_one_run = {
            m.code for m in _REAL if any(title in fold(f) for f in (m.title, m.description))
        }
        check(as_one_run <= _codes(measure.title), f"{measure.code}: se perdieron resultados")


def test_the_words_of_a_query_must_all_land_in_the_same_field() -> None:
    # La otra mitad: repartir las palabras entre campos es el mismo falso
    # positivo que unir los campos en una cadena, por otra puerta. "arquitectura"
    # sólo está en el título de op.pl.2 y "usuarios." sólo en sus otros textos.
    check(_codes("arquitectura usuarios.") == set(), f"-> {_codes('arquitectura usuarios.')}")
    check("op.pl.2" in _codes("arquitectura"), "la palabra suelta sí tiene que encontrarla")
