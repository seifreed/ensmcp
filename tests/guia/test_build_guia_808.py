"""Tests for the §6.1 article parser of ``scripts/build_guia_808.py``.

El script no viaja en el paquete —sólo regenera los ficheros que sí viajan— y
por eso se quedó sin tests y fuera de la cobertura. Pero es parsing heurístico
sobre el texto de un PDF, que es donde de verdad se esconden los fallos: la
frontera entre un artículo y el siguiente no la marca nada salvo una línea en
blanco, y equivocarla no rompe ningún recuento, sólo contamina el texto.

Estos tests trabajan sobre una sección sintética con la maquetación real de
``pdftotext -layout``: dos columnas separadas por rachas de tres o más
espacios, la fila de aplicabilidad, y la tabla "Aspectos a evaluar".
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from build_guia_808 import (
    _ARTICLES_HEADING,
    _MEASURES_HEADING,
    _ArticleDict,
    _check,
    _QuestionDict,
    _split_at_column,
    build,
    parse_articles,
    parse_measure_evidence,
    parse_source,
)

from tests.support import check, require

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_guia_808.py"

_QUESTION = _QuestionDict(reference="Art. 1", question="¿Se cumple?")


# Dos artículos seguidos, que es la forma en la que aparecía el fallo: el
# título del segundo vive **encima** de su fila "Aplica: SI", así que cae
# dentro del cuerpo del primero si el corte se hace en la fila y no en el
# título.
_SECTION = """
Art. 28                              Declaración de aplicabilidad
                                                                     Aplica: SI ☐ NO ☐

Propuestas de evidencias

☐ Declaración de Aplicabilidad.

Aspectos a evaluar                                                   Cumple

Art. 28.2    ¿La DdA está suscrita por el responsable?               ☐ SI ☐ NO ☐ EN PROCESO

Art. 30                              Perfiles de cumplimiento
                                                                     Aplica: SI ☐ NO ☐

Aspectos a evaluar                                                   Cumple

Art. 30.1    ¿Se ha acogido a un perfil de cumplimiento?             ☐ SI ☐ NO ☐ EN PROCESO
"""


def test_an_article_stops_before_the_next_ones_title() -> None:
    # La regresión. El corte se hacía en la fila "Aplica: SI" del artículo
    # siguiente, así que su bloque de título quedaba dentro del cuerpo del
    # anterior y `_questions` se lo pegaba como continuación de la última
    # pregunta: "...responsable? Perfiles de cumplimiento". Esas mismas líneas
    # las recoge `_title_block` para el artículo al que pertenecen, o sea que
    # se estaban usando dos veces.
    primero, segundo = parse_articles(_SECTION)

    ultima = primero["questions"][-1]["question"]
    check(
        ultima.endswith("?"),
        f"la última pregunta del primer artículo acaba mal: ...{ultima[-45:]!r}",
    )
    check(segundo["title"] not in ultima, f"se coló el título del siguiente: {ultima!r}")
    check(segundo["reference"] not in ultima, f"se coló la referencia del siguiente: {ultima!r}")


def test_both_articles_keep_their_own_reference_title_and_questions() -> None:
    # El corte nuevo no puede haberse comido nada del artículo al que pertenece.
    primero, segundo = parse_articles(_SECTION)

    check(primero["reference"] == "Art. 28", f"referencia: {primero['reference']!r}")
    check(primero["title"] == "Declaración de aplicabilidad", f"título: {primero['title']!r}")
    check(primero["evidence"] == ["Declaración de Aplicabilidad."], f"{primero['evidence']}")
    check(len(primero["questions"]) == 1, f"preguntas: {primero['questions']}")

    check(segundo["reference"] == "Art. 30", f"referencia: {segundo['reference']!r}")
    check(segundo["title"] == "Perfiles de cumplimiento", f"título: {segundo['title']!r}")
    check(len(segundo["questions"]) == 1, f"preguntas: {segundo['questions']}")


def test_a_question_keeps_its_own_reference() -> None:
    # La referencia se acumulaba igual que el texto: la del Art. 28 acabó
    # siendo "Apartado 4.4 ... del RD 311/2022. Art. 30", con la del siguiente
    # artículo pegada detrás.
    primero, _ = parse_articles(_SECTION)

    pregunta = require(primero["questions"][-1])
    check(pregunta["reference"] == "Art. 28.2", f"referencia: {pregunta['reference']!r}")


def test_the_last_article_of_the_section_runs_to_the_end() -> None:
    # No hay título siguiente en el que cortar, así que el cuerpo llega al
    # final: la rama que el corte nuevo deja intacta.
    _, segundo = parse_articles(_SECTION)

    check(segundo["questions"][-1]["question"].endswith("cumplimiento?"))


# La otra forma del mismo fallo, y la que sobrevivía: la referencia de la
# izquierda **también** se parte en varias líneas, y en una de ellas la
# maquetación deja tres espacios *dentro* de la propia celda ("segunda   del").
# Es literalmente lo que hace la guía con "Disposición adicional segunda del
# ENS" en sus tres preguntas.
_WRAPPED_REFERENCE_SECTION = """
Disposición adicional segunda        Desarrollo del ENS
                                                                     Aplica: SI ☐ NO ☐

Aspectos a evaluar                                                   Cumple

