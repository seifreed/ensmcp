"""Tests for the write guard of ``scripts/build_snapshot.py``.

El script sobrescribe el fichero que el paquete distribuye, así que lo que hay
que probar es justo lo que **no** debe escribir. Puro: la comprobación no toca
el navegador ni la red, sólo mira lo que el scrape devolvió frente a lo que ya
hay en disco.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from atomic_write import write_atomic
from build_snapshot import build, check

from ensmcp.scraping.live_session import LiveSession
from ensmcp.snapshot.codec import SCHEMA_VERSION, dump
from ensmcp.snapshot.repository import SnapshotRepository, default_snapshot_text
from tests.support import (
    MEASURE_ROW_HTML,
    MINIMAL_ENS_NORM_JS,
    MINIMAL_REQUISITOS_JS,
    local_session,
    local_site,
    site_files,
    table_html,
)
from tests.support import check as assert_that


@asynccontextmanager
async def local_repository_session(content_html: str) -> AsyncIterator[LiveSession]:
    """Una LiveSession sobre un sitio de fixture, para llamar a ``build``."""
    with local_site(site_files(content_html, MINIMAL_REQUISITOS_JS, MINIMAL_ENS_NORM_JS)) as base:
        async with local_session(base) as session:
            yield session


_SHIPPED = SnapshotRepository.from_package_data()
_CATEGORIES, _MEASURES = list(_SHIPPED.categories), list(_SHIPPED.measures)
_PREVIOUS = default_snapshot_text()


def test_atomic_write_keeps_the_previous_file_when_writing_fails() -> None:
    with TemporaryDirectory() as directory:
        output = Path(directory) / "anexo_ii.json"
        output.write_bytes(b"previous\n")

        with pytest.raises(UnicodeEncodeError):
            write_atomic(output, "partial\ud800")

        assert_that(output.read_bytes() == b"previous\n", "se dañó el fichero anterior")
        assert_that(list(output.parent.iterdir()) == [output], "quedó un temporal sin limpiar")


def test_the_real_corpus_passes_its_own_guard() -> None:
    # Lo que el script acaba de escribir tiene que poder volver a escribirse.
    check(_CATEGORIES, _MEASURES, _PREVIOUS)
    check(_CATEGORIES, _MEASURES, None)


def test_an_empty_scrape_never_overwrites_the_snapshot() -> None:
    # El fallo que esto existe para parar: `dump([], [])` es un fichero válido
    # de 110 bytes que carga sin una queja y deja el servidor sirviendo cero
    # medidas. Y es alcanzable — LiveSession espera a que #tablaResumen sea
    # *visible*, no a que tenga filas, y las filas las inyecta el JS después.
    with pytest.raises(ValueError, match="no se scrapeó ninguna medida"):
        check(_CATEGORIES, [], _PREVIOUS)

    with pytest.raises(ValueError, match="no se scrapeó ninguna categoría"):
        check([], _MEASURES, _PREVIOUS)

    # Sin fichero previo tampoco: un primer scrape vacío es igual de malo.
    with pytest.raises(ValueError, match="no se scrapeó ninguna medida"):
        check([], [], None)


def test_a_partial_scrape_never_shrinks_the_snapshot() -> None:
    # Una tabla a medio inyectar da filas, pero menos. El recuento exacto no
    # sirve de guarda —que el ENS cambie es lo que este script recoge— así que
    # lo que se exige es que no encoja.
    with pytest.raises(ValueError, match="medidas: 73 en el fichero actual, 5 ahora") as excinfo:
        check(_CATEGORIES, _MEASURES[:5], _PREVIOUS)

    assert_that("borra el fichero de destino" in str(excinfo.value), "el mensaje no dice qué hacer")

    with pytest.raises(ValueError, match="categorías: 18 en el fichero actual, 2 ahora"):
        check(_CATEGORIES[:2], _MEASURES, _PREVIOUS)


def test_growing_is_allowed_because_the_ens_may_add_measures() -> None:
    # La asimetría es deliberada: recoger una ampliación del Anexo II es
    # exactamente para lo que existe el script.
    smaller = dump(_CATEGORIES[:1], _MEASURES[:1], _SHIPPED.captured_at)

    check(_CATEGORIES, _MEASURES, smaller)


def test_the_guard_survives_a_snapshot_from_an_older_schema() -> None:
    # La guarda se apagaba a sí misma justo cuando más falta hace: el primer
    # scrape tras subir SCHEMA_VERSION. Contaba con `codec.load`, que rechaza
    # cualquier otra versión, así que reventaba —tras un scrape completo con
    # Chrome— con "snapshot schema version 2, expected 3": un mensaje que parece
    # culpar al scrape. Un recuento no necesita el dominio.
    stale = json.loads(_PREVIOUS)
    stale["schema_version"] = SCHEMA_VERSION - 1
    for measure in stale["measures"]:
        del measure["norm_text"]
    older = json.dumps(stale)

    check(_CATEGORIES, _MEASURES, older)
    with pytest.raises(ValueError, match="medidas: 73 en el fichero actual, 5 ahora"):
        check(_CATEGORIES, _MEASURES[:5], older)


def test_an_unreadable_snapshot_has_nothing_to_shrink_from() -> None:
    # Un fichero corrupto no puede decir cuántas medidas tenía, y bloquear la
    # regeneración es lo contrario de lo que hace falta con un fichero corrupto.
    # Cuenta como cero; el scrape vacío lo siguen parando las otras dos guardas.
    for broken in ("no es json", "[]", '{"schema_version": 3}'):
        check(_CATEGORIES, _MEASURES, broken)

    with pytest.raises(ValueError, match="no se scrapeó ninguna medida"):
        check(_CATEGORIES, [], "no es json")


# Una tabla mínima pero completa: una fila de categoría y una de medida, que es
# lo que `check` exige (ninguna de las dos listas puede salir vacía).
_CATEGORY_ROW_HTML = (
    '<tr class="fondo_oscuro"><td class="fondo_oscuro">org</td><td>Organización</td></tr>'
)
_TINY_TABLE_HTML = table_html(_CATEGORY_ROW_HTML, MEASURE_ROW_HTML)


async def test_build_writes_the_snapshot_it_scraped() -> None:
    # El camino feliz de la función entera, que nadie recorría: `build` sólo se
    # podía llamar con Chrome y la web real delante, así que ni esto ni las
    # guardas de abajo tenían test.
    with TemporaryDirectory() as directory:
        output = Path(directory) / "anexo_ii.json"
        async with local_repository_session(_TINY_TABLE_HTML) as session:
            categories, measures = await build(output, session)

        assert_that((categories, measures) == (1, 1), f"salieron {categories} y {measures}")
        written = SnapshotRepository(output.read_text(encoding="utf-8"))
        assert_that([m.code for m in written.measures] == ["org.1"], "no se escribió lo scrapeado")


async def test_build_refuses_to_shrink_the_file_it_would_overwrite() -> None:
    """La guarda tiene que estar **conectada**, no sólo existir.

    `check` y `_previous` se probaban sueltas, y eso es lo que no basta:
    quitándole a `build` la llamada a `check`, o dejando de leer el fichero
    anterior, la suite entera seguía en verde con las dos guardas intactas y
    desconectadas — el scrape de una medida habría machacado las 73 del fichero.
    Es el mismo fallo que `build_wiring` existe para impedir en `__main__`.
    """
    with TemporaryDirectory() as directory:
        output = Path(directory) / "anexo_ii.json"
        output.write_text(_PREVIOUS, encoding="utf-8")

        async with local_repository_session(_TINY_TABLE_HTML) as session:
            with pytest.raises(ValueError, match="el scrape parece incompleto"):
                await build(output, session)

        assert_that(output.read_text(encoding="utf-8") == _PREVIOUS, "se sobrescribió el fichero")
