"""Shared test helpers: assertions, fixture pages, and local browser sessions.

``check`` and ``require`` exist because bandit's B101 (assert_used) flags the
``assert`` statement anywhere in the tree, tests included, and the project's
quality gate forbids silencing bandit findings via ``# nosec``, ``skips`` or
``exclude_dirs``. Both raise ``AssertionError`` explicitly instead, so tests
read like normal pytest assertions without tripping B101.

The rest builds fixture sites and the sessions that scrape them:
``site_files``/``table_html`` compose the pages, ``local_site`` and
``threaded_http_server`` serve them over a real socket, and ``local_session``
and ``local_repository`` point a real headless browser at the result. That is
what lets the suite reach 100% coverage with no network and no test doubles.
"""

from __future__ import annotations

import functools
import glob
import http.server
import json
import tempfile
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from os import PathLike
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest

from ensmcp.domain.models import ApplicabilityLevel, Reinforcement
from ensmcp.scraping.live_session import STEALTH_CHANNEL, STEALTH_HEADLESS, LiveSession
from ensmcp.scraping.navegable_repository import NavegableRepository
from ensmcp.scraping.persistent_context import DEFAULT_TIMEOUT_MS
from ensmcp.scraping.selectors import (
    CONTENT_IFRAME_URL_FRAGMENT,
    ENS_NAVEGABLE_URL,
    ENS_NORM_JS_PATH,
    REQUISITOS_JS_PATH,
)

# El corte del arnés para un `await` que sólo puede colgarse si el código bajo
# prueba está roto. Un test que se cuelga no falla: se come el tiempo del job
# entero y no dice nada, así que todo await cuyo límite lo ponga el propio código
# (un `while True` con fecha límite, un fetch sin timeout) va envuelto en esto.
HARNESS_GUARD_S = 20.0

# El prefijo que ``PersistentBrowserContext.open()`` pasa a ``tempfile.mkdtemp``.
_PROFILE_GLOB = "ensmcp-*"


def leftover_profiles() -> set[Path]:
    """Los perfiles temporales de Chrome que hay ahora mismo en el directorio temporal.

    Compartido porque hay dos dueños de la misma fuga: ``PersistentBrowserContext``,
    que crea el directorio, y ``LiveSession``, que es quien tiene que cerrarlo
    cuando la primera navegación falla. Lo que se comprueba siempre es el disco y
    no los campos internos — esa distinción está explicada en
    ``test_open_cleans_up_after_a_failed_launch``.
    """
    return {Path(path) for path in glob.glob(str(Path(tempfile.gettempdir()) / _PROFILE_GLOB))}


def check(condition: bool, message: str = "") -> None:
    """Raise AssertionError with ``message`` when ``condition`` is false."""
    if not condition:
        raise AssertionError(message)


def require[T](value: T | None, message: str = "") -> T:
    """Return ``value``, raising AssertionError when it is None.

    ``check`` takes a bool, so it cannot narrow a type: every
    ``check(x is not None)`` had to be followed by an ``if x is not None:``
    guard whose false branch can never be reached, since ``check`` would
    already have raised. That guard was dead code kept alive purely to satisfy
    the type checker. Narrowing by return type instead makes it unnecessary.
    """
    if value is None:
        raise AssertionError(message or "expected a value, but it was None")
    return value


def set_json_value(document: dict[str, Any], path: tuple[str | int, ...], value: object) -> None:
    """Set a nested JSON value used by malformed-document tests."""
    target: Any = document
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value


def check_invalid_json_shape(
    load: Callable[[str], object], document: dict[str, object], expected: str
) -> None:
    """Check the shape-error contract shared by both versioned codecs."""
    with pytest.raises(ValueError, match="does not have its shape") as excinfo:
        load(json.dumps(document))
    check(expected in str(excinfo.value), f"el mensaje no localiza el fallo: {excinfo.value}")


def requisitos_section(code: str) -> str:
    """One measure's block of a requisitos.js literal, the shape the real one has."""
    return f'<div class="section-title"><span>Título ({code})</span></div>texto'