Disposición     ¿La organización conoce la relación de las           ☐ SI
adicional       Instrucciones Técnicas de Seguridad (ITS)            ☐ NO
segunda     del y guías de seguridad que le son de
ENS             aplicación?
"""


def test_a_reference_that_wraps_with_a_gap_inside_it_stays_in_its_own_column() -> None:
    # La regresión. Contando columnas, "segunda   del ..." parece "referencia +
    # pregunta", así que "del" se leía como el principio de la continuación y se
    # empalmaba en mitad de la frase — "(ITS) del y guías de seguridad" — a la
    # vez que la referencia lo perdía. Lo que decide es la columna, no el
    # recuento de campos: "del" empieza a la izquierda de donde abrió la
    # pregunta, luego es de la celda de la izquierda.
    article = require(next(iter(parse_articles(_WRAPPED_REFERENCE_SECTION)), None))
    question = require(next(iter(article["questions"]), None))

    check(
        question["question"] == "¿La organización conoce la relación de las Instrucciones "
        "Técnicas de Seguridad (ITS) y guías de seguridad que le son de aplicación?",
        f"la pregunta quedó: {question['question']!r}",
    )
    # Y la referencia recupera la palabra que la pregunta le estaba robando.
    check(question["reference"] == "Disposición adicional segunda del ENS", question["reference"])


# La guía marca sus viñetas con dos caracteres distintos para lo mismo: BALLOT
# BOX (U+2610) y WHITE SQUARE (U+25A1). Hoy las evidencias usan sólo el primero,
# pero el segundo aparece 439 veces en el documento.
_MIXED_BULLET_SECTION = """
Art. 28                              Declaración de aplicabilidad
                                                                     Aplica: SI ☐ NO ☐

Propuestas de evidencias

☐ Primera evidencia, con la caja de siempre.
□ Segunda evidencia, con la otra caja.

Aspectos a evaluar                                                   Cumple

Art. 28.2    ¿La DdA está suscrita por el responsable?               ☐ SI ☐ NO ☐ EN PROCESO
"""


def test_a_bullet_marked_with_the_other_box_character_still_opens_an_entry() -> None:
    # Sin aceptar los dos caracteres, la segunda viñeta no abre entrada: cae en
    # la rama de "continuación" y se pega entera al final de la primera. Y en
    # silencio, porque `_check` sólo mira que ninguna esté vacía — y una viñeta
    # con otra pegada detrás no lo está.
    article = require(next(iter(parse_articles(_MIXED_BULLET_SECTION)), None))

    check(
        article["evidence"]
        == [
            "Primera evidencia, con la caja de siempre.",
            "Segunda evidencia, con la otra caja.",
        ],
        f"las evidencias quedaron: {article['evidence']}",
    )


# Una fila "Cumple" que además arrastre texto a su derecha. La guarda de
# `_bullets` que descarta la línea entera no la ve —no es *sólo* casillas—, así
# que lo único que impide que "☐ SI ..." abra una viñeta es `_BULLET`: su mirada
# adelante rechaza SI/NO/EN PROCESO, y el `\S` que abre el grupo es lo que
# impide que el retroceso la esquive (con `(.+)`, `\s*` retrocede a cero, la
# mirada se hace sobre " SI" —que no empieza por SI— y pasa, así que la viñeta
# salía como " SI ...").
_CHECKBOX_ROW_SECTION = """
Art. 28                              Declaración de aplicabilidad
                                                                     Aplica: SI ☐ NO ☐

Propuestas de evidencias

☐ La única evidencia de verdad.
☐ SI ☐ NO ☐ EN PROCESO   Observaciones del auditor
□ SI □ NO □ EN PROCESO   Y la misma con la otra caja

Aspectos a evaluar                                                   Cumple

Art. 28.2    ¿La DdA está suscrita por el responsable?               ☐ SI ☐ NO ☐ EN PROCESO
"""


def test_a_cumple_row_never_reaches_the_evidence() -> None:
    """Las dos grafías de la casilla, que son dos guardas distintas.

    La fila con BALLOT BOX la descarta ``_bullets``: la condición era "las
    casillas y **nada más**", así que ésta —con una columna de observaciones a su
    derecha— se colaba y, como ``_BULLET`` no abre viñeta con ella, se pegaba al
    final de la anterior con los ``☐`` incluidos. Salía "La única evidencia de
    verdad. ☐ SI ☐ NO ☐ EN PROCESO Observaciones del auditor", y ``_check`` la
    daba por buena porque sólo mira que no esté vacía.

    La fila escrita **entera** con WHITE SQUARE es la otra grafía de lo mismo, y
    era la que ninguna guarda apartaba: ``_CHECKBOXES`` sólo conocía el BALLOT
    BOX, y lo que había en ``_BULLET`` —una mirada adelante contra
    SI/NO/EN PROCESO— no la aparta, sólo le impide *abrir* viñeta, con lo que
    acaba pegada igual. Tiene que llevar la otra caja en las tres casillas: con
    una sola ``☐`` en la línea la aparta ya la primera guarda y esta mitad queda
    sin ejercitar.
    """
    article = require(next(iter(parse_articles(_CHECKBOX_ROW_SECTION)), None))

    check(
        not any(re.search(r"\bSI\b|\bEN PROCESO\b", item) for item in article["evidence"]),
        f"una fila de casillas se coló como evidencia: {article['evidence']}",
    )
    check(
        article["evidence"][0] == "La única evidencia de verdad.",
        f"las evidencias quedaron: {article['evidence']}",
    )


_STRAY_BOX_SECTION = """
Art. 28                              Declaración de aplicabilidad
                                                                     Aplica: SI ☐ NO ☐

Propuestas de evidencias

☐ Una evidencia de verdad.
☐

Aspectos a evaluar                                                   Cumple

