"""Extract the CCN-STIC 808 checklist into the data file the server ships.

An ENS audit verifies two things: the RD's articles and the Anexo II measures.
ENS Navegable publishes the measures' questionnaire but neither the article
checks nor the "propuestas de evidencias" — the documents an auditor will ask
for. Both are in the CCN-STIC 808, so they come from the guide's PDF instead of
from the site.

What gets committed is the **extracted data**, with its attribution, not the
guide: the PDF stays out of the repository (see .gitignore). Regenerating this
therefore needs a copy of it, the same way regenerating the snapshot needs
Chrome.

It is written to a file of its own, never merged into ``anexo_ii.json``: that
snapshot is checked byte for byte against the live site by a network test, so
folding in data the site does not publish would break it for no real reason.

It reads the guide's text on stdin rather than shelling out to a PDF reader:
that keeps it a pure text-to-JSON transformer with no process to trust, and
makes the parser directly testable against a text fixture.

Usage:
    pdftotext -layout CCN-STIC-808-*.pdf - | python scripts/build_guia_808.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import TypedDict

from atomic_write import write_atomic

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = _ROOT / "src" / "ensmcp" / "data" / "guia_808.json"

SCHEMA_VERSION = 2
_SOURCE = (
    "CCN-STIC-808 ENS. Verificación del cumplimiento, edición de {edition} "
    "(Centro Criptológico Nacional)"
)

# La portada imprime la edición como "Abril 2026": una sola vez en todo el
# documento y dentro de las primeras líneas. Las cuatro guías que ``guia.codec``
# descarta la imprimen exactamente igual (Junio 2017, Octubre 2018, Mayo 2020,
# Diciembre 2019), así que la forma no es una casualidad de este PDF.
_EDITION = re.compile(
    r"\b((?:Enero|Febrero|Marzo|Abril|Mayo|Junio|Julio|Agosto|Septiembre|Octubre"
    r"|Noviembre|Diciembre)\s+20\d{2})\b"
)
_COVER_LINES = 40


def parse_source(text: str) -> str:
    """La atribución de la guía, con la edición de la que salió la extracción.

    Sin la edición, la atribución no identifica el documento. Y en esta serie la
    edición **es** lo que decide si una guía vale: ``guia.codec`` descarta cuatro
    justamente por ser anteriores al RD 311/2022, y la 804 trae dos ediciones
    dentro del mismo fichero. Lo que se distribuye es la extracción y no el PDF,
    así que si la edición no viaja con el dato no viaja en ninguna parte — nadie
    puede saber contra qué versión de la guía está auditando.

    Se lee de la portada en vez de fijarla a mano para que siga siendo cierta
    cuando alguien regenere el fichero con la guía siguiente, que es exactamente
    cuando una constante escrita a mano se queda mintiendo.
    """
    match = _EDITION.search("\n".join(text.split("\n")[:_COVER_LINES]))
    if match is None:
        raise ValueError(
            "no se encontró la edición en la portada de la guía "
            f"(se buscó un «Mes AAAA» en las primeras {_COVER_LINES} líneas)"
        )
    return _SOURCE.format(edition=match.group(1))


# Section boundaries, taken from the last occurrence so the table of contents
# (which repeats both headings on page 3) does not win.
_ARTICLES_HEADING = "6.1 CUMPLIMIENTO DE ARTÍCULOS DEL ENS"
_MEASURES_HEADING = "6.2 CUMPLIMIENTO DE MEDIDAS DE SEGURIDAD"

# Running head and foot, repeated mid-block on every page break.
_PAGE_FURNITURE = re.compile(r"^\s*(CCN-STIC-808\b|Centro Criptológico Nacional\b|\d+\s*$)")

# An article opens with a row offering the auditor the applicability checkboxes.
# Art. 40/41 says "Medida aplica" where the rest say "Aplica" — the guide's own
# inconsistency, not a parsing artefact.
_ARTICLE_HEADER = re.compile(r"^\s*(?:.*?\s{3,})?(?:Medida a|A)plica: SI ☐")
# La guía escribe este encabezado de tres maneras, contadas sobre el documento
# real: "Propuesta de evidencias" 76 veces, "Propuestas de evidencias" 2 y
# "Propuesta de Evidencias" 1 (op.exp.6). O sea que el **singular es la regla**
# —y no la excepción de una medida, que es lo que decía aquí— así que la ``s``
# opcional carga con 77 de los 79 bloques y el ``IGNORECASE`` con el que queda.
_EVIDENCE_HEADING = re.compile(r"^\s*Propuestas? de evidencias\s*$", re.IGNORECASE)
_TABLE_HEADING = re.compile(r"^\s*(?:.*\s{3,})?Aspectos a evaluar\b")
# La guía marca sus viñetas con dos caracteres distintos para lo mismo: BALLOT
# BOX (U+2610) y WHITE SQUARE (U+25A1), 1647 y 439 veces. Hoy los bloques de
# evidencia usan sólo el primero —verificado: 365 viñetas con cualquiera de los
# dos, las mismas 365— y los U+25A1 viven todos en las tablas de preguntas, que
# esto no extrae. Aceptar ambos no cambia nada y quita la trampa: una viñeta
# marcada con el otro carácter no abriría entrada, caería en la rama de
# "continuación" y se pegaría entera al final de la viñeta anterior. En
# silencio, además: ``_check`` sólo mira que ninguna esté vacía, y una pegada
# no lo está.
#
# El grupo es ``(.+)`` y no ``(\S.*)``, que es lo que había. El ``\S`` estaba
# para que una casilla suelta no abriera una viñeta hecha sólo de espacios, y esa
# línea la aparta ahora ``_bullets`` antes de llegar aquí — con o sin espacios
# detrás, que es lo que el ``\S`` no distinguía. Sin un caso que lo alcance, lo
# único que quedaba era la diferencia invisible entre un renglón y el mismo
# renglón con espacios al final.
#
# Aquí había además una mirada adelante que se negaba a abrir viñeta con
# ``SI``/``NO``/``EN PROCESO``, y se ha ido porque no servía para lo que decía
# servir: negarse a *abrir* viñeta con una fila "Cumple" no la aparta, la manda a
# la rama de continuación, y entonces se pega al final de la viñeta anterior con
# las casillas incluidas. La única forma de que una fila así no llegue a las
# evidencias es descartar la línea entera, que es lo que hace ``_bullets`` con
# ``_CHECKBOXES`` — y desde que ése reconoce las dos grafías, la mirada adelante
# no tenía ya ni un caso que la alcanzase.
_BULLET = re.compile(r"[☐□]\s*(.+)$")
# Las dos cajas por su cuenta, para preguntar si una línea es sólo casillas.
_BOXES = re.compile(r"[☐□]")
# Each question row carries the "Cumple" checkboxes on its first line.
_QUESTION_START = "☐ SI"
# Las dos grafías, igual que ``_BULLET`` y por la misma razón: la guía marca lo
# mismo con las dos, y es esto lo que aparta una fila "Cumple" de las evidencias.
# Comprobado sobre la guía real: aceptar el WHITE SQUARE deja la extracción byte
# a byte idéntica —sus filas "Cumple" usan el BALLOT BOX, que es lo que
# ``_QUESTION_START`` ya da por hecho— así que esto sólo quita la trampa.
_CHECKBOXES = re.compile(r"[☐□]\s*(?:SI|NO|EN PROCESO)\b")

# A measure block in §6.2 opens the same way, right under its "Org.1  Título".
_MEASURE_HEADER = re.compile(r"^\s*([A-Za-z]+(?:\.[A-Za-z]+)*\.\d+)\s{3,}\S")
_MEASURE_APPLIES = "Medida aplica: SI"

# The applicability boilerplate to strip off a header row before reading the
# reference that shares the line with it ("del ENS").
_APPLICABILITY = re.compile(r"(?:Medida a|A)plica: SI.*$")
# Peels the reference off a title line the layout did not split into columns
# ("Art.40 y 41 Categorización...", "Disposición adicional segunda Desarrollo...").
_REFERENCE = re.compile(r"^(Art\.\s*\d+(?:\s*y\s*\d+)?|Disposición adicional \w+)\s+(.*)$")


class _QuestionDict(TypedDict):
    reference: str
    question: str


class _ArticleDict(TypedDict):
    reference: str
    title: str
    evidence: list[str]
    questions: list[_QuestionDict]


def _clean(lines: list[str]) -> list[str]:
    """Drop page furniture, keeping blank lines.

    The blanks are load-bearing: they are what separates an article's title
    from the table of the article above it, which has no other marker.
    """
    return [line for line in lines if not (line.strip() and _PAGE_FURNITURE.match(line))]


# A column of a laid-out row: a run of text whose internal gaps are one or two
# spaces. The inverse of splitting on three or more, but as a match, so the
# offset each column starts at comes out with it.
_FIELD = re.compile(r"\S+(?:[^\S\n]{1,2}\S+)*")


def _fields(line: str) -> list[tuple[int, str]]:
    """Each column of a laid-out row, with the offset where it starts.

    The offset is what tells a wrapped **reference** from a wrapped **question**
    when the layout leaves three spaces *inside* the left cell: both look like
    two columns and only the position says which cell the second one is in.
    """
    return [(match.start(), match.group()) for match in _FIELD.finditer(line)]


def _columns(line: str) -> list[str]:
    """Split a laid-out row into its columns.

    The layout separates cells by runs of three or more spaces, so this
    recovers "ref | text" on a row's first line and just "text" on its
    continuations, without having to guess pixel columns.
    """
    return [text for _, text in _fields(line)]


def _split_at_column(line: str, column: int) -> tuple[str, str]:
    """Cut a laid-out line into ``(left cell, right cell)`` at ``column``.

    A cut, not a field split, because the two cells can end up inside the *same*
    field: "segunda   del y guías…" separates the left cell's own wrapped word
    from the right cell's text by a single space, so nothing about the gaps
    distinguishes them and only the column does.

    Walking left off a mid-word cut is the tolerance — the PDF layout does not
    align to the character, and clipping a continuation's first letters would
    just trade one silent corruption for another.
    """
    cut = min(column, len(line))
    while cut > 0 and not line[cut - 1].isspace():
        cut -= 1
    return line[:cut].strip(), line[cut:].strip()


def _join(parts: list[str]) -> str:
    return " ".join(" ".join(parts).split())


def _bullets(lines: list[str]) -> list[str]:
    """The ``☐`` bullets of an evidence block, each rejoined across wraps.

    Una fila "Cumple" se descarta entera, igual que una línea en blanco. Que
    ``_BULLET`` se niegue a abrir viñeta con ella no basta: entonces cae en la
    rama de continuación de abajo y se pega al final de la viñeta anterior, que
    es la misma corrupción silenciosa por el otro lado. Y silenciosa de verdad:
    ``_check`` sólo mira que ninguna viñeta esté vacía, y una viñeta con una
    fila de casillas pegada detrás no lo está.

    **Lleve o no algo más en la línea.** La condición era "las casillas y nada
    más", así que la fila con una columna de observaciones a su derecha —dos
    celdas que la maquetación puede poner en el mismo renglón— se escapaba por
    donde el propio párrafo de arriba dice que no debe: ``_BULLET`` no abre
    viñeta con ella, así que se pegaba al final de la anterior con los ``☐``
    incluidos. Medido sobre una sección de prueba, la evidencia salía como "La
    única evidencia de verdad. ☐ SI ☐ NO ☐ EN PROCESO Observaciones del
    auditor".

    Descartar la línea entera es lo único que aparta una fila "Cumple": lo que
    había en su lugar era una mirada adelante en ``_BULLET`` que se negaba a
    *abrir* viñeta con ella, y eso no la aparta — la manda a la rama de
    continuación de abajo, que es la corrupción que este párrafo describe. Y no
    puede llevarse por delante una viñeta real, porque una evidencia no empieza
    por "SI", "NO" ni "EN PROCESO", que es lo único que ``_CHECKBOXES`` reconoce.

    Y una línea que, quitadas las casillas, no deja nada tampoco lleva
    evidencia: un ``☐`` suelto es tan vacío como un renglón en blanco, y por eso
    se descarta con el mismo criterio en vez de por su longitud. Sin esto no
    había forma buena de tratarlo — ``_BULLET`` no puede abrir viñeta con lo que
    no tiene texto, así que el ``☐`` acababa pegado al final de la viñeta
    anterior ("Una evidencia de verdad. ☐") — y el criterio "está en blanco" ya
    queda subsumido, porque un renglón sin casillas ni texto no deja nada
    tampoco.

    Los bloques de evidencia de hoy se cortan en "Aspectos a evaluar" antes de la
    primera casilla, así que la extracción sigue siendo byte a byte la misma: las
    mismas 365 viñetas.
    """
    found: list[list[str]] = []
    for line in lines:
        if _CHECKBOXES.search(line) or not _BOXES.sub("", line).strip():
            continue
        match = _BULLET.search(line)
        if match is not None:
            found.append([match.group(1)])
        elif found:
            # A wrapped bullet: it continues the previous one.
            found[-1].append(line.strip())
    return [_join(parts) for parts in found]


def _questions(lines: list[str], fallback_reference: str) -> list[_QuestionDict]:
    """The rows of an "Aspectos a evaluar" table.

    A row starts on the line carrying its "☐ SI" checkbox and runs until the
    next one, so the NOTA paragraphs and the extra questions the guide packs
    into the same cell stay with the row they belong to.

    Which cell a continuation's text belongs to is decided **by where it starts,
    not by how many columns the line has**. Counting columns says "one is
    question, two means the reference wrapped as well, and the last one is the
    question" — which is wrong whenever the left cell itself wraps with three
    spaces inside it. The guide does exactly that three times, in "Disposición
    adicional segunda del ENS": the layout writes "segunda   del", so "del" was
    read as the start of the question and spliced into the middle of the
    sentence — "(ITS) **del** y guías de seguridad" — while the reference lost
    it. Cutting each continuation at the column the row itself opened at puts
    every fragment back in its own cell.

    La primera línea de una fila se corta igual, y antes no: se quedaba con
    ``fields[-1]``, o sea **sólo la última** columna. Con dos columnas eso es la
    pregunta entera y sale bien, pero una racha de tres espacios dentro de la
    celda derecha la parte en dos y la de en medio desaparecía — sin dejar rastro,
    porque lo que queda sigue siendo una pregunta con su texto y ``_check`` sólo
    mira que no esté vacía. Que este documento mete rachas de tres espacios dentro
    de una celda no es una hipótesis: es justo lo que dice el párrafo de arriba
    sobre la celda izquierda, y no hay nada que se lo impida a la derecha.
    Cortando por columna, la pregunta es "todo lo que hay del corte a la derecha"
    y da igual en cuántos trozos lo parta la maquetación. Hoy ninguna fila de la
    guía abre con más de dos columnas, así que la extracción no cambia; lo que se
    va es la trampa.
    """
    rows: list[dict[str, list[str]]] = []
    question_at = 0
    for line in lines:
        clean = _CHECKBOXES.sub("", line)
        fields = _fields(clean)
        if not fields:
            continue
        if _QUESTION_START in line:
            # Con una sola columna no hay referencia en la fila: la pregunta
            # empieza donde empieza lo único que hay, y la referencia la pone el
            # artículo. Con dos o más, la referencia es la primera y la pregunta
            # abre en la segunda.
            question_at = fields[1][0] if len(fields) > 1 else fields[0][0]
            reference, question = _split_at_column(clean, question_at)
            rows.append({"reference": [reference or fallback_reference], "question": [question]})
        elif rows:
            reference, question = _split_at_column(clean, question_at)
            if reference:
                rows[-1]["reference"].append(reference)
            if question:
                rows[-1]["question"].append(question)
    return [
        _QuestionDict(reference=_join(row["reference"]), question=_join(row["question"]))
        for row in rows
    ]


def _title_start(lines: list[str], header: int) -> int:
    """Where the contiguous non-blank block just above ``header`` begins.

    Walking back to the nearest blank line is what keeps the previous
    article's table out: nothing else marks where a title starts.

    It also marks where the previous article *ends*, which is why this is a
    position and not just the block: an article's body has to stop before the
    next one's title, not at the next one's header row.
    """
    start = header
    while start > 0 and lines[start - 1].strip():
        start -= 1
    return start


def _title_block(lines: list[str], header: int) -> list[str]:
    """The contiguous non-blank lines just above an article's header row."""
    return lines[_title_start(lines, header) : header]