def requisitos_section_with_a_question(code: str) -> str:
    """The same block, but carrying one real audit question under its level.

    ``requisitos_section`` above has a body of plain text and therefore **no**
    questions at all, so every offline scraper fixture served a corpus whose
    ``audit_requirements`` were empty on purpose. Nothing offline noticed that
    the repository merges them in — sustituir ``requirements.get(...)`` por una
    tupla vacía dejaba la suite entera en verde — y son la mitad del corpus de
    la que viven ``alcance_auditoria`` y ``requisitos_auditoria``.

    Es la forma del asset real: una cabecera de nivel abre el desplegable y cada
    ``requirement`` que viene detrás le pertenece, con su código impreso, su
    marca de esencial y su "NOTA:".
    """
    return (
        f'<div class="section-title"><span>Título ({code})</span></div>'
        '<div class="tittle-measure">Categoría Básica</div>'
        '<div class="requirement"><div class="requirement-question audit essential">'
        '<div class="q"><span class="code">1.1</span>¿Se cumple lo básico?</div>'
        '<div class="a">NOTA: una aclaración.</div></div></div>'
    )


# A minimal requisitos.js literal (parseable by parse_requisitos) for
# local_site() fixtures that need a real description merged in without pulling
# in the full captured fixture file.
#
# Cubre las cuatro medidas que aparecen en filas de fixture, igual y por lo
# mismo que MINIMAL_ENS_NORM_JS: el repositorio exige que toda fila de la tabla
# tenga su cuestionario en el asset, así que un sitio de fixture cuya tabla
# nombra medidas que su propio asset no define es la incoherencia que se
# persigue, no una simplificación admisible.
MINIMAL_REQUISITOS_JS = (
    "var requisitos808='"
    + "".join(requisitos_section(code) for code in ("org.1", "mp.s.4", "op.acc.5", "mp.if.3"))
    + "'"
)

# A minimal norms/ens.js: the two block shapes the real asset uses — a measure
# block, marked "[medida.k]", and a refuerzo block, marked "[medida.rN.k]" under
# an "R<n>-Título" heading — for fixture sites that want real RD wording merged
# in without serving the whole 162 KB capture.
#
# El texto es inventado, sólo la forma importa aquí. Ojo con leerlo como si
# fuera el real: un refuerzo tiene **título propio** ("R1-Detección y
# reacción."), no el de su medida, y este fixture repite el de mp.s.4 porque le
# daba igual. El README copió justo eso y acabó documentando un texto que el RD
# no dice; lo real se afirma en test_norm_texts.py.
# Cubre las cuatro medidas que aparecen en filas de fixture, no sólo mp.s.4: el
# repositorio exige que toda fila de la tabla tenga su redacción en el asset —es
# lo que caza un asset truncado— así que un sitio de fixture cuya tabla nombra
# medidas que su propio asset no define es justo la incoherencia que se
# persigue, no una simplificación admisible.
#
# Y por lo mismo cubre los **refuerzos** que esas filas exigen, no sólo las
# medidas: el R1 de mp.s.4 y los R1..R5 de op.acc.5, que es lo que pide su celda
# ("+ [R1 o R2 o R3 o R4]" en bajo, "+ [R2 o R3 o R4] + R5" en medio y alto). La
# guarda de los refuerzos caza un corte del asset caído *entre* el bloque de una
# medida y los de sus refuerzos, así que un fixture con la medida redactada y sus
# refuerzos mudos es esa misma incoherencia un nivel más abajo.
_OP_ACC_5_REINFORCEMENTS = "".join(
    f'"R{n}-Refuerzo {n} de la autenticación.\\r\\n'
    f'\u2013 [op.acc.5.r{n}.1] Redacción inventada del refuerzo R{n}.\\r\\n",'
    for n in range(1, 6)
)