Art. 28.2    ¿La DdA está suscrita por el responsable?               ☐ SI ☐ NO ☐ EN PROCESO
"""


def test_a_stray_checkbox_carries_no_evidence_and_is_dropped() -> None:
    """Un ``☐`` suelto es tan vacío como un renglón en blanco.

    No abría viñeta —no hay texto que capturar— así que caía en la rama de
    continuación y se pegaba al final de la anterior: la evidencia salía como
    "Una evidencia de verdad. ☐", en silencio. Se descarta con el mismo criterio
    que una fila "Cumple": quitadas las casillas, la línea no deja nada.
    """
    article = require(next(iter(parse_articles(_STRAY_BOX_SECTION)), None))

    check(
        article["evidence"] == ["Una evidencia de verdad."],
        f"las evidencias quedaron: {article['evidence']}",
    )


def test_an_article_with_an_empty_bullet_is_refused_like_a_measure() -> None:
    # De las medidas se exige desde siempre que ninguna viñeta esté vacía; de los
    # artículos sólo se miraba que la lista no lo estuviera, aunque salen del
    # mismo `_bullets`. La misma extracción a medias delataba una mitad del
    # fichero y pasaba en la otra.
    article = _ArticleDict(
        reference="Art. 28",
        title="Declaración de aplicabilidad",
        evidence=["", "algo"],
        questions=[_QuestionDict(reference="Art. 28.2", question="¿Está suscrita?")],
    )

    with pytest.raises(ValueError, match="artículo incompleto"):
        _check([article], {})


def test_a_measure_with_an_empty_bullet_is_refused() -> None:
    # La mitad que ya existía, y que tampoco distinguía ningún test: quitar el
    # `any(...)` de la línea de las medidas pasaba la suite entera. `_check` es
    # la función cuyo único trabajo es que nada se pierda en silencio, así que
    # sus dos comprobaciones tienen que estar afirmadas por separado.
    with pytest.raises(ValueError, match=r"evidencias vacías en org\.1"):
        _check([], {"org.1": ["un papel", ""]})


# El bloque de título de un artículo son dos celdas que el layout separa por una
# racha de espacios, y sus continuaciones llegan como líneas de **una** columna:
# la de la izquierda sigue la referencia, la de la derecha sigue el título. Lo
# único que las distingue es la columna en la que empiezan, que es la que abrió
# el título en la primera línea.
#
# Aquí "adicional" continúa la referencia (empieza donde ella) y "ITS y guías de
# seguridad" continúa el título (empieza donde él). Ninguno de los otros
# fixtures tiene una continuación de la *izquierda* con el título ya abierto, y
# por eso la discriminación por columna no estaba afirmada: mutar el `and` a
# `or` o el `is not None` a `is None` pasaba la suite entera.
#
# El `>=` no lo caza este fixture, al contrario de lo que decía aquí: sus dos
# continuaciones empiezan en la columna 0 y en la del título, así que `>=` y `>`
# dan lo mismo para las dos. Sólo las separa una que empiece **exactamente** en
# `title_indent - 2`, que es la tolerancia que el `- 2` concede — y de eso se
# ocupa `test_the_two_column_tolerance_of_a_wrapped_title_is_the_boundary`.
_WRAPPED_TITLE_SECTION = """
Disposición     Desarrollo del ENS.
adicional
                ITS y guías de seguridad
                                                                     Aplica: SI ☐ NO ☐

Aspectos a evaluar                                                   Cumple

Disposición     ¿Se conoce la relación de ITS?                       ☐ SI ☐ NO
"""


def test_a_title_that_wraps_keeps_the_reference_out_of_it() -> None:
    article = require(next(iter(parse_articles(_WRAPPED_TITLE_SECTION)), None))

    check(
        article["title"] == "Desarrollo del ENS. ITS y guías de seguridad",
        f"el título quedó: {article['title']!r}",
    )
    # "adicional" empieza en la columna de la referencia, así que es de la
    # referencia — no del título, por mucho que sea una línea de una sola columna.
    check(
        article["reference"].startswith("Disposición adicional"),
        f"la referencia quedó: {article['reference']!r}",
    )
    check("adicional" not in article["title"], "la referencia se coló dentro del título")


def test_the_sections_are_taken_from_the_body_and_not_from_the_index() -> None:
    """``rfind`` y no ``find``, que es lo que dice el comentario del script.

    La guía imprime los dos encabezados dos veces: una en el índice de la página
    3 y otra donde empieza el apartado. Buscando la primera aparición, el corte
    cae en el índice y ``parse_articles`` se pone a leer la tabla de contenidos.

    Ninguna prueba lo veía porque todas parten de una sección ya recortada a
    mano, donde el encabezado sale una sola vez y ``find`` y ``rfind`` dan lo
    mismo. Sobre el PDF real no dan lo mismo.
    """
    articles, measure_evidence = build(_guide_text_with_a_table_of_contents())

    references = [article["reference"] for article in articles]
    check(references == [f"Art. {n}" for n in range(6)], f"se parsearon {references}")
    check(len(measure_evidence) == 73, f"medidas: {len(measure_evidence)}")


def _guide_text_with_a_table_of_contents() -> str:
    """Un PDF sintético con la forma que obliga a usar ``rfind``.

    Con los recuentos que ``_check`` exige (6 artículos, 14 preguntas, 73
    medidas), porque lo que se afirma es que ``build`` **entero** corta por el
    apartado y no por el índice: recortar la sección a mano en el test, como
    hacen los demás, es justo lo que dejaba pasar el fallo — se saltaba la línea
    bajo prueba.
    """

    def article(number: int) -> str:
        # 9 preguntas en el primero y una en cada uno de los otros cinco: 14.
        questions = "\n".join(
            f"Art. {number}.{q}    ¿Se cumple el punto {q}?           ☐ SI ☐ NO ☐ EN PROCESO"
            for q in range(9 if number == 0 else 1)
        )
        return f"""
Art. {number}                              Título del artículo {number}
                                                                     Aplica: SI ☐ NO ☐