def _article(lines: list[str], header: int, end: int) -> _ArticleDict:
    # Reference and title share the rows above the applicability line, in two
    # columns the layout does not always separate. Where it does, the split is
    # taken as given; where it does not, the reference is peeled off by shape.
    block = [line for line in _title_block(lines, header) if _ARTICLES_HEADING not in line]
    references: list[str] = []
    titles: list[str] = []
    title_indent: int | None = None
    for line in block:
        # Cortado por columna, exactamente como `_questions` corta sus filas y
        # por la misma razón: quedarse con `columns[0]` y `columns[-1]` es
        # quedarse con la primera y la **última**, así que una racha de tres
        # espacios dentro de una de las dos celdas la parte en tres y el trozo de
        # en medio desaparecía. En silencio, además: lo que queda sigue siendo un
        # título con texto y una referencia con texto, que es lo único que mira
        # `_check`. Que esta maquetación mete rachas de tres espacios dentro de
        # una celda no es hipótesis — es lo que le pasa a "Disposición adicional
        # segunda   del", que ya obligó a cortar por columna en `_questions`.
        # Hoy ninguna línea de título de la guía abre más de dos columnas, así
        # que la extracción no cambia; lo que se va es la trampa.
        fields = _fields(line)
        if len(fields) > 1:
            title_indent = fields[1][0]
            reference, title = _split_at_column(line, title_indent)
            references.append(reference)
            titles.append(title)
        elif title_indent is not None and fields[0][0] >= title_indent - 2:
            titles.append(fields[0][1])
        else:
            references.append(fields[0][1])
    if not titles:
        peeled = _REFERENCE.match(_join(references))
        if peeled is not None:
            references, titles = [peeled.group(1)], [peeled.group(2)]
    # The reference can spill onto the header row's own left column ("del ENS").
    references += _columns(_APPLICABILITY.sub("", lines[header]))
    reference, title = _join(references), _join(titles)
    body = lines[header + 1 : end]
    evidence_at = next(
        (index for index, line in enumerate(body) if _EVIDENCE_HEADING.match(line)), None
    )
    table_at = next((index for index, line in enumerate(body) if _TABLE_HEADING.match(line)), None)
    evidence = _bullets(body[evidence_at:table_at]) if evidence_at is not None else []
    questions = _questions(body[table_at + 1 :], reference) if table_at is not None else []
    return _ArticleDict(reference=reference, title=title, evidence=evidence, questions=questions)