# El R2 de mp.s.4 no lo pide la fila de este mismo fichero ("+ R1"), sino la
# celda "+ [R1 o R2] + R1" que arma un test del servidor para afirmar el orden de
# un refuerzo nombrado dos veces en la misma celda. Que sobre redacción no
# molesta: la guarda mira que la tengan los refuerzos que **la fila** exige, no
# que el asset no defina ninguno más.
_MP_S_4_REINFORCEMENTS = (
    '"R1-Protección frente a denegación de servicio.\\r\\n'
    '\u2013 [mp.s.4.r1.1] Se contratará un servicio de protección frente a DoS.\\r\\n",'
    '"R2-Balanceo de carga.\\r\\n'
    '\u2013 [mp.s.4.r2.1] Se repartirá la carga entre varios nodos.\\r\\n",'
)

MINIMAL_ENS_NORM_JS = (
    'var ENScompleto=["Se establecerán medidas preventivas frente a DoS. Para ello:\\r\\n'
    '\u2013 [mp.s.4.1] Se planificará la capacidad del sistema.\\r\\n",'
    + _MP_S_4_REINFORCEMENTS
    + '"[org.1.1] Se aprobará una política de seguridad.\\r\\n",'
    '"[op.acc.5.1] Se identificará a los usuarios externos.\\r\\n",'
    + _OP_ACC_5_REINFORCEMENTS
    + '"[mp.if.3.1] Se protegerán las instalaciones.\\r\\n"]'
)

# Fixture-site paths derived from the production constants rather than
# retyped: LiveSession resolves the content frame by matching
# CONTENT_IFRAME_URL_FRAGMENT against the frame url, and the repository fetches
# the descriptions from REQUISITOS_JS_PATH and the refuerzo wording from
# ENS_NORM_JS_PATH. Hardcoding any of them here would let a change in
# selectors.py leave every fixture silently serving the old path.
OUTER_PAGE_FILENAME = "outer.html"
CONTENT_PAGE_FILENAME = f"{CONTENT_IFRAME_URL_FRAGMENT}.html"
REQUISITOS_JS_FILENAME = REQUISITOS_JS_PATH.lstrip("/")
ENS_NORM_JS_FILENAME = ENS_NORM_JS_PATH.lstrip("/")

# The outer page every local fixture site serves: a bare iframe pointing at
# the content page, mirroring the real ens-navegable page's shape.
OUTER_IFRAME_HTML = f'<iframe src="{CONTENT_PAGE_FILENAME}"></iframe>'

# A content page with no #tablaResumen at all — for tests of what happens
# when the summary table never appears (a refresh gone wrong, a broken page).
NO_TABLE_HTML = "<p>no summary table here</p>"

# The one real measure row (org.1) every #tablaResumen fixture in these tests
# needs when it wants at least one real, fully-parseable measure.
MEASURE_ROW_HTML = (
    '<tr class="cuerpo_tabla_izq"><td class="cuerpo_tabla_izq">org.1</td>'
    "<td>Política de seguridad</td><td>Categoría</td>"
    "<td>aplica</td><td>aplica</td><td>aplica</td></tr>"
)


# A second real row (mp.s.4) whose Alto cell carries a reinforcement note, so
# a full scrape over a real HTTP server — not just the pure parser — is what
# proves reinforcements survive the DOM round trip.
REINFORCED_MEASURE_ROW_HTML = (
    '<tr class="cuerpo_tabla_izq"><td class="cuerpo_tabla_izq">mp.s.4</td>'
    "<td>Protección frente a denegación de servicio</td><td>D</td>"
    "<td>n.a.</td><td>aplica</td><td>+ R1</td></tr>"
)

# The real op.acc.5 row, the one measure shape where the table asks for a
# *choice* ("+ [R1 o R2 o R3 o R4]") instead of a conjunction, and mixes a
# choice with a required reinforcement in the same cell ("+ [R2 o R3 o R4] + R5").
CHOICE_MEASURE_ROW_HTML = (
    '<tr class="cuerpo_tabla_izq"><td class="cuerpo_tabla_izq">op.acc.5</td>'
    "<td>Mecanismo de autenticación (usuarios externos)</td><td>C I T A</td>"
    "<td>+ [R1 o R2 o R3 o R4]</td><td>+ [R2 o R3 o R4] + R5</td>"
    "<td>+ [R2 o R3 o R4] + R5</td></tr>"
)