Propuestas de evidencias

☐ Una evidencia del artículo {number}.

Aspectos a evaluar                                                   Cumple

{questions}
"""

    articles = "\n".join(article(n) for n in range(6))
    measures = "\n".join(f"""
Org.{n}                                Medida número {n}
                                                                     Medida aplica: SI ☐ NO ☐

Propuestas de evidencias

☐ Una evidencia de la medida {n}.

Aspectos a evaluar                                                   Cumple
""" for n in range(73))
    # La portada, de la que ``parse_source`` lee la edición, y luego el índice
    # que repite los dos encabezados antes de que empiecen los apartados.
    return (
        "Guía de Seguridad de las TIC\nCCN-STIC 808\nENS. Verificación\n   Abril 2026\n\n"
        f"{_ARTICLES_HEADING} ......... 18\n{_MEASURES_HEADING} ......... 25\n\n"
        f"{_ARTICLES_HEADING}\n{articles}\n{_MEASURES_HEADING}\n{measures}"
    )


def test_a_wrapped_line_is_cut_at_a_space_and_never_mid_word() -> None:
    """La tolerancia de ``_split_at_column``: la maquetación no cuadra al carácter.

    Si el corte cae dentro de una palabra, se camina hacia atrás hasta el
    espacio anterior; cortando en seco, la continuación pierde sus primeras
    letras y el texto sale mutilado en silencio — que es cambiar una corrupción
    por otra.
    """
    line = "Art. 28.2   continuación de la pregunta"
    # La columna 16 cae **dentro** de "continuación", que es lo que hace falta:
    # apuntando al espacio de antes, el bucle que camina hacia atrás no llega a
    # ejecutarse y la prueba pasaría igual sin él.
    check(not line[16].isspace(), "el fixture ya no corta dentro de una palabra")

    left, right = _split_at_column(line, 16)

    check(left == "Art. 28.2", f"la izquierda salió {left!r}")
    check(right == "continuación de la pregunta", f"la derecha se comió letras: {right!r}")


@pytest.mark.parametrize(
    ("articles", "questions", "evidence", "expected"),
    [
        (5, 14, 73, "esperaba 6 artículos"),
        (6, 13, 73, "esperaba 14 preguntas de artículo"),
        (6, 14, 72, "esperaba 73 medidas con evidencias"),
    ],
    ids=["faltan-articulos", "faltan-preguntas", "faltan-medidas"],
)
def test_check_names_the_recuento_that_falla(
    articles: int, questions: int, evidence: int, expected: str
) -> None:
    """Cada recuento de ``_check`` tiene que fallar por su cuenta.

    Es la única defensa contra el riesgo que el propio docstring nombra: un
    parser heurístico sobre un PDF que se traga la mitad del contenido en
    silencio. Y estaba sin probar por separado — se podía desactivar el recuento
    de artículos, o el de preguntas, o el de medidas, y la suite seguía en verde
    porque los otros dos seguían saltando ante una extracción truncada. Por eso
    los tres recuentos se mueven de uno en uno: cada caso deja los otros dos en
    su valor bueno, así que sólo puede saltar el que se está probando.
    """
    extracted = [
        _ArticleDict(
            reference=f"Art. {n}", title="T", evidence=["una evidencia"], questions=[_QUESTION]
        )
        for n in range(articles)
    ]
    extracted[0]["questions"] = [_QUESTION] * (questions - (articles - 1))

    with pytest.raises(ValueError, match=expected):
        _check(extracted, {f"org.{n}": ["una evidencia"] for n in range(evidence)})


def test_check_accepts_the_shape_the_real_guide_has() -> None:
    # La contraparte: con los tres recuentos en su sitio, `_check` no se queja.
    articles = [
        _ArticleDict(
            reference=f"Art. {n}", title="T", evidence=["una evidencia"], questions=[_QUESTION]
        )
        for n in range(6)
    ]
    articles[0]["questions"] = [_QUESTION] * 9

    _check(articles, {f"org.{n}": ["una evidencia"] for n in range(73)})


def test_check_rejects_a_duplicate_article_reference() -> None:
    articles = [
        _ArticleDict(
            reference=f"Art. {n}", title="T", evidence=["una evidencia"], questions=[_QUESTION]
        )
        for n in range(6)
    ]
    articles[0]["questions"] = [_QUESTION] * 9
    articles[-1]["reference"] = articles[0]["reference"]

    with pytest.raises(ValueError, match="referencias de artículo duplicadas"):
        _check(articles, {f"org.{n}": ["una evidencia"] for n in range(73)})


async def test_the_guide_is_decoded_as_utf8_whatever_the_console_says() -> None:
    """El PDF se decodifica igual sea cual sea la codificación de la consola.

    ``sys.stdin`` usa la codificación de la *locale*, y el modo UTF-8 no es el
    predeterminado hasta Python 3.15 (PEP 686). En un Windows recién instalado
    eso es cp1252, así que la tubería de ``pdftotext`` llegaba destrozada — y el
    apartado que se busca lleva tilde y las viñetas son U+2610, con lo que
    ``build`` fallaba con "no se encontraron los apartados 6.1 y 6.2 en el PDF",
    culpando al PDF de un problema de consola.

    Parecía que esto sólo se podía comprobar en Windows, y no:
    ``PYTHONIOENCODING`` fija esa misma codificación en cualquier sistema. Con
    ella puesta a cp1252 y los bytes de siempre en la entrada, el script tiene
    que seguir escribiendo lo mismo. Verificado que distingue: con
    ``sys.stdin.read()`` en su sitio, esto falla con ese mismo mensaje.

    ``create_subprocess_exec`` y no el módulo ``subprocess``, por lo mismo que
    ``test_main``: lista de argumentos, sin shell.
    """
    guide = _guide_text_with_a_table_of_contents()
    with TemporaryDirectory() as temporary:
        output = Path(temporary) / "guia_808.json"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(_SCRIPT),
            str(output),
            env={**os.environ, "PYTHONIOENCODING": "cp1252"},
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        _, errors = await process.communicate(guide.encode("utf-8"))

        check(
            process.returncode == 0,
            f"falló con la consola en cp1252: {errors.decode(errors='replace')[-300:]}",
        )
        document = json.loads(output.read_text(encoding="utf-8"))

    check(len(document["articles"]) == 6, f"artículos: {len(document['articles'])}")
    check(len(document["measure_evidence"]) == 73, f"medidas: {len(document['measure_evidence'])}")
    # La tilde es justo lo que una decodificación por locale rompe.
    check("Título del artículo 0" in json.dumps(document, ensure_ascii=False), "se perdió la tilde")
    # Y de paso el único sitio donde se ejecuta `main()` de verdad: la
    # atribución que escribe tiene que salir de la portada y no de una
    # constante, que es lo que se queda mintiendo en cuanto cambie la edición.
    check(
        document["source"].endswith("edición de Abril 2026 (Centro Criptológico Nacional)"),
        f"la atribución escrita fue {document['source']!r}",
    )


def test_the_attribution_names_the_edition_it_came_from() -> None:
    """La atribución tiene que identificar el documento, y eso incluye la edición.

    En esta serie la edición **es** lo que decide si una guía vale:
    ``guia.codec`` descarta cuatro justamente por ser anteriores al RD 311/2022.
    Lo que se distribuye es la extracción y no el PDF, así que una atribución sin
    edición no viaja en ninguna parte y nadie puede saber contra qué versión de
    la guía está auditando.
    """
    cover = "Guía de Seguridad de las TIC\nCCN-STIC 808\nENS. Verificación\n   Abril 2026\n"

    source = parse_source(cover + "cuerpo del documento\n" * 100)

    check("Abril 2026" in source, f"la atribución no nombra la edición: {source!r}")
    check("CCN-STIC-808" in source, source)
    check("Centro Criptológico Nacional" in source, source)


def test_a_guide_with_no_edition_on_its_cover_is_refused() -> None:
    # Se lee de la portada en vez de fijarse a mano para que siga siendo cierta
    # al regenerar con la guía siguiente. Si no está, hay que enterarse: una
    # constante que miente es peor que un fallo.
    with pytest.raises(ValueError, match="no se encontró la edición"):
        parse_source("Guía de Seguridad de las TIC\nCCN-STIC 808\nsin fecha en la portada\n")


def test_the_edition_is_read_from_the_cover_and_not_from_the_body() -> None:
    """La búsqueda se acota a la portada, y hay que probarlo por el lado que duele.

    Un documento cuya portada **sí** lleva fecha no distingue nada: sea cual sea
    el alcance de la búsqueda, la de la portada es la primera y gana igual. Lo
    que separa las dos versiones es el caso contrario — portada sin fecha y
    cuerpo con una—, que es justo el de la 804 y la 883, que citan dentro una
    edición anterior a la suya. Buscando en todo el texto, ese documento se
    publicaría atribuido a la edición equivocada; acotado, se rechaza.
    """
    body_only = (
        "Guía de Seguridad de las TIC\nCCN-STIC 808\nsin fecha en la portada\n"
        + "\n".join(f"línea {n}" for n in range(60))
        + "\nreferencia a la edición de Junio 2017\n"
    )

    with pytest.raises(ValueError, match="no se encontró la edición"):
        parse_source(body_only)


# La fila "Cumple" de la guía imprime sus casillas como "☐ SI ☐ NO ☐ EN PROCESO",
# con espacio entre la caja y la palabra. `_BULLET` lleva una mirada adelante
# para que esas casillas no abran viñeta de evidencia — y con `(.+)` no servía
# de nada: `\s*` retrocedía a cero espacios, la mirada se hacía sobre " SI" en
# vez de sobre "SI" y pasaba. Es decir, la guarda sólo funcionaba contra la
# grafía sin espacio ("☐SI"), que es justo la que la guía no usa.
_CHECKBOX_IN_EVIDENCE_SECTION = """
Art. 28                              Declaración de aplicabilidad
                                                                     Aplica: SI ☐ NO ☐