def parse_articles(section: str) -> list[_ArticleDict]:
    """§6.1, the audit checks over the RD's own articles."""
    lines = _clean(section.split("\n"))
    headers = [index for index, line in enumerate(lines) if _ARTICLE_HEADER.match(line)]
    # Un artículo acaba donde empieza el **título** del siguiente, no en su fila
    # de aplicabilidad. Cortando en la fila, las líneas del título quedaban
    # dentro del cuerpo anterior y `_questions` se las pegaba como continuación
    # de su última pregunta: cinco de las seis preguntas finales acababan con el
    # título del artículo siguiente ("...de medidas de seguridad? Declaración de
    # aplicabilidad"), y una arrastró también su referencia. Esas mismas líneas
    # las recoge `_title_block` para el artículo al que pertenecen, así que
    # estaban usándose dos veces.
    return [
        _article(
            lines,
            header,
            (
                _title_start(lines, headers[position + 1])
                if position + 1 < len(headers)
                else len(lines)
            ),
        )
        for position, header in enumerate(headers)
    ]


def parse_measure_evidence(section: str) -> dict[str, list[str]]:
    """§6.2, the documents an auditor may ask for, per measure."""
    lines = _clean(section.split("\n"))
    evidence: dict[str, list[str]] = {}
    seen: set[str] = set()
    code: str | None = None
    collecting: list[str] | None = None
    for index, line in enumerate(lines):
        header = _MEASURE_HEADER.match(line)
        if header is not None and _MEASURE_APPLIES in "".join(lines[index : index + 4]):
            code = header.group(1).lower()
            if code in seen:
                raise ValueError(f"cabecera de medida duplicada: {code}")
            seen.add(code)
            collecting = None
        elif _EVIDENCE_HEADING.match(line):
            collecting = []
        elif _TABLE_HEADING.match(line) and collecting is not None and code is not None:
            evidence.setdefault(code, []).extend(_bullets(collecting))
            collecting = None
        elif collecting is not None:
            collecting.append(line)
    return evidence