def table_html(*rows: str) -> str:
    """Wrap ``rows`` (raw ``<tr>...</tr>`` HTML) in a #tablaResumen table."""
    return f'<table id="tablaResumen"><tbody>{"".join(rows)}</tbody></table>'


# El #tablaResumen del sitio real justo antes de que su script inyecte las filas:
# la cabecera ya está, así que la tabla **es visible** —que es lo único que
# espera `LiveSession`— y el `tbody` está todavía vacío. Con un `tbody` vacío y
# sin cabecera la tabla no tiene tamaño y no cuenta como visible, así que la
# cabecera es justo lo que hace representable esa carrera.
HEADER_ONLY_TABLE_HTML = (
    '<table id="tablaResumen"><thead><tr>'
    "<th>Medida</th><th>Título</th><th>Dimensiones</th>"
    "<th>Bajo</th><th>Medio</th><th>Alto</th>"
    "</tr></thead><tbody></tbody></table>"
)


# The one-real-measure #tablaResumen every fixture site serves when the test
# needs a scrape to actually yield a parseable measure.
MEASURE_TABLE_HTML = table_html(MEASURE_ROW_HTML)

# A local fixture site answers instantly, so it needs nowhere near the live
# site's timeout; the tests that assert on a *timeout* pass their own.
LOCAL_TIMEOUT_MS = 10000


def site_files(
    content_html: str, requisitos_js: str | None = None, ens_norm_js: str | None = None
) -> dict[str, str]:
    """The file set a local fixture site serves, keyed by the real paths.

    The two JS assets are only served when given: both are fetched lazily, once
    a measure row is actually parsed, so the tests that never reach one (a
    missing table, a malformed header row) deliberately omit them.
    """
    files = {OUTER_PAGE_FILENAME: OUTER_IFRAME_HTML, CONTENT_PAGE_FILENAME: content_html}
    if requisitos_js is not None:
        files[REQUISITOS_JS_FILENAME] = requisitos_js
    if ens_norm_js is not None:
        files[ENS_NORM_JS_FILENAME] = ens_norm_js
    return files


@asynccontextmanager
async def live_session(
    url: str = ENS_NAVEGABLE_URL,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    headless: bool = STEALTH_HEADLESS,
    channel: str | None = STEALTH_CHANNEL,
) -> AsyncIterator[LiveSession]:
    """A ``LiveSession`` that always closes, even when the test body raises."""
    session = LiveSession(url=url, timeout_ms=timeout_ms, headless=headless, channel=channel)
    try:
        yield session
    finally:
        await session.close()


@asynccontextmanager
async def local_session(
    base_url: str, timeout_ms: int = LOCAL_TIMEOUT_MS
) -> AsyncIterator[LiveSession]:
    """A ``LiveSession`` over a local fixture site's outer page.

    Headless bundled Chromium (``channel=None``), unlike the headed real
    Chrome the ``STEALTH_*`` defaults select: that one exists only to get past
    the live site's WAF, which a local server obviously does not have.
    """
    async with live_session(
        url=f"{base_url}/{OUTER_PAGE_FILENAME}",
        timeout_ms=timeout_ms,
        headless=True,
        channel=None,
    ) as session:
        yield session


@asynccontextmanager
async def local_repository(
    files: dict[str, str], timeout_ms: int = LOCAL_TIMEOUT_MS
) -> AsyncIterator[NavegableRepository]:
    """A ``NavegableRepository`` scraping a local fixture site.

    The whole ``local_site`` + ``local_session`` + ``NavegableRepository``
    stack, which is what every offline scraper test wants. The two tests that
    also need the ``LiveSession`` itself (to kill its page, or to drive a
    stateful server that ``local_site`` cannot serve) compose the two halves
    by hand instead.
    """
    with local_site(files) as base_url:
        async with local_session(base_url, timeout_ms=timeout_ms) as session:
            yield NavegableRepository(session)


