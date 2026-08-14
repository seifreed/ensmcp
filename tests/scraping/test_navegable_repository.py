"""Integration tests for NavegableRepository over a warm LiveSession.

Every test drives the real Patchright scraper in a real browser — the
project's no-mocks rule leaves no substitute for that. What differs is the
page it scrapes: the ones marked ``network`` assert on the real ENS corpus and
so need the live site, while the rest scrape fixture pages served by a real
local HTTP server, which is what lets them pin down malformed rows and missing
selectors that the live page cannot be made to exhibit on demand.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from contextlib import contextmanager
from html import unescape

import pytest

from ensmcp.domain.models import ApplicabilityLevel, CategoryGroup
from ensmcp.scraping.errors import MeasurePageStructureError
from ensmcp.scraping.navegable_repository import NavegableRepository
from ensmcp.scraping.parsers import parse_dimension_labels
from ensmcp.scraping.selectors import (
    ENS_NAVEGABLE_URL,
    ENS_NORM_JS_PATH,
    MEASURE_ROW_CLASS,
    REQUISITOS_JS_PATH,
)
from tests.support import (
    CHOICE_MEASURE_ROW_HTML,
    CONTENT_PAGE_FILENAME,
    ENS_NORM_JS_FILENAME,
    HARNESS_GUARD_S,
    LOCAL_TIMEOUT_MS,
    MEASURE_ROW_HTML,
    MEASURE_TABLE_HTML,
    MINIMAL_ENS_NORM_JS,
    MINIMAL_REQUISITOS_JS,
    NO_TABLE_HTML,
    OUTER_IFRAME_HTML,
    OUTER_PAGE_FILENAME,
    REINFORCED_MEASURE_ROW_HTML,
    REQUISITOS_JS_FILENAME,
    Utf8RequestHandler,
    check,
    live_session,
    local_repository,
    local_session,
    local_site,
    require,
    requisitos_section,
    requisitos_section_with_a_question,
    site_files,
    table_html,
    threaded_http_server,
)


@pytest.mark.network
async def test_fetch_corpus_returns_the_three_top_level_groups() -> None:
    async with live_session(timeout_ms=60000) as session:
        repository = NavegableRepository(session)
        categories = (await repository.fetch_corpus())[0]

        check(len(categories) > 0)
        check(all(category.code.split(".")[0] in {"org", "op", "mp"} for category in categories))
        groups = {category.group for category in categories}
        check(groups == set(CategoryGroup))


@pytest.mark.network
async def test_fetch_corpus_returns_real_ens_measures_with_descriptions() -> None:
    async with live_session(timeout_ms=60000) as session:
        repository = NavegableRepository(session)
        measures = (await repository.fetch_corpus())[1]

        check(len(measures) > 0)
        check(all(measure.code.split(".")[0] in {"org", "op", "mp"} for measure in measures))
        check(all(measure.dimensions for measure in measures))
        check(any(measure.description for measure in measures))
        codes = {measure.code for measure in measures}
        check("org.1" in codes)


@pytest.mark.network
async def test_every_reinforcement_of_the_real_corpus_carries_its_wording() -> None:
    # The one claim only the live corpus can settle: the reinforcements the
    # summary table names ("+ R1") and the wording norms/ens.js defines line up
    # by code across all 73 measures. A rebuild that renumbered either side
    # would show up here as reinforcements with an empty text.
    async with live_session(timeout_ms=60000) as session:
        repository = NavegableRepository(session)
        measures = (await repository.fetch_corpus())[1]

        reinforcements = [r for measure in measures for r in measure.reinforcements]
        check(len(reinforcements) > 100, f"only {len(reinforcements)} reinforcements found")
        untexted = sorted(
            f"{measure.code} {r.code}"
            for measure in measures
            for r in measure.reinforcements
            if not r.text
        )
        check(not untexted, f"reinforcements with no wording: {untexted}")
        # The choice form the RD uses for op.acc.5 has to survive the live
        # scrape, not just the fixture rows.
        check(any(r.alternative for r in reinforcements))


@pytest.mark.network
async def test_every_measure_of_the_real_corpus_carries_the_wording_of_the_rd() -> None:
    # El hermano del test de arriba, para la otra lectura del mismo asset: las
    # 73 filas de #tablaResumen y los 73 bloques `[medida.k]` de norms/ens.js
    # casan por código. Un renumerado en cualquiera de los dos lados sale aquí
    # como medidas sin redacción — que es servir el examen sin la norma.
    async with live_session(timeout_ms=60000) as session:
        repository = NavegableRepository(session)
        measures = (await repository.fetch_corpus())[1]

        untexted = sorted(measure.code for measure in measures if not measure.norm_text)
        check(not untexted, f"medidas sin la redacción del RD: {untexted}")
        org_4 = next(measure for measure in measures if measure.code == "org.4")
        check(
            org_4.norm_text.startswith("Se establecerá un proceso formal de autorizaciones"),
            f"org.4 empezaba por {org_4.norm_text[:60]!r}",
        )
        # Lo que exige el RD y lo que pregunta la 808 son dos textos distintos.
        check(org_4.description != org_4.norm_text, "description y norm_text coinciden")


async def test_repository_raises_when_the_page_has_no_iframe_at_all() -> None:
    # A local page with no <iframe> at all, so wait_for_selector times out
    # inside LiveSession.start() and its cleanup-and-reraise path runs.
    #
    # This used to point at python.org purely to get "a real page without the
    # ENS iframe". A fixture gives the same shape deterministically, without
    # depending on an unrelated third party staying reachable and iframe-free.
    with local_site({OUTER_PAGE_FILENAME: "<p>no iframe here</p>"}) as base_url:
        async with local_session(base_url, timeout_ms=2000) as session:
            repository = NavegableRepository(session)
            with pytest.raises(MeasurePageStructureError) as excinfo:
                await repository.fetch_corpus()

            check(excinfo.value.selector == "iframe")


async def test_repository_raises_when_no_iframe_matches_the_content_fragment() -> None:
    # A real <iframe>, just not the ENS content one: exercises
    # LiveSession._resolve_content_frame's own deadline, distinct from the
    # "no iframe at all" case above.
    #
    # This used to load w3schools.com, which was heavy and ad-laden enough that
    # a tight timeout flaked under a full-suite run, forcing a 60s timeout. A
    # local fixture is instant and cannot flake.
    with local_site(
        {
            OUTER_PAGE_FILENAME: '<iframe src="something-else.html"></iframe>',
            "something-else.html": "<p>not the ENS content frame</p>",
        }
    ) as base_url:
        async with local_session(base_url, timeout_ms=2000) as session:
            repository = NavegableRepository(session)
            # Con guarda del arnés, por lo mismo que la tiene el test del fetch
            # que se queda colgado: la fecha límite de ``_resolve_content_frame``
            # es un bucle escrito a mano, así que si se rompe no hay nada que
            # corte — el `while True` gira para siempre y **este** test, que es
            # justo el que la prueba, se lleva por delante la suite entera en vez
            # de fallar. Comprobado: rompiéndola a propósito, la ejecución se
            # quedó colgada 25 minutos sin decir nada.
            with pytest.raises(MeasurePageStructureError) as excinfo:
                await asyncio.wait_for(repository.fetch_corpus(), timeout=HARNESS_GUARD_S)

            check(excinfo.value.selector == "ens-navegable-contenido")


async def test_repository_raises_when_the_summary_table_never_appears_in_time() -> None:
    # A real local page with a real matching iframe, but no #tablaResumen in
    # it: deterministically exercises LiveSession._wait_for_summary_table's
    # own error, rather than racing the real site's own render time.
    async with local_repository(site_files(NO_TABLE_HTML), timeout_ms=1000) as repository:
        with pytest.raises(MeasurePageStructureError) as excinfo:
            await repository.fetch_corpus()

        check(excinfo.value.selector == "#tablaResumen")


async def test_scrape_skips_rows_with_no_cells_or_an_unrecognized_class() -> None:
    # #tablaResumen rows outside the two known shapes (category header, measure
    # row) are meant to be skipped rather than raise: exercises both `return
    # None` branches in _parse_row and the loop's corresponding no-op branch.
    # The one real measure row also keeps this the deterministic (non-live-site)
    # coverage of the requisitos.js fetch+merge pipeline: _descriptions_map()
    # is only reached, lazily, once a measure row is actually parsed.
    async with local_repository(
        site_files(
            table_html(
                "<tr></tr>",
                '<tr><td class="not-a-known-class">x</td><td>y</td></tr>',
                '<tr class="fondo_oscuro"><td class="fondo_oscuro">org</td>'
                "<td>Organización</td></tr>",
                MEASURE_ROW_HTML,
            ),
            MINIMAL_REQUISITOS_JS,
            MINIMAL_ENS_NORM_JS,
        )
    ) as repository:
        categories, measures = await repository.fetch_corpus()

        check(len(categories) == 1)
        check(categories[0].code == "org")
        check(len(measures) == 1)
        check(measures[0].code == "org.1")
        check(measures[0].description == "texto")


async def test_a_scraped_measure_carries_the_questionnaire_of_the_asset() -> None:
    """El cuestionario llega a la medida, y con todos sus campos.

    Ningún test offline lo comprobaba: los fixtures de ``requisitos.js`` tienen
    un cuerpo de texto plano y por tanto **cero** preguntas, así que sustituir
    ``audit_requirements=requirements.get(...)`` por una tupla vacía dejaba la
    suite entera en verde. Sólo lo habrían visto los tests marcados ``network``,
    o sea que un CI sin red daba por bueno un scrape sin temario — que es la
    mitad del corpus de la que viven ``alcance_auditoria`` y
    ``requisitos_auditoria``.
    """
    async with local_repository(
        site_files(
            MEASURE_TABLE_HTML,
            f"var requisitos808='{requisitos_section_with_a_question('org.1')}'",
            MINIMAL_ENS_NORM_JS,
        )
    ) as repository:
        measure = require(next(iter((await repository.fetch_corpus())[1]), None))

        requirement = require(next(iter(measure.audit_requirements), None))
        check(len(measure.audit_requirements) == 1, f"llegaron {len(measure.audit_requirements)}")
        check(requirement.position == 0, f"position: {requirement.position}")
        check(requirement.code == "1.1", f"code: {requirement.code!r}")
        check(requirement.level is ApplicabilityLevel.BASICO, f"level: {requirement.level}")
        check(requirement.essential is True, "la pregunta va marcada essential en el fixture")
        check(
            requirement.question == "¿Se cumple lo básico?", f"question: {requirement.question!r}"
        )
        check(requirement.note == "NOTA: una aclaración.", f"note: {requirement.note!r}")


@contextmanager
def _site_that_loses_its_table_after_the_first_load() -> Iterator[str]:
    """La tabla está en la primera carga del frame de contenido y ya no vuelve.

    Con eso, el `refresh()` de la sesión falla en el paso de esperar
    `#tablaResumen`: la recarga y el iframe van bien, y lo que no aparece es la
    tabla. Es la forma de un rediseño, o de la interstitial del WAF.
    """
    loads = {"content": 0}

    class _Handler(Utf8RequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler protocol
            path = self.path.lstrip("/")
            if path == OUTER_PAGE_FILENAME:
                self._send(OUTER_IFRAME_HTML, "text/html")
            elif path == CONTENT_PAGE_FILENAME:
                loads["content"] += 1
                first = loads["content"] == 1
                self._send(MEASURE_TABLE_HTML if first else NO_TABLE_HTML, "text/html")
            elif path == REQUISITOS_JS_FILENAME:
                self._send(MINIMAL_REQUISITOS_JS, "application/javascript")
            elif path == ENS_NORM_JS_FILENAME:
                self._send(MINIMAL_ENS_NORM_JS, "application/javascript")
            else:
                self.send_error(404)

    with threaded_http_server(_Handler) as base_url:
        yield base_url


async def test_a_failed_refresh_still_drops_the_cached_assets() -> None:
    """Las cachés se tiran **antes** de recargar, no después.

    `session.refresh()` puede fallar —un timeout, la interstitial del WAF, una
    tabla que ya no aparece— y tirándolas después ese fallo dejaba cacheado el
    `requisitos.js` de antes de la recarga. El siguiente scrape emparejaría
    medidas recién leídas con descripciones del asset viejo: un descuadre mudo
    si el asset cambió.

    Se mira el estado interno porque no hay observable mejor: tras un refresh
    fallido el frame queda detached hasta el siguiente refresh, y un refresh
    que sí funciona tira las cachés en las dos versiones, así que contar
    peticiones no distingue nada. Es la misma excepción que ya hacen los tests
    que miran `browser._playwright` o `repository._task`.
    """
    with _site_that_loses_its_table_after_the_first_load() as base_url:
        async with local_session(base_url, timeout_ms=2000) as session:
            repository = NavegableRepository(session)
            await repository.fetch_corpus()
            check(repository._requisitos is not None, "el primer scrape no cacheó nada")

            with pytest.raises(MeasurePageStructureError):
                await repository.refresh()

            check(repository._requisitos is None, "el refresh fallido dejó requisitos.js cacheado")
            check(repository._norms is None, "el refresh fallido dejó norms/ens.js cacheado")


def test_ens_navegable_url_points_at_the_real_site() -> None:
    check(ENS_NAVEGABLE_URL == "https://gobernanza.ccn-cert.cni.es/ens-navegable")


async def test_scrape_raises_for_a_category_row_with_too_few_cells() -> None:
    # A row that carries a known category-header class is a data row, not a
    # spacer: too few <td> cells is a broken known shape, which must surface
    # as a clear error instead of an opaque IndexError on texts[1] or being
    # silently skipped (which would lose the category without a signal).
    async with local_repository(
        site_files(table_html('<tr class="fondo_oscuro"><td class="fondo_oscuro">org</td></tr>'))
    ) as repository:
        with pytest.raises(ValueError, match="category header row"):
            await repository.fetch_corpus()


async def test_scrape_reads_the_first_six_cells_of_a_wider_measure_row() -> None:
    # The guard is `len(texts) < 6`, so a row with *more* than six cells is
    # accepted and read from its first six — extra trailing columns are
    # tolerated rather than treated as a broken row.
    #
    # Nothing pinned that, which left the level slice free to widen unnoticed:
    # texts[3:6] and texts[3:7] are identical on a six-cell row, so only a
    # wider row can tell them apart. A widened slice would hand parse_levels
    # four cells and blow up on a row the scraper is meant to handle.
    async with local_repository(
        site_files(
            table_html(
                '<tr class="cuerpo_tabla_izq"><td class="cuerpo_tabla_izq">org.1</td>'
                "<td>Política de seguridad</td><td>Categoría</td>"
                "<td>aplica</td><td>aplica</td><td>aplica</td>"
                "<td>columna de más</td></tr>"
            ),
            MINIMAL_REQUISITOS_JS,
            MINIMAL_ENS_NORM_JS,
        )
    ) as repository:
        measures = (await repository.fetch_corpus())[1]

        check(len(measures) == 1)
        check(measures[0].code == "org.1")
        check(measures[0].title == "Política de seguridad")
        check(measures[0].levels == frozenset(ApplicabilityLevel))


async def test_scrape_raises_for_a_measure_row_with_too_few_cells() -> None:
    # Same contract as the category case, for measure rows: a recognized
    # cuerpo_tabla_izq row with fewer than six cells is a broken measure row
    # (the IndexError on texts[2] would otherwise crash the whole scrape),
    # not an unknown row to skip.
    async with local_repository(
        site_files(
            table_html(
                '<tr class="cuerpo_tabla_izq"><td class="cuerpo_tabla_izq">org.1</td>'
                "<td>Política</td></tr>"
            )
        )
    ) as repository:
        with pytest.raises(ValueError, match="measure row has 2 cell"):
            await repository.fetch_corpus()


async def test_scrape_rejects_a_duplicate_category_code() -> None:
    row = '<tr class="fondo_oscuro"><td class="fondo_oscuro">org</td>' "<td>Organización</td></tr>"
    async with local_repository(site_files(table_html(row, row))) as repository:
        with pytest.raises(ValueError, match=r"duplicate category code: org\b"):
            await repository.fetch_corpus()


async def test_scrape_rejects_73_copies_of_a_measure_code() -> None:
    async with local_repository(
        site_files(
            table_html(*([MEASURE_ROW_HTML] * 73)),
            MINIMAL_REQUISITOS_JS,
            MINIMAL_ENS_NORM_JS,
        )
    ) as repository:
        with pytest.raises(ValueError, match=r"duplicate measure code: org\.1\b"):
            await repository.fetch_corpus()


async def test_concurrent_refresh_and_scrape_keep_state_consistent() -> None:
    # The MCP server dispatches tool calls concurrently against this one
    # repository; a refresh_live_page reloads the page (detaching every
    # ElementHandle an in-flight scrape is iterating) and clears the cached
    # description map. Without the repository lock serialising _scrape()
    # against refresh(), the race either crashes the scrape (detached element)
    # or lets an in-flight description fetch overwrite the invalidation with
    # stale text. The lock makes both serialise; the final state is the one
    # measure the fixture serves, intact.
    async with local_repository(
        site_files(MEASURE_TABLE_HTML, MINIMAL_REQUISITOS_JS, MINIMAL_ENS_NORM_JS)
    ) as repository:
        await repository.fetch_corpus()

        await asyncio.gather(
            repository.refresh(),
            repository.fetch_corpus(),
            repository.fetch_corpus(),
        )

        measures = (await repository.fetch_corpus())[1]
        check(len(measures) == 1)
        check(measures[0].code == "org.1")


# El sitio sirve la misma tabla dos veces: la inyecta en el DOM y la trae
# pre-renderizada en este asset. No se usa en producción —el DOM es la fuente—
# pero es la única representación independiente que hay de esos datos.
_TABLA_JS_PATH = "/build/navigableens/tabla.js"

# Escapes de JS que el navegador resuelve al pintar y que este asset trae
# crudos, porque aquí se lee el fichero, no la página.
_JS_ESCAPES = re.compile(r"\\[tnr]")


def _rows_of(source: str) -> dict[str, list[str]]:
    """Las filas de medida del asset, sin navegador y sin los parsers.

    Un camino aparte a propósito: regex sobre el HTML, entidades resueltas y
    espacios colapsados. Si coincidiera con el scrape por compartir código no
    probaría nada.
    """
    rows = {}
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", source, re.S | re.I):
        cells = re.findall(r"<td\b([^>]*)>(.*?)</td>", row, re.S | re.I)
        classes = re.search(r'class\s*=\s*"([^"]*)"', cells[0][0]) if cells else None
        if not classes or classes.group(1) != MEASURE_ROW_CLASS or len(cells) < 6:
            continue
        texts = [
            " ".join(unescape(_JS_ESCAPES.sub(" ", re.sub(r"<[^>]+>", " ", body))).split())
            for _, body in cells
        ]
        rows[texts[0]] = texts
    return rows


@pytest.mark.network
async def test_the_scraped_table_agrees_with_the_sites_own_prerendered_copy() -> None:
    # El punto ciego del test que compara el snapshot contra re-escrapear: si el
    # parser lee mal la tabla, los dos lados salen igual de mal y pasa. Esto lo
    # cierra contrastando contra la **otra** representación que el sitio publica
    # de los mismos datos, leída por un camino que no comparte una línea con el
    # scrape: sin navegador, sin `#tablaResumen`, sin `parse_measure`.
    async with live_session(timeout_ms=60000) as session:
        expected = _rows_of(await session.fetch_asset(_TABLA_JS_PATH))
        measures = (await NavegableRepository(session).fetch_corpus())[1]

    check(len(expected) == 73, f"tabla.js trajo {len(expected)} filas de medida")
    check({m.code for m in measures} == set(expected), "los códigos no coinciden")
    for measure in measures:
        row = expected[measure.code]
        check(measure.title == row[1], f"{measure.code}: {measure.title!r} vs {row[1]!r}")
        check(
            list(measure.raw_levels) == row[3:6],
            f"{measure.code}: celdas {measure.raw_levels} vs {row[3:6]}",
        )
        # La columna de dimensiones es la que decide a qué medidas alcanza una
        # DdA, así que se contrasta el literal, no lo ya interpretado.
        check(row[2] == row[2].strip(), f"{measure.code}: columna de dimensión con bordes")
        check(
            measure.dimensions == parse_dimension_labels(row[2]),
            f"{measure.code}: dimensiones {measure.dimensions} vs columna {row[2]!r}",
        )


@contextmanager
def _site_whose_norm_asset_fails(status: int, body: str = "") -> Iterator[str]:
    """Sirve la tabla y requisitos.js bien, y el asset de la norma roto.

    Un servidor real: `fetch` resuelve igual de contento con un 404 que con el
    fichero, así que la única forma de probar la diferencia es que un origen
    real conteste con ese estado.
    """

    class _Handler(Utf8RequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler protocol
            path = self.path.lstrip("/")
            if path == OUTER_PAGE_FILENAME:
                self._send(OUTER_IFRAME_HTML, "text/html")
            elif path == CONTENT_PAGE_FILENAME:
                self._send(MEASURE_TABLE_HTML, "text/html")
            elif path == REQUISITOS_JS_FILENAME:
                self._send(MINIMAL_REQUISITOS_JS, "application/javascript")
            elif path == ENS_NORM_JS_FILENAME and status == 200:
                self._send(body, "application/javascript")
            elif path == ENS_NORM_JS_FILENAME:
                self.send_error(status)
            else:
                self.send_error(404)

    with threaded_http_server(_Handler) as base_url:
        yield base_url


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (404, "", "HTTP 404"),
        (500, "", "HTTP 500"),
        (200, "<html><body>Error 503</body></html>", "carries no measure wording"),
        (200, "", "carries no measure wording"),
        # Truncado a media descarga: sigue trayendo bloques, así que "vacío" no
        # lo caza. Este define mp.s.4 y no org.1, que es la fila de la tabla.
        (
            200,
            'var ENScompleto=["[mp.s.4.1] Se planificará la capacidad."]',
            "has no wording for org.1",
        ),
    ],
    ids=["not-found", "server-error", "error-page-with-200", "empty-with-200", "truncated"],
)
async def test_a_broken_norm_asset_stops_the_scrape_instead_of_emptying_it(
    status: int, body: str, expected: str
) -> None:
    # `fetch` resuelve igual de contento con un 404 que con el fichero, así que
    # el cuerpo del error llegaba al parser como si fuera el asset. El de
    # requisitos.js se salvaba de casualidad —comprueba la forma del literal y
    # se negaba—, pero el de la norma no tiene forma que comprobar: devolvía un
    # diccionario vacío y el scrape terminaba **sin error**, con las 73 medidas
    # sin redacción y todos los refuerzos sin la suya.
    #
    # Y eso se adopta: `build_snapshot` sólo cuenta filas, así que habría
    # escrito ese corpus; y la comprobación de arranque lo habría puesto en
    # memoria encima de datos correctos.
    with _site_whose_norm_asset_fails(status, body) as base_url:
        async with local_session(base_url) as session:
            with pytest.raises(Exception) as excinfo:
                await NavegableRepository(session).fetch_corpus()

    check(expected in str(excinfo.value), f"el mensaje fue: {str(excinfo.value)[:160]}")
    check(ENS_NORM_JS_PATH in str(excinfo.value), "el mensaje no nombra el asset")


async def test_a_row_the_questionnaire_asset_does_not_name_stops_the_scrape() -> None:
    # El mismo contrato que el asset de la norma, sobre requisitos.js, que
    # estaba sujeto a uno más débil: un bloque ausente caía a `""` por defecto y
    # a cero requisitos de auditoría. Así que una medida que el asset dejase de
    # nombrar —una grafía de código que deriva, algo que este corpus hace
    # ("[mp.s. 4.r1.2]", "artáculo")— se publicaba con la descripción en blanco
    # *y* sin temario, sin una queja: cero preguntas no es de por sí anómalo,
    # tres medidas reales no tienen ninguna en básico. `alcance_auditoria` le
    # habría dicho al auditor que de esa medida no hay nada que preguntar.
    #
    # El asset de abajo está bien formado y define op.acc.5, no la org.1 que
    # nombra la tabla: es la incoherencia entre las dos, no un fichero roto.
    async with local_repository(
        site_files(
            MEASURE_TABLE_HTML,
            f"var requisitos808='{requisitos_section('op.acc.5')}'",
            MINIMAL_ENS_NORM_JS,
        )
    ) as repository:
        with pytest.raises(ValueError, match=r"has no questionnaire for org\.1") as excinfo:
            await repository.fetch_corpus()

    check(REQUISITOS_JS_PATH in str(excinfo.value), "el mensaje no nombra el asset")


# El mismo asset de la norma, pero sin el bloque del refuerzo de mp.s.4: define
# la redacción de la **medida** y no la de su R1. Es lo que deja un corte a media
# descarga, porque los dos bloques no van juntos en el fichero.
_NORM_JS_WITHOUT_THE_REINFORCEMENT_BLOCK = (
    'var ENScompleto=["Se establecerán medidas preventivas frente a DoS. Para ello:\\r\\n'
    '\u2013 [mp.s.4.1] Se planificará la capacidad del sistema.\\r\\n"]'
)


async def test_a_reinforcement_the_norm_asset_does_not_word_stops_the_scrape() -> None:
    # Las otras dos guardas sólo miran la redacción de la *medida*, así que un
    # asset truncado entre el bloque de una medida y el de sus refuerzos las
    # pasaba de largo: la fila mp.s.4 pide "+ R1" en alto, el R1 salía con el
    # texto en blanco y nadie se quejaba. Medido sobre el asset real, hasta 12
    # medidas caen en esa ventana, `op.acc.5` entre ellas — cuya celda de nivel
    # bajo es "+ [R1 o R2 o R3 o R4]", así que la Declaración de Aplicabilidad
    # diría "implanta uno de los cuatro" sin decir qué es ninguno.
    async with local_repository(
        site_files(
            table_html(REINFORCED_MEASURE_ROW_HTML),
            MINIMAL_REQUISITOS_JS,
            _NORM_JS_WITHOUT_THE_REINFORCEMENT_BLOCK,
        )
    ) as repository:
        expected = r"has no wording for mp\.s\.4 refuerzos R1"
        with pytest.raises(ValueError, match=expected) as excinfo:
            await repository.fetch_corpus()

    check(ENS_NORM_JS_PATH in str(excinfo.value), "el mensaje no nombra el asset")


@contextmanager
def _counting_site() -> Iterator[tuple[str, dict[str, int]]]:
    """Un sitio de fixture que cuenta cuántas veces se pide cada ruta.

    Un servidor real contando peticiones reales: es la única forma de afirmar
    "esto se pide una vez" sin doblar nada, que es lo que prohíbe CLAUDE.md.
    """
    hits: dict[str, int] = {}

    class _Handler(Utf8RequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler protocol
            path = self.path.lstrip("/")
            hits[path] = hits.get(path, 0) + 1
            if path == OUTER_PAGE_FILENAME:
                self._send(OUTER_IFRAME_HTML, "text/html")
            elif path == CONTENT_PAGE_FILENAME:
                rows = (MEASURE_ROW_HTML, REINFORCED_MEASURE_ROW_HTML, CHOICE_MEASURE_ROW_HTML)
                self._send(table_html(*rows), "text/html")
            elif path == REQUISITOS_JS_FILENAME:
                self._send(MINIMAL_REQUISITOS_JS, "application/javascript")
            elif path == ENS_NORM_JS_FILENAME:
                self._send(MINIMAL_ENS_NORM_JS, "application/javascript")
            else:
                self.send_error(404)

    with threaded_http_server(_Handler) as base_url:
        yield base_url, hits


async def test_each_build_asset_is_fetched_once_per_scrape_not_once_per_row() -> None:
    """ "Cada asset se pide una vez y se cachea por sesión", que nadie afirmaba.

    Las dos guardas de caché (`_load_requisitos` y `_load_norms`) se consultan
    **por fila**, así que si una se degrada el asset se vuelve a pedir en cada
    medida. El corpus no cambia —se re-parsea lo mismo—, por eso ningún test lo
    veía: mutando cualquiera de las dos, la suite entera seguía en verde.

    Lo que cambia es el tráfico: 73 descargas por scrape en vez de una, contra un
    origen protegido por un WAF que ya bloquea todo lo que no parezca un
    navegador. Con las tres filas de este fixture bastan para distinguirlo.
    """
    with _counting_site() as (base_url, hits):
        async with local_session(base_url, timeout_ms=LOCAL_TIMEOUT_MS) as session:
            measures = (await NavegableRepository(session).fetch_corpus())[1]

    check(len(measures) == 3, f"se scrapearon {len(measures)} medidas")
    check(hits.get(REQUISITOS_JS_FILENAME) == 1, f"requisitos.js: {hits}")
    check(hits.get(ENS_NORM_JS_FILENAME) == 1, f"norms/ens.js: {hits.get(ENS_NORM_JS_FILENAME)}")


async def test_two_concurrent_starts_launch_a_single_browser() -> None:
    """El doble chequeo de `LiveSession.start()`, que sólo una carrera distingue.

    El `return` de dentro del lock es lo que impide que la segunda corrutina —la
    que llega cuando la primera ya ha arrancado— se meta en la rama de
    recuperación y tire abajo un navegador perfectamente vivo para volver a
    levantarlo. La comprobación de fuera del lock no lo cubre: con la sesión ya
    arrancada devuelve antes de llegar aquí, así que este camino **sólo** se
    recorre en la carrera que el propio docstring de `start()` describe.

    Se cuentan peticiones reales al servidor en vez de mirar campos internos: dos
    navegadores son dos cargas de la página exterior.
    """
    with _counting_site() as (base_url, hits):
        async with local_session(base_url, timeout_ms=LOCAL_TIMEOUT_MS) as session:
            await asyncio.gather(session.start(), session.start())

    check(hits.get(OUTER_PAGE_FILENAME) == 1, f"la página exterior se cargó {hits} veces")