def build(text: str) -> tuple[list[_ArticleDict], dict[str, list[str]]]:
    """Parse the guide, refusing to return anything that looks truncated."""
    start, middle = text.rfind(_ARTICLES_HEADING), text.rfind(_MEASURES_HEADING)
    if start == -1 or middle == -1 or middle < start:
        raise ValueError("no se encontraron los apartados 6.1 y 6.2 en el PDF")
    articles = parse_articles(text[start:middle])
    measure_evidence = parse_measure_evidence(text[middle:])
    _check(articles, measure_evidence)
    return articles, measure_evidence


def _check(articles: list[_ArticleDict], measure_evidence: dict[str, list[str]]) -> None:
    """Fail loudly on a partial extraction.

    A heuristic parser over a PDF that quietly swallows half the content is the
    real risk here, and shipping that is worse than not shipping at all.

    Las evidencias de un artículo entran en el recuento de abajo, y no estaban:
    de las medidas sí se exigía que ninguna se quedase sin viñetas, y de los
    artículos no, aunque los sirve la misma tool y salen del mismo parser. Un
    ``Propuestas de evidencias`` que dejara de casar —la guía ya escribe uno en
    singular, "Propuesta de Evidencias" en op.exp.6— deja ``evidence`` a cero sin
    tocar ni la referencia, ni el título, ni las preguntas, que es justo lo único
    que se miraba. Los seis artículos de la guía las tienen (11 viñetas), así que
    esto no cambia la extracción; cierra la asimetría en la función cuyo trabajo
    es precisamente que nada se pierda en silencio.
    """
    questions = [q for article in articles for q in article["questions"]]
    problems = []
    if len(articles) != 6:
        problems.append(f"esperaba 6 artículos, hay {len(articles)}")
    if len(questions) != 14:
        problems.append(f"esperaba 14 preguntas de artículo, hay {len(questions)}")
    if len(measure_evidence) != 73:
        problems.append(f"esperaba 73 medidas con evidencias, hay {len(measure_evidence)}")
    references = [article["reference"] for article in articles]
    if len(references) != len(set(references)):
        problems.append("hay referencias de artículo duplicadas")
    for article in articles:
        if (
            not article["reference"]
            or not article["title"]
            or not article["questions"]
            or not article["evidence"]
            # Y que ninguna de sus viñetas esté vacía, que es la mitad que
            # faltaba: de las medidas se exige desde siempre (abajo), y de los
            # artículos sólo se miraba que la lista no lo estuviera. Salen del
            # mismo ``_bullets``, así que una viñeta que se queda en nada —una
            # casilla suelta en el bloque de evidencias— la delataba en una mitad
            # del fichero y pasaba en la otra. Los seis artículos tienen sus 11
            # viñetas con texto, así que esto no cambia la extracción.
            or any(not bullet for bullet in article["evidence"])
        ):
            problems.append(f"artículo incompleto: {article['reference']!r}")
    for code, bullets in measure_evidence.items():
        if not bullets or any(not bullet for bullet in bullets):
            problems.append(f"evidencias vacías en {code}")
    if problems:
        raise ValueError("extracción incompleta:\n  " + "\n  ".join(problems))