class _Utf8RequestHandler(http.server.SimpleHTTPRequestHandler):
    """``SimpleHTTPRequestHandler``, but text responses always declare UTF-8.

    Fixture files are always written as UTF-8 (see ``local_site()`` below),
    but the base handler's guessed Content-Type never carries a charset —
    without one, a real browser has to sniff the encoding of anything
    outside plain ASCII, and Chromium's guess is not UTF-8. That silently
    scrambled every accented character (real ENS Navegable text: "í", "ó",
    "ñ", ...) in any fixture HTML served this way.
    """

    def guess_type(self, path: str | PathLike[str]) -> str:
        content_type = super().guess_type(path)
        if content_type.startswith("text/") and "charset=" not in content_type:
            return f"{content_type}; charset=utf-8"
        return content_type


class Utf8RequestHandler(http.server.BaseHTTPRequestHandler):
    """Quiet handler for stateful fixture sites with UTF-8 text responses."""

    def _send(self, body: str, content_type: str) -> None:
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: object) -> None:
        pass


_RequestHandlerFactory = Callable[..., http.server.BaseHTTPRequestHandler]


@contextmanager
def threaded_http_server(handler: _RequestHandlerFactory) -> Iterator[str]:
    """Run ``handler`` on a real ``ThreadingHTTPServer``, yielding its base URL.

    A real socket and a real background thread, not a mock. Factored out of
    ``local_site`` because ``test_live_session.py`` needs the same bootstrap
    for a handler that serves *stateful* responses (not files from disk).
    """
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@contextmanager
def local_site(files: dict[str, str]) -> Iterator[str]:
    """Serve ``files`` (relative path -> content) over a real local HTTP server.

    For scraper-logic edge cases (a missing selector, an empty table row)
    that the live page's own content cannot be relied on to exhibit on
    demand. Yields the server's base URL.
    """
    with TemporaryDirectory() as root:
        for relative_path, content in files.items():
            path = Path(root) / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        handler = functools.partial(_Utf8RequestHandler, directory=root)
        with threaded_http_server(handler) as base_url:
            yield base_url


# Cuántas parejas se prueban antes de rendirse. Cada una vale la mitad de las
# veces, así que agotar cien es un suceso de probabilidad 2**-100: el bucle
# termina siempre, y el límite sólo está para que no pueda no terminar.
_TIE_ATTEMPTS = 100


def a_reinforcement_tie_the_set_iterates_backwards() -> tuple[frozenset[Reinforcement], str, str]:
    """Dos refuerzos que sólo difieren en la redacción y que el conjunto itera al revés.

    Las dos ordenaciones que llevan ``text`` en su clave —la de ``snapshot.codec``
    y la del payload— existen porque un ``Reinforcement`` tiene cuatro campos y
    la clave enumeraba tres: dos que empatan en nivel, código y ``alternative``
    salían en el orden de iteración del frozenset, que es arbitrario **entre
    procesos**. Afirmarlo con una pareja fija de textos no lo comprueba, aunque
    lo diga: el hash de una cadena está aleatorizado por ``PYTHONHASHSEED``, así
    que esa pareja itera ya ordenada en cerca de la mitad de los arranques, y en
    ésos la comprobación pasa igual con la clave rota. Medido sobre ocho
    semillas: tres la dejaban pasar.

    Buscar la pareja en vez de fijarla quita esa moneda al aire. Se prueban
    redacciones hasta dar con unas que el conjunto itere al revés de como se
    ordenan; a partir de ahí, una clave sin ``text`` no puede dar el orden
    ordenado, sea cual sea la semilla — que es lo que aquellas afirmaciones
    prometían y no cumplían.
    """
    for attempt in range(_TIE_ATTEMPTS):
        first, second = f"aaa redacción {attempt}", f"zzz redacción {attempt}"
        tied = frozenset(
            {
                Reinforcement("R1", ApplicabilityLevel.BASICO, True, first),
                Reinforcement("R1", ApplicabilityLevel.BASICO, True, second),
            }
        )
        if [item.text for item in tied] == [second, first]:
            return tied, first, second
    raise AssertionError(f"ninguna de {_TIE_ATTEMPTS} parejas se itera al revés")