Propuestas de evidencias

☐ Declaración de Aplicabilidad.
☐ SI ☐ NO ☐ EN PROCESO

Aspectos a evaluar                                                   Cumple

Art. 28.2    ¿La DdA está suscrita por el responsable?               ☐ SI ☐ NO ☐ EN PROCESO
"""


def test_a_cumple_checkbox_row_does_not_become_an_evidence_bullet() -> None:
    # Sin la guarda, el auditor recibe " SI" como documento a preparar, y `_check`
    # no lo ve: sólo mira que ninguna viñeta esté vacía, y " SI" no lo está.
    article = require(next(iter(parse_articles(_CHECKBOX_IN_EVIDENCE_SECTION)), None))

    check(
        article["evidence"] == ["Declaración de Aplicabilidad."],
        f"las evidencias quedaron: {article['evidence']}",
    )


# La celda derecha de una fila con una racha de tres espacios dentro: la
# maquetación parte la pregunta en dos columnas más. La izquierda hace justo
# esto tres veces en la guía real ("segunda   del"), así que nada se lo impide a
# la derecha — y quedándose con la última columna, el trozo de en medio se
# perdía sin dejar rastro.
_SPLIT_QUESTION_SECTION = """
Art. 28                              Declaración de aplicabilidad
                                                                     Aplica: SI ☐ NO ☐

