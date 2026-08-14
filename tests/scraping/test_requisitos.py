"""Offline tests for the requisitos.js description parser.

No network, no Patchright: ``fixtures/requisitos.js`` is a verbatim copy of a
real ``requisitos.js`` response body (originally pulled from a HAR capture of
the live site) fed to the pure parser. Every branch of ``requisitos.py`` is
exercised here, including each ``ValueError`` path.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ensmcp.domain.models import ApplicabilityLevel
from ensmcp.scraping.requisitos import parse_requisitos
from tests.support import check

_REQUISITOS_JS = (Path(__file__).resolve().parent / "fixtures" / "requisitos.js").read_text(
    encoding="utf-8"
)


def test_parse_requisitos_extracts_descriptions_for_all_73_real_measures() -> None:
    descriptions = parse_requisitos(_REQUISITOS_JS).descriptions

    check(len(descriptions) == 73)
    check(descriptions["org.1"].startswith("Categoría Básica"))
    check("¿La PSI" in descriptions["org.1"])
    check("mp.s.4" in descriptions)
    check(all(value.strip() for value in descriptions.values()))


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("not a js var declaration", "does not declare"),
        ("var x='unclosed literal", "not closed"),
        ("var x='no sections here'", "no section-title"),
        (
            "var x='<div class=\"section-title\"><span>Sin código</span></div>'",
            "no \\(code\\) group",
        ),
        (
            "var x='<div class=\"section-title\"><span>Título (nope)</span></div>'",
            "not an ENS measure code",
        ),
        # ')' before '(' has both characters present, so the two rfind lookups
        # succeed and only the close < open check catches it. Without that
        # check the reversed slice yields "", which would surface as the far
        # more confusing "code '' is not an ENS measure code".
        (
            "var x='<div class=\"section-title\"><span>Título ) org.1 (</span></div>'",
            "no \\(code\\) group",
        ),
    ],
    ids=[
        "no-var-declaration",
        "unclosed-string-literal",
        "no-section-titles",
        "section-title-without-a-code-group",
        "code-is-not-an-ens-measure-code",
        "parentheses-in-reverse-order",
    ],
)
def test_parse_requisitos_rejects_a_malformed_source(source: str, message: str) -> None:
    """One row per ValueError path of the parser — every raise is covered here."""
    with pytest.raises(ValueError, match=message):
        parse_requisitos(source)


def test_parse_requisitos_accepts_a_title_that_is_only_the_code() -> None:
    # The opening parenthesis at index 0 is a real position, not "absent".
    # Guarding with `open_idx == -1` (rather than a falsiness check) is what
    # keeps a title carrying no text before its code from being rejected.
    source = "var x='<div class=\"section-title\"><span>(org.1)</span></div>cuerpo'"

    check(parse_requisitos(source).descriptions == {"org.1": "cuerpo"})


def test_parse_requisitos_keeps_the_last_block_up_to_the_end_of_the_literal() -> None:
    # Two measures: the second block's body runs to the closing quote (no
    # trailing section-title), exercising the len(html) fallback branch.
    source = (
        "var x='"
        '<div class="section-title"><span>Uno (org.1)</span></div>primer bloque'
        '<div class="section-title"><span>Dos (org.2)</span></div>segundo bloque\''
    )
    descriptions = parse_requisitos(source).descriptions

    check(set(descriptions) == {"org.1", "org.2"})
    check(descriptions["org.1"] == "primer bloque")
    check(descriptions["org.2"] == "segundo bloque")


def test_parse_requisitos_rejects_a_duplicate_measure_block() -> None:
    source = (
        "var x='"
        '<div class="section-title"><span>Uno (org.1)</span></div>primero'
        '<div class="section-title"><span>Dos (org.1)</span></div>segundo\''
    )

    with pytest.raises(ValueError, match=r"duplicate measure block.*org\.1"):
        parse_requisitos(source)


def test_parses_every_audit_question_of_the_real_asset() -> None:
    # The counts are what catch a parser that starts merging or dropping
    # questions after a site rebuild — they come from the real capture.
    requirements = parse_requisitos(_REQUISITOS_JS).audit_requirements

    total = [r for per_measure in requirements.values() for r in per_measure]
    check(len(requirements) == 73, f"esperaba 73 medidas, hay {len(requirements)}")
    check(len(total) == 430, f"esperaba 430 requisitos, hay {len(total)}")
    check(sum(1 for r in total if r.essential) == 136)
    check(sum(1 for r in total if r.note) == 91)
    check(all(per_measure for per_measure in requirements.values()), "una medida sin requisitos")


def test_an_audit_question_carries_its_level_and_essential_flag() -> None:
    requirements = parse_requisitos(_REQUISITOS_JS).audit_requirements

    first = requirements["org.1"][0]
    check(first.position == 0)
    check(first.code == "1.1")
    check(first.level is ApplicabilityLevel.BASICO)
    check(first.essential is True, "1.1 de org.1 está marcada essential en el sitio")
    check(first.question.startswith("¿La PSI de la organización ha sido aprobada"))
    check(first.note == "", "una pregunta sin nota trae cadena vacía, no None")


def test_an_audit_question_keeps_its_clarifying_note() -> None:
    requirements = parse_requisitos(_REQUISITOS_JS).audit_requirements

    with_note = next(r for r in requirements["org.1"] if r.note)
    check(with_note.code == "2.1")
    check(with_note.note.startswith("NOTA: En algunos organismos"))
    check(with_note.note.endswith("decreto de estructura."), f"nota truncada: {with_note.note!r}")


def test_a_repeated_printed_code_does_not_collapse_requirements() -> None:
    # op.acc.5's numbering restarts inside the same level: five separate "1.1"
    # among its fourteen questions. A parser keyed by code would silently keep
    # one and lose four, which is why position is the identity.
    requirements = parse_requisitos(_REQUISITOS_JS).audit_requirements

    op_acc_5 = requirements["op.acc.5"]
    check(len(op_acc_5) == 14, f"esperaba 14 requisitos, hay {len(op_acc_5)}")
    check(sum(1 for r in op_acc_5 if r.code == "1.1") == 5, "los cinco '1.1' deben sobrevivir")
    check(len({r.position for r in op_acc_5}) == 14, "las posiciones deben ser todas distintas")
    check([r.position for r in op_acc_5] == list(range(14)), "y consecutivas, en orden de lectura")


def test_the_three_levels_are_all_recognised() -> None:
    requirements = parse_requisitos(_REQUISITOS_JS).audit_requirements

    levels = {r.level for per_measure in requirements.values() for r in per_measure}
    check(levels == set(ApplicabilityLevel), f"faltan niveles: {levels}")


def test_an_unknown_level_header_is_refused() -> None:
    # Silently skipping it would drop every question under it; guessing a level
    # would put questions in the wrong column of an audit scope.
    source = (
        'var requisitos808=\'<div class="section-title"><span>T (org.1)</span></div>'
        '<div class="tittle-measure">Categoría Suprema</div>\''
    )
    with pytest.raises(ValueError, match="unknown audit level header"):
        parse_requisitos(source)


def test_a_requirement_before_the_first_level_header_is_refused() -> None:
    # La otra mitad del test de arriba. Un `requirement` puesto **antes** del
    # primer «Categoría …» no lo lee nadie: el parser arranca en cada cabecera y
    # lee hacia delante, así que ése no tiene nivel bajo el que archivarse y
    # desaparecía sin una queja. Y en silencio de verdad: cero preguntas de más
    # no es anómalo —tres medidas reales no tienen ninguna en básica— así que
    # `alcance_auditoria` habría dicho que hay menos que preguntar de lo que
    # pregunta la guía.
    source = (
        'var requisitos808=\'<div class="section-title"><span>T (org.1)</span></div>'
        '<div class="requirement"><div class="q"><span class="code">1.1</span>¿Suelta?</div></div>'
        '<div class="tittle-measure">Categoría Básica</div>'
        '<div class="requirement"><div class="q"><span class="code">1.1</span>¿La buena?</div>'
        "</div>'"
    )
    with pytest.raises(ValueError, match=r"1 requirement.*before the first"):
        parse_requisitos(source)


def test_a_requirement_in_a_block_with_no_level_header_at_all_is_refused() -> None:
    # El mismo fallo llevado al extremo: sin ninguna cabecera, **todos** los
    # requisitos son huérfanos. Un bloque sin cabeceras y sin requisitos sí es
    # legítimo —así son los que no son de medida— y no se rechaza.
    orphan = (
        'var requisitos808=\'<div class="section-title"><span>T (org.1)</span></div>'
        '<div class="requirement"><div class="q"><span class="code">1.1</span>¿Suelta?</div>'
        "</div>'"
    )
    empty = "var requisitos808='<div class=\"section-title\"><span>T (org.1)</span></div>texto'"

    with pytest.raises(ValueError, match=r"1 requirement.*before the first"):
        parse_requisitos(orphan)
    check(
        parse_requisitos(empty).audit_requirements == {"org.1": ()},
        "un bloque sin preguntas es legítimo",
    )


def test_every_requirement_div_of_the_real_asset_is_accounted_for() -> None:
    # Reconciliación completa contra el asset: cada `<div class="requirement">`
    # o se publica, o se descarta por una razón concreta. Nada puede caerse en
    # silencio, que es el modo de fallo de un parser que trocea por marcadores.
    source = _REQUISITOS_JS
    divs = source.count('<div class="requirement">')
    publicados = sum(len(per) for per in parse_requisitos(source).audit_requirements.values())

    check(divs == 431, f"el asset trae {divs} divs 'requirement', esperaba 431")
    # El único descarte es el "1.4" de mp.info.1, que no lleva pregunta.
    check(publicados == divs - 1, f"publicados {publicados} de {divs}: hay descartes sin explicar")


def test_the_questions_that_name_their_own_reinforcement() -> None:
    # El sitio no marca a qué refuerzo pertenece cada grupo de preguntas, pero
    # el texto de once de ellas lo dice ("... Relativo a la medida Op.mon.3.r5").
    # Se pina porque el README lo afirma: si un rebuild del sitio las quitara o
    # las cambiara, esa afirmación se quedaría falsa en silencio.
    requirements = parse_requisitos(_REQUISITOS_JS).audit_requirements
    nombrado = re.compile(r"Relativo a (?:la medida )?([A-Za-z][\w.]*\.r\d+)", re.IGNORECASE)

    hits: list[tuple[str, str]] = []
    for code, per_measure in requirements.items():
        for requirement in per_measure:
            match = nombrado.search(requirement.question)
            if match is not None:
                hits.append((code, match.group(1)))

    check(len(hits) == 11, f"esperaba 11 preguntas, hay {len(hits)}")
    check(
        {code for code, _ in hits} == {"op.exp.3", "op.cont.2", "op.mon.2", "op.mon.3"},
        f"medidas: {sorted({code for code, _ in hits})}",
    )
    # Y el refuerzo que nombran es siempre uno de su propia medida.
    for code, referencia in hits:
        check(referencia.lower().startswith(f"{code}.r"), f"{code} apunta a {referencia}")


def test_no_parsed_requirement_carries_an_empty_question() -> None:
    # Regresión sobre el asset real. El sitio sirve para el "1.4" de mp.info.1
    # un `<div class="q"><span class="code">1.4</span></div>`: el código y nada
    # más. La guarda de al lado sólo miraba que el div *faltara*, así que ese
    # pasaba y el corpus publicaba un requisito de auditoría cuya pregunta era
    # la cadena vacía — contaba para el temario y se renderizaba como un hueco.
    requirements = parse_requisitos(_REQUISITOS_JS).audit_requirements

    vacias = [
        f"{code} pos={r.position} code={r.code!r}"
        for code, per_measure in requirements.items()
        for r in per_measure
        if not r.question.strip()
    ]
    check(not vacias, f"requisitos con pregunta vacía: {vacias}")
    # El hueco sigue siendo visible por la numeración que imprime el sitio: se
    # descarta la entrada, no se falsea la secuencia.
    codes = [r.code for r in requirements["mp.info.1"]]
    check(codes == ["1.1", "1.2", "1.3", "1.5", "1.6", "1.7", "1.8", "1.9"], f"códigos: {codes}")
    check(
        [r.position for r in requirements["mp.info.1"]] == list(range(8)),
        "las posiciones tienen que seguir siendo consecutivas",
    )


def test_a_question_div_holding_only_its_code_yields_nothing() -> None:
    # La forma mínima del caso anterior, aislada.
    source = (
        'var requisitos808=\'<div class="section-title"><span>T (org.1)</span></div>'
        '<div class="tittle-measure">Categoría Básica</div>'
        '<div class="requirement"><div class="q"><span class="code">1.4</span></div></div>\''
    )
    check(parse_requisitos(source).audit_requirements == {"org.1": ()})


def test_a_requirement_div_with_no_question_yields_nothing() -> None:
    # The sibling "requirement-text" box (the auditor's own answer field) is
    # always empty and must not become a phantom requirement.
    source = (
        'var requisitos808=\'<div class="section-title"><span>T (org.1)</span></div>'
        '<div class="tittle-measure">Categoría Básica</div>'
        '<div class="requirement"><div class="requirement-text"><p></p></div></div>\''
    )
    check(parse_requisitos(source).audit_requirements == {"org.1": ()})


def test_a_run_of_spaces_inside_the_html_collapses_like_any_other() -> None:
    # Aplanar trozo a trozo con `.strip()` deja intactas las rachas que van
    # *dentro* de un trozo. El sitio sirve dos, en op.exp.4 y mp.info.3, así que
    # esas medidas publicaban una descripción que nadie puede teclear: buscar
    # "NOTA: La priorización" con un espacio no encontraba nada, en un corpus
    # que sí contiene la frase.
    source = (
        'var requisitos808=\'<div class="section-title"><span>Título (org.1)</span></div>'
        "<p>NOTA:  dos espacios</p><p>y  tres   más</p>'"
    )

    check(
        parse_requisitos(source).descriptions == {"org.1": "NOTA: dos espacios y tres más"},
        "no colapsó",
    )


def test_the_real_corpus_has_no_double_spaces_left() -> None:
    # Guarda sobre el asset real, no sobre un fixture: es donde estaban las dos.
    parsed = parse_requisitos(_REQUISITOS_JS)
    descriptions = parsed.descriptions
    requirements = parsed.audit_requirements

    offenders = [code for code, text in descriptions.items() if re.search(r"[ ]{2,}", text)]
    check(not offenders, f"descripciones con espacios múltiples: {offenders}")
    check(
        not [
            (code, item.position)
            for code, items in requirements.items()
            for item in items
            if re.search(r"[ ]{2,}", item.question + item.note)
        ],
        "un requisito conserva espacios múltiples",
    )


def test_a_question_ending_in_an_ampersand_is_not_swallowed_whole() -> None:
    """``HTMLParser.feed`` sin ``close`` se come la cola que aún no sabe leer.

    Y no sólo la cola: ante un ``&`` suelto —que el parser tiene que suponer
    que puede ser el principio de ``&amp;``— se guarda **todo lo que va desde
    la última etiqueta**. La pregunta salía como cadena vacía, y una pregunta
    vacía la descarta ``_requirements_in``, así que desaparecía del alcance de
    la auditoría sin una queja: exactamente lo que pasaba con el "1.4" en
    blanco de ``mp.info.1``, que este parser ya tiene rama para rechazar.
    """
    source = (
        "var requisitos808='"
        '<div class="section-title"><span>T (org.1)</span></div>'
        '<div class="tittle-measure">Categoría Básica</div>'
        '<div class="requirement"><div class="requirement-question audit ">'
        '<div class="q"><span class="code">1.1</span>¿Se cubre I+D&</div></div></div>'
        "'"
    )

    parsed = parse_requisitos(source)
    requirements = parsed.audit_requirements["org.1"]

    check(len(requirements) == 1, f"la pregunta se perdió entera: {requirements}")
    check(
        requirements[0].question == "¿Se cubre I+D&",
        f"la pregunta salió como {requirements[0].question!r}",
    )
    check("I+D&" in parsed.descriptions["org.1"], "la descripción también la perdió")