def main() -> None:
    """Entry point: parse the guide's text from stdin and write the data file."""
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    # Se decodifica desde ``stdin.buffer`` y no se lee ``sys.stdin`` directamente:
    # ése usa la codificación de la *locale*, y el modo UTF-8 no es el
    # predeterminado hasta Python 3.15 (PEP 686). En un Windows recién instalado
    # eso es cp1252, con lo que la tubería de ``pdftotext`` llegaba destrozada: el
    # apartado que se busca lleva tilde ("6.1 CUMPLIMIENTO DE ARTÍCULOS DEL ENS")
    # y las viñetas son U+2610, así que ``build`` fallaba con "no se encontraron
    # los apartados 6.1 y 6.2 en el PDF" — culpando al PDF de un problema de
    # consola. El PDF siempre se decodifica igual, sea cual sea el sistema.
    text = sys.stdin.buffer.read().decode("utf-8")
    articles, measure_evidence = build(text)
    document = {
        "schema_version": SCHEMA_VERSION,
        "source": parse_source(text),
        "articles": articles,
        # Una lista y no un objeto: el orden en que la guía imprime sus medidas
        # en el 6.2 es el del Anexo II, y es el que ``evidencias_auditoria``
        # tiene que servir para recorrer las 73 igual que ``alcance_auditoria``.
        # Escrito como objeto se perdía dos veces —``sort_keys=True`` reordena
        # las claves al volcar, y quien lea el fichero no tiene por qué
        # conservarlas—, así que la lista es lo que hace del orden un dato.
        "measure_evidence": [
            {"measure_code": code, "evidence": bullets}
            for code, bullets in measure_evidence.items()
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # ``newline="\n"`` por lo mismo que ``build_snapshot``: sin él, en Windows el
    # fichero saldría con CRLF y el dato commiteado dependería del sistema.
    write_atomic(
        output_path,
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    bullets = sum(len(items) for items in measure_evidence.values())
    print(f"Wrote {len(articles)} articles and {bullets} evidence bullets to {output_path}")


if __name__ == "__main__":
    main()