Propuestas de evidencias

☐ Declaración de Aplicabilidad.

Aspectos a evaluar                                                   Cumple

Art. 28.2    ¿La DdA está suscrita   por el responsable de seguridad?  ☐ SI ☐ NO ☐ EN PROCESO
"""


def test_a_question_the_layout_split_into_three_columns_keeps_all_of_it() -> None:
    # Lo que se perdía era el trozo de en medio, y en silencio: lo que queda
    # sigue siendo una pregunta con texto, así que `_check` —que sólo mira que no
    # esté vacía— la da por buena. Una pregunta de auditoría a la que le falta la
    # mitad es peor que una que falta entera, porque nadie la echa en falta.
    article = require(next(iter(parse_articles(_SPLIT_QUESTION_SECTION)), None))
    question = require(next(iter(article["questions"]), None))

    check(
        question["question"] == "¿La DdA está suscrita por el responsable de seguridad?",
        f"la pregunta quedó: {question['question']!r}",
    )
    check(question["reference"] == "Art. 28.2", f"la referencia quedó: {question['reference']!r}")


# Lo mismo, una línea más arriba: la racha de tres espacios cae dentro del
# **título** del artículo, así que la maquetación lo parte en dos columnas más.
_SPLIT_TITLE_SECTION = _SPLIT_QUESTION_SECTION.replace(
    "Art. 28                              Declaración de aplicabilidad",
    "Art. 28                              Declaración de   aplicabilidad",
).replace("¿La DdA está suscrita   por el", "¿La DdA está suscrita por el")


def test_a_title_the_layout_split_into_three_columns_keeps_all_of_it() -> None:
    """La misma trampa que la fila de arriba, en la línea del título.

    `_article` se quedaba con la primera columna y la **última**, así que con
    tres el trozo de en medio desaparecía — y en silencio, porque lo que queda
    sigue siendo un título con texto y `_check` sólo mira que no esté vacío. Un
    artículo del ENS titulado por la mitad no lo echa nadie en falta.

    La corrupción es la misma que `_questions` ya evita cortando por columna, en
    el mismo fichero y sobre el mismo documento: la guía real mete una racha de
    tres espacios dentro de la celda izquierda ("segunda   del"), así que nada
    se lo impide a la línea del título.
    """
    article = require(next(iter(parse_articles(_SPLIT_TITLE_SECTION)), None))

    check(
        article["title"] == "Declaración de aplicabilidad",
        f"el título quedó: {article['title']!r}",
    )
    check(article["reference"] == "Art. 28", f"la referencia quedó: {article['reference']!r}")


@pytest.mark.parametrize(
    ("reference", "title", "questions", "evidence"),
    [
        ("", "T", [_QUESTION], ["una evidencia"]),
        ("Art. 1", "", [_QUESTION], ["una evidencia"]),
        ("Art. 1", "T", [], ["una evidencia"]),
        ("Art. 1", "T", [_QUESTION], []),
    ],
    ids=["sin-referencia", "sin-titulo", "sin-preguntas", "sin-evidencias"],
)
def test_check_refuses_an_article_missing_any_of_its_four_parts(
    reference: str, title: str, questions: list[_QuestionDict], evidence: list[str]
) -> None:
    """Las cuatro partes de un artículo entran en el recuento, y `evidence` no estaba.

    De las medidas sí se exigía que ninguna se quedase sin viñetas; de los
    artículos, no — aunque los sirve la misma tool y salen del mismo parser. Un
    `Propuestas de evidencias` que dejara de casar (la guía ya escribe uno en
    singular, "Propuesta de Evidencias" en op.exp.6) deja `evidence` a cero sin
    tocar la referencia, el título ni las preguntas, que era lo único que se
    miraba.
    """
    articles = [
        _ArticleDict(
            reference=f"Art. {n}", title="T", evidence=["una evidencia"], questions=[_QUESTION]
        )
        for n in range(6)
    ]
    articles[1] = _ArticleDict(
        reference=reference, title=title, evidence=evidence, questions=questions
    )
    # Los 14 del recuento, repartidos entre los seis: el caso "sin-preguntas"
    # quita una, así que el primero la repone. Sin eso saltaría también el
    # recuento de preguntas y el test pasaría por el motivo equivocado.
    articles[0]["questions"] = [_QUESTION] * (14 - 4 - len(questions))

    with pytest.raises(ValueError, match="artículo incompleto") as excinfo:
        _check(articles, {f"org.{n}": ["una evidencia"] for n in range(73)})

    check("esperaba" not in str(excinfo.value), f"saltó además un recuento: {excinfo.value}")


# Una continuación del título que empieza **exactamente** dos columnas a la
# izquierda de donde el título abrió: es el único sitio donde el `- 2` de
# `_article` decide algo. Se arma con anchos explícitos en vez de contar espacios
# a ojo, porque lo que se afirma es una columna concreta y un espacio de más lo
# convertiría en otro test.
_TOLERANCE_SECTION = "\n".join(
    [
        "",
        f"{'Art. 28':<16}Declaración de aplicabilidad",  # el título abre en la columna 16
        f"{'':<14}firmada por el responsable",  # continuación en la 14 = 16 - 2
        f"{'':<69}Aplica: SI ☐ NO ☐",
        "",
        f"{'Aspectos a evaluar':<69}Cumple",
        "",
        f"{'Art. 28.2':<13}¿La DdA está suscrita?{'':<26}☐ SI ☐ NO ☐ EN PROCESO",
        "",
    ]
)


def test_the_two_column_tolerance_of_a_wrapped_title_is_the_boundary() -> None:
    """El `- 2` de `_article` es una tolerancia, y nada afirmaba cuánto tolera.

    La maquetación del PDF no cuadra al carácter, así que una línea que continúa
    el título puede empezar un par de columnas antes que él. El `>=` sobre
    `title_indent - 2` es lo que la recoge; con `>`, una continuación que caiga
    justo en esa columna se lee como referencia y el título pierde su segunda
    mitad.

    Los otros fixtures no lo ven: sus continuaciones empiezan en la columna 0 —
    claramente referencia— o en la del título — claramente título—, y ahí `>=` y
    `>` responden igual. Sólo el borde exacto los separa.
    """
    article = require(next(iter(parse_articles(_TOLERANCE_SECTION)), None))

    check(
        article["title"] == "Declaración de aplicabilidad firmada por el responsable",
        f"el título quedó: {article['title']!r}",
    )
    check(article["reference"] == "Art. 28", f"la referencia quedó: {article['reference']!r}")


def test_check_refuses_a_measure_whose_evidence_block_parsed_to_nothing() -> None:
    """La contraparte de las evidencias vacías, que sólo estaba a medias.

    `_check` mira dos cosas de cada medida: que tenga viñetas y que ninguna esté
    en blanco. La segunda sí se afirmaba; la primera no, y son fallos distintos —
    un bloque `Propuestas de evidencias` cuyo contenido no casa como viñeta deja
    la lista **vacía**, no una viñeta vacía. Sin afirmarla, el `or` de esa guarda
    podía degradarse a `and` con la suite en verde, y entonces una medida sin una
    sola evidencia pasaba.
    """
    articles = [
        _ArticleDict(
            reference=f"Art. {n}", title="T", evidence=["una evidencia"], questions=[_QUESTION]
        )
        for n in range(6)
    ]
    articles[0]["questions"] = [_QUESTION] * 9
    evidence: dict[str, list[str]] = {f"org.{n}": ["una evidencia"] for n in range(73)}
    evidence["org.0"] = []

    with pytest.raises(ValueError, match=r"evidencias vacías en org\.0") as excinfo:
        _check(articles, evidence)

    check("esperaba" not in str(excinfo.value), f"saltó además un recuento: {excinfo.value}")


@pytest.mark.parametrize(
    "headings",
    [(False, False), (True, False), (False, True), (True, True)],
    ids=["faltan-los-dos", "falta-6.2", "falta-6.1", "6.2-antes-que-6.1"],
)
def test_build_refuses_a_pdf_that_is_not_the_808(headings: tuple[bool, bool]) -> None:
    """La guarda de `build`, que no tenía una sola aserción.

    Es la primera cosa que mira el script y la que separa la 808 de las otras
    cuatro guías de la serie —que se descartan justamente por esto—, pero sólo
    se la mencionaba de pasada en el docstring de otro test. Sin afirmarla, sus
    tres condiciones podían degradarse por separado con la suite en verde: con
    un `and` en medio, un PDF al que le falte **una** de las dos secciones deja
    de rechazarse y se pone a parsear sobre un corte inventado.

    Los cuatro casos son las cuatro formas de estar mal: sin ninguna de las dos
    secciones, sin cada una de ellas, y con las dos pero en el orden cambiado.
    """
    has_first, has_second = headings
    parts = ["Guía de Seguridad de las TIC", "CCN-STIC 808", "Abril 2026", ""]
    if has_second and has_first:
        parts += [_MEASURES_HEADING, "cuerpo", _ARTICLES_HEADING, "cuerpo"]
    else:
        if has_first:
            parts += [_ARTICLES_HEADING, "cuerpo"]
        if has_second:
            parts += [_MEASURES_HEADING, "cuerpo"]

    with pytest.raises(ValueError, match="no se encontraron los apartados"):
        build("\n".join(parts))


# Una viñeta que se parte en dos líneas. La rama de "continuación" de `_bullets`
# la usan 12 de las 365 viñetas de la guía real, y ningún test la ejercitaba:
# medido instrumentando el parseo del PDF.
_WRAPPED_BULLET_SECTION = """
Art. 28                              Declaración de aplicabilidad
                                                                     Aplica: SI ☐ NO ☐

