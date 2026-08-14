"""Tests for the CCN-STIC 808 data the package ships.

Run against the committed `guia_808.json` — the file the server actually
serves — because the question worth asking of a heuristic PDF extraction is
whether what shipped is complete and clean, not whether the parser compiles.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from ensmcp import package_data
from ensmcp.domain.queries import code_order
from ensmcp.guia.codec import SCHEMA_VERSION, load
from ensmcp.guia.loader import load_packaged_guide
from ensmcp.snapshot.repository import SnapshotRepository
from tests.support import check, check_invalid_json_shape, require, set_json_value

_GUIA = load_packaged_guide()
_EVIDENCE_BY_CODE = {item.measure_code: item.evidence for item in _GUIA.measure_evidence}


def _minimal_guide_document() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "CCN-STIC-808",
        "articles": [
            {
                "reference": "Art. 28",
                "title": "Declaración de aplicabilidad",
                "evidence": ["Una declaración"],
                "questions": [{"reference": "Art. 28.2", "question": "¿Está suscrita?"}],
            }
        ],
        "measure_evidence": [{"measure_code": "org.1", "evidence": ["Una política"]}],
    }


def test_the_shipped_guide_is_complete() -> None:
    # The counts of the real §6.1 and §6.2. A silent half-extraction is the
    # failure mode of parsing a PDF, so the numbers are the guard.
    questions = [q for article in _GUIA.articles for q in article.questions]
    bullets = [b for item in _GUIA.measure_evidence for b in item.evidence]

    check(len(_GUIA.articles) == 6, f"esperaba 6 artículos, hay {len(_GUIA.articles)}")
    check(len(questions) == 14, f"esperaba 14 preguntas, hay {len(questions)}")
    check(
        len(_GUIA.measure_evidence) == 73, f"esperaba 73 medidas, hay {len(_GUIA.measure_evidence)}"
    )
    check(len(bullets) == 365, f"esperaba 365 evidencias, hay {len(bullets)}")
    check("CCN-STIC-808" in _GUIA.source, "el fichero debe llevar su atribución")


def test_every_article_carries_a_reference_a_title_and_questions() -> None:
    for article in _GUIA.articles:
        check(article.reference.strip() != "", f"artículo sin referencia: {article.title!r}")
        check(article.title.strip() != "", f"artículo sin título: {article.reference!r}")
        check(len(article.questions) > 0, f"artículo sin preguntas: {article.reference!r}")
        check(
            "Aplica: SI" not in article.reference and "Aplica: SI" not in article.title,
            f"restos de la fila de aplicabilidad en {article.reference!r}",
        )


def test_the_declaration_of_applicability_article_is_intact() -> None:
    # Art. 28 es el que un consultor mira primero: si la DdA está firmada y si
    # justifica las exclusiones.
    art_28 = require(next((a for a in _GUIA.articles if a.reference == "Art. 28"), None))

    check(art_28.title == "Declaración de aplicabilidad")
    check(len(art_28.questions) == 5, f"esperaba 5 preguntas, hay {len(art_28.questions)}")
    firmada = require(next((q for q in art_28.questions if "suscrita por el" in q.question), None))
    check(firmada.reference == "Art. 28.2")
    check(
        firmada.question.endswith("reflejadas?"), f"pregunta truncada: {firmada.question[-40:]!r}"
    )
    check("Declaración de Aplicabilidad." in art_28.evidence)


def test_no_question_carries_the_next_articles_title() -> None:
    # Los recuentos que afirma `_check` no ven esto: eran 6 artículos y 14
    # preguntas antes y después, pero cinco de las seis preguntas finales
    # acababan con el título del artículo siguiente pegado detrás ("...de
    # medidas de seguridad? Declaración de aplicabilidad"), y una arrastró
    # también su referencia. La frontera entre artículos no la marca nada salvo
    # una línea en blanco, así que equivocarla no rompe ningún número.
    etiquetas = {a.reference for a in _GUIA.articles} | {a.title for a in _GUIA.articles}

    for article in _GUIA.articles:
        for question in article.questions:
            check(
                question.question.rstrip().endswith(("?", ".", ":", ")")),
                f"pregunta cortada o contaminada en {article.reference}: "
                f"...{question.question[-55:]!r}",
            )
            ajenas = [
                etiqueta
                for etiqueta in etiquetas
                if len(etiqueta) > 20
                and etiqueta not in (article.reference, article.title)
                and (etiqueta in question.question or etiqueta in question.reference)
            ]
            check(not ajenas, f"{article.reference} arrastra {ajenas} de otro artículo")


def test_no_extracted_text_carries_page_furniture_or_is_empty() -> None:
    # Lo que delata una extracción sucia: encabezados, pies y viñetas cortadas.
    texts = (
        [b for item in _GUIA.measure_evidence for b in item.evidence]
        + [b for article in _GUIA.articles for b in article.evidence]
        + [q.question for article in _GUIA.articles for q in article.questions]
    )
    check(len(texts) > 0)
    for text in texts:
        check(text.strip() != "", "texto vacío en la guía")
        check("CCN-STIC-808" not in text, f"encabezado colado: {text[:60]!r}")
        check("Centro Criptológico Nacional" not in text, f"pie colado: {text[:60]!r}")
        check("☐" not in text, f"casilla sin limpiar: {text[:60]!r}")


def test_every_measure_in_the_guide_exists_in_the_snapshot() -> None:
    # La prueba de integridad del join: si la extracción inventase o deformase
    # un código, las evidencias no casarían con ninguna medida.
    real = {measure.code for measure in SnapshotRepository.from_package_data().measures}
    extracted = set(_EVIDENCE_BY_CODE)

    check(extracted == real, f"descuadre: {extracted ^ real}")


def test_the_evidence_order_matches_the_order_every_other_tool_serves() -> None:
    """La unión que el README promete, fila a fila.

    ``evidencias_auditoria`` se lee al lado de ``alcance_auditoria`` y
    ``declaracion_aplicabilidad``, que sirven las medidas en el orden de la
    tabla. Las dos listas tienen que recorrer las 73 igual.

    Este test **ordenaba también la lista de la tabla** antes de compararla, o
    sea que comprobaba que un orden coincide consigo mismo y daba verde con las
    73 filas descolocadas: el codec ordenaba por código, y alfabéticamente
    ``mp`` va antes que ``op`` y ``op`` antes que ``org``, justo al revés que el
    Anexo II. Comparar tal cual es lo único que responde a la pregunta.
    """
    table_order = [measure.code for measure in SnapshotRepository.from_package_data().measures]
    evidence_order = [item.measure_code for item in _GUIA.measure_evidence]

    check(
        table_order == evidence_order,
        f"el orden de las evidencias no coincide con el de las medidas: "
        f"{[pair for pair in zip(table_order, evidence_order, strict=True) if pair[0] != pair[1]]}",
    )


def test_the_evidence_is_not_served_in_alphabetical_order() -> None:
    # La otra mitad del test de arriba: que el orden bueno y el alfabético sean
    # distintos es lo que hace que aquél diga algo. Si algún día coincidieran,
    # este test cae y avisa de que el de arriba dejó de discriminar.
    codes = [item.measure_code for item in _GUIA.measure_evidence]

    check(codes != sorted(codes, key=code_order), "el orden alfabético ya no distingue nada")
    check(codes[0] == "org.1", f"el Anexo II abre en org.1, no en {codes[0]}")


def test_org_1_evidence_reads_like_the_guide() -> None:
    evidence = _EVIDENCE_BY_CODE["org.1"]

    check(len(evidence) == 6, f"esperaba 6 evidencias, hay {len(evidence)}")
    check(evidence[0].startswith("Documento formal conteniendo la política de Seguridad"))


def test_an_empty_guide_file_is_refused_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    text = json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "source": "CCN-STIC-808",
            "articles": [],
            "measure_evidence": [],
        }
    )
    monkeypatch.setattr(package_data, "read", lambda _filename: text)

    with pytest.raises(ValueError, match="guia_808 is incomplete"):
        load_packaged_guide()


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("source",), ""),
        (("articles", 0, "title"), ""),
        (("articles", 0, "evidence", 0), ""),
        (("articles", 0, "questions", 0, "question"), ""),
        (("measure_evidence", 0, "evidence", 0), ""),
    ],
)
def test_an_incomplete_guide_file_is_refused(
    monkeypatch: pytest.MonkeyPatch, path: tuple[str | int, ...], value: object
) -> None:
    document = _minimal_guide_document()
    set_json_value(document, path, value)
    monkeypatch.setattr(package_data, "read", lambda _filename: json.dumps(document))

    with pytest.raises(ValueError, match="guia_808 is incomplete"):
        load_packaged_guide()


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("source",), 3),
        (("articles", 0, "reference"), 3),
        (("articles", 0, "title"), 3),
        (("articles", 0, "evidence", 0), 3),
        (("articles", 0, "questions", 0, "reference"), 3),
        (("articles", 0, "questions", 0, "question"), 3),
        (("measure_evidence", 0, "measure_code"), 3),
        (("measure_evidence", 0, "evidence", 0), 3),
    ],
)
def test_load_refuses_wrong_scalar_types(path: tuple[str | int, ...], value: object) -> None:
    document = _minimal_guide_document()
    set_json_value(document, path, value)

    with pytest.raises(ValueError, match=r"guia_808 declares schema version .* shape"):
        load(json.dumps(document))


@pytest.mark.parametrize(
    ("collection", "identity"),
    [("articles", "article reference"), ("measure_evidence", "measure evidence code")],
)
def test_load_refuses_duplicate_root_identifiers(collection: str, identity: str) -> None:
    document = _minimal_guide_document()
    document[collection].append(document[collection][0])

    with pytest.raises(ValueError, match=r"guia_808 declares schema version .* shape") as excinfo:
        load(json.dumps(document))

    check(f"duplicate {identity}" in str(excinfo.value))


def test_load_allows_duplicate_question_references() -> None:
    document = _minimal_guide_document()
    questions = document["articles"][0]["questions"]
    questions.append({**questions[0], "question": "¿Está publicada?"})

    guide = load(json.dumps(document))

    check([question.reference for question in guide.articles[0].questions] == ["Art. 28.2"] * 2)


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ({"schema_version": SCHEMA_VERSION}, "source"),
        (
            {
                "schema_version": SCHEMA_VERSION,
                "source": "",
                "articles": [{"reference": "Art. 28"}],
                "measure_evidence": [],
            },
            "title",
        ),
        (
            {
                "schema_version": SCHEMA_VERSION,
                "source": "",
                "articles": [],
                "measure_evidence": {"org.1": ["un papel"]},
            },
            "TypeError",
        ),
        (
            {
                "schema_version": SCHEMA_VERSION,
                "source": "",
                "articles": [
                    {
                        "reference": "Art. 28",
                        "title": "Declaración de aplicabilidad",
                        "evidence": "un papel",
                        "questions": [],
                    }
                ],
                "measure_evidence": [],
            },
            "evidence for Art. 28 is a str",
        ),
        (
            {
                "schema_version": SCHEMA_VERSION,
                "source": "",
                "articles": [],
                "measure_evidence": [{"measure_code": "org.1", "evidence": "un papel"}],
            },
            "evidence for org.1 is a str",
        ),
    ],
    ids=[
        "nothing-but-the-version",
        "short-article",
        "measure-evidence-as-a-mapping",
        "article-evidence-as-a-string",
        "measure-evidence-as-a-string",
    ],
)
def test_load_refuses_a_file_that_declares_the_version_without_the_shape(
    document: dict[str, object], expected: str
) -> None:
    # Mismo contrato que el codec del snapshot: la versión correcta no implica
    # la forma correcta, y antes esto reventaba con un `KeyError: 'title'` que
    # no decía ni de qué fichero hablaba.
    check_invalid_json_shape(load, document, expected)


def test_no_question_stole_a_word_from_its_own_reference() -> None:
    # Guarda a nivel de dato de un fallo que ningún recuento veía. La celda de
    # la izquierda de la tabla también se parte en varias líneas, y en una de
    # ellas la maquetación deja tres espacios *dentro* de la propia celda
    # ("segunda   del"). Leído como dos columnas, ese "del" se empalmaba en
    # mitad de la pregunta —"(ITS) del y guías de seguridad"— y desaparecía de
    # la referencia.
    #
    # Lo que lo delata sin mirar el PDF: la referencia de la pregunta sale del
    # cuerpo de la tabla y la del artículo del bloque de título, por caminos
    # distintos. Si la de la pregunta es la del artículo **con palabras caídas**
    # —subsecuencia estricta— es que la tabla perdió justo lo que se coló en el
    # texto. Una referencia legítimamente distinta ("Art. 28.2" frente a
    # "Art. 28") no es subsecuencia de la suya, así que no salta.
    for article in _GUIA.articles:
        for question in article.questions:
            words = iter(article.reference.split())
            dropped = all(word in words for word in question.reference.split())
            check(
                not dropped or question.reference == article.reference,
                f"{question.reference!r} es {article.reference!r} con palabras caídas: "
                "la referencia de la tabla perdió lo que se coló en la pregunta",
            )


def test_the_shipped_guide_says_which_edition_it_came_from() -> None:
    # La otra mitad, sobre el dato que de verdad se distribuye: si alguien
    # regenera el fichero con una guía cuya portada no se lee, `parse_source`
    # revienta — pero si alguien vuelve a fijar la atribución a mano, esto es lo
    # que lo caza.
    source = _GUIA.source

    check("CCN-STIC-808" in source, source)
    check(
        re.search(r"edición de \w+ 20\d{2}", source) is not None,
        f"la atribución no identifica la edición: {source!r}",
    )