Propuestas de evidencias

☐ Acta de aprobación de la Declaración de Aplicabilidad suscrita
  por el Responsable de Seguridad.
☐ Segunda evidencia, de una sola línea.

Aspectos a evaluar                                                   Cumple

Art. 28.2    ¿La DdA está suscrita por el responsable?               ☐ SI ☐ NO ☐ EN PROCESO
"""


def test_a_bullet_that_wraps_is_rejoined_with_the_next_line() -> None:
    # Sin la rama de continuación, la segunda línea se pierde entera y la
    # evidencia queda cortada a media frase — en silencio, porque lo que queda
    # sigue siendo una viñeta con texto y `_check` sólo mira que no esté vacía.
    article = require(next(iter(parse_articles(_WRAPPED_BULLET_SECTION)), None))

    check(
        article["evidence"]
        == [
            "Acta de aprobación de la Declaración de Aplicabilidad suscrita "
            "por el Responsable de Seguridad.",
            "Segunda evidencia, de una sola línea.",
        ],
        f"las evidencias quedaron: {article['evidence']}",
    )


# Un bloque de título en el que **ninguna** línea trae dos columnas: la
# maquetación no separó la referencia del título en ninguna de ellas, así que
# todo cae del lado de la referencia y sólo la puede desenredar `_REFERENCE`.
# Es la forma real de dos de los seis artículos de la guía ("Disposición
# adicional segunda" y "Art.40 y 41"), y tampoco tenía test.
_UNSPLIT_TITLE_SECTION = """
Disposición
adicional segunda
Desarrollo del ENS. ITS y guías de seguridad
                                                                     Aplica: SI ☐ NO ☐

Aspectos a evaluar                                                   Cumple

Disposición     ¿Se conoce la relación de ITS?                       ☐ SI ☐ NO
"""


def test_a_title_block_the_layout_never_split_is_peeled_by_shape() -> None:
    # Sin el peel, `titles` se queda vacío y el artículo sale sin título: lo caza
    # `_check`, pero con "artículo incompleto" y sin decir que lo que falló fue
    # la maquetación de dos columnas. Con él, la referencia se reconoce por su
    # forma ("Disposición adicional <palabra>") y el resto es el título.
    article = require(next(iter(parse_articles(_UNSPLIT_TITLE_SECTION)), None))

    check(article["reference"] == "Disposición adicional segunda", article["reference"])
    check(
        article["title"] == "Desarrollo del ENS. ITS y guías de seguridad",
        f"el título quedó: {article['title']!r}",
    )


# El apartado 6.2, con las tres formas que la guía real usa y que nada afirmaba.
# La cabecera de una medida va **capitalizada** ("Mp.com.1"): las 143 del PDF lo
# están y ninguna viene en minúsculas. Y su encabezado de evidencias va en
# **singular** 77 veces de 79, una de ellas además con la E mayúscula.
_MEASURES_SECTION = """
Org.1                                  Política de seguridad
                                                                     Medida aplica: SI ☐ NO ☐

Propuesta de evidencias

☐ La PSI aprobada.
Op.exp.1     Se cita aquí la medida de inventario, y no abre otra.

Aspectos a evaluar                                                   Cumple

Mp.com.1                               Perímetro seguro
                                                                     Medida aplica: SI ☐ NO ☐

Propuesta de Evidencias

☐ El diagrama de red.

Aspectos a evaluar                                                   Cumple
"""


def test_a_measure_code_is_lower_cased_so_it_joins_with_the_anexo_ii() -> None:
    """La guía escribe "Mp.com.1" y el Anexo II "mp.com.1": sin el `.lower()`
    el fichero extraído no se une con nada.

    `evidencias_auditoria` cruza `measure_code` con las 73 medidas del snapshot,
    y valida el `code` que le pasan contra ese mismo conjunto. Con las claves
    capitalizadas, la tool rechazaría *todos* los códigos reales como "no es
    ninguna medida del Anexo II" y no serviría una sola evidencia.

    No es una precaución para un PDF hipotético: de las 143 cabeceras de medida
    del documento real, las 143 vienen capitalizadas y **ninguna** en minúsculas.
    """
    evidence = parse_measure_evidence(_MEASURES_SECTION)

    check(list(evidence) == ["org.1", "mp.com.1"], f"las claves salieron {list(evidence)}")


def test_a_duplicate_measure_header_is_refused() -> None:
    with pytest.raises(ValueError, match=r"cabecera de medida duplicada: org\.1"):
        parse_measure_evidence(_MEASURES_SECTION + _MEASURES_SECTION)


def test_a_line_that_looks_like_a_measure_header_but_is_not_one_opens_nothing() -> None:
    """Lo que separa una cabecera de medida de una cita a otra medida es la fila
    "Medida aplica", y nada lo afirmaba.

    `_MEASURE_HEADER` sólo mira la forma —código, tres espacios, texto— y esa
    forma la tienen también las referencias cruzadas que la guía escribe dentro
    de los cuerpos. Medido sobre el PDF real: **142 líneas** casan el patrón y
    sólo **73** son cabeceras; la confirmación descarta las otras 69.

    Sin ella, cada una de esas 69 abre una medida inventada y corta en seco el
    bloque de evidencias que estaba leyendo: aquí, `org.1` se quedaría sin su
    única viñeta y el fichero ganaría una clave `op.exp.1` con nada dentro.
    """
    evidence = parse_measure_evidence(_MEASURES_SECTION)

    check("op.exp.1" not in evidence, f"la cita abrió una medida: {list(evidence)}")
    check(
        evidence["org.1"]
        == ["La PSI aprobada. Op.exp.1 Se cita aquí la medida de inventario, y no abre otra."],
        f"las evidencias de org.1 quedaron: {evidence['org.1']}",
    )


def test_an_evidence_heading_in_the_singular_still_opens_the_block() -> None:
    """El `?` de `Propuestas?` carga con casi todo el apartado 6.2.

    El comentario del script lo presenta como la excepción de una medida, y es
    al revés: el documento real escribe el encabezado en **singular 77 veces de
    79** —una de ellas "Propuesta de Evidencias", con la E mayúscula, que es lo
    que pide el `re.IGNORECASE`— y en plural sólo dos. Sin la `s` opcional, 77
    medidas se quedarían sin evidencias; sin `IGNORECASE`, una más.
    """
    evidence = parse_measure_evidence(_MEASURES_SECTION)

    check(evidence["mp.com.1"] == ["El diagrama de red."], f"quedaron: {evidence['mp.com.1']}")


def test_a_continuation_shorter_than_its_own_column_does_not_crash() -> None:
    """El `min` de `_split_at_column`, que era el único de sus dos límites sin
    afirmar.

    La columna donde abre una fila la fija su primera línea, y las siguientes se
    cortan por ahí — pero una continuación puede ser más corta que esa columna,
    y entonces `line[cut - 1]` se sale de la cadena. Sin el `min`, eso es un
    `IndexError` que se lleva por delante la extracción entera y no menciona ni
    la fila ni el apartado.

    La guía de hoy no llega a tenerlo (medido: cero recortes en las 79 tablas),
    así que esto no cambia la extracción y sólo quita la trampa.
    """
    check(_split_at_column("corta", 40) == ("", "corta"), "no recortó al final de la línea")
