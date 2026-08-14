"""Test for the frame lookup of ``scripts/capture_dom.py``.

Navegador real sobre un sitio local real, como el resto de la suite: la
herramienta trabaja con una ``Page`` de Patchright y no hay forma de probarla
sin una, ni la habría con mocks, que este proyecto no usa.

Lo que se prueba es el modo de fallo, que es lo único que esta herramienta
comparte con el servidor: si el sitio deja de servir el iframe de contenido, el
mensaje tiene que decirlo.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from capture_dom import _capture_measure_detail, _content_frame, capture
from patchright.async_api import Error as PlaywrightError

from ensmcp.scraping.errors import MeasurePageStructureError
from ensmcp.scraping.persistent_context import PersistentBrowserContext
from ensmcp.scraping.selectors import (
    CONTENT_IFRAME_URL_FRAGMENT,
    SUMMARY_TABLE_SELECTOR,
)
from tests.support import (
    CONTENT_PAGE_FILENAME,
    MEASURE_TABLE_HTML,
    NO_TABLE_HTML,
    OUTER_IFRAME_HTML,
    OUTER_PAGE_FILENAME,
    REINFORCED_MEASURE_ROW_HTML,
    check,
    leftover_profiles,
    local_site,
    table_html,
)


async def test_a_page_without_the_content_frame_says_so() -> None:
    # Antes, un `next()` sin defecto levantaba `StopIteration` dentro de una
    # corrutina, y Python lo convierte en "RuntimeError: coroutine raised
    # StopIteration": un mensaje que no nombra ni el frame ni la página, en la
    # herramienta cuyo trabajo es diagnosticar qué está sirviendo el sitio.
    # Es lo que se ve ante una interstitial del WAF o un rediseño.
    files = {
        OUTER_PAGE_FILENAME: '<iframe src="otra-cosa.html"></iframe>',
        "otra-cosa.html": "<p>no es el frame de contenido</p>",
    }
    # Chromium headless (channel=None), no el Chrome headed que sólo hace falta
    # para pasar el WAF del sitio real.
    browser = PersistentBrowserContext(headless=True, channel=None)
    try:
        page = await browser.open()
        with local_site(files) as base_url:
            await page.goto(f"{base_url}/{OUTER_PAGE_FILENAME}", wait_until="domcontentloaded")

            with pytest.raises(MeasurePageStructureError) as excinfo:
                await _content_frame(page)

        check(excinfo.value.selector == CONTENT_IFRAME_URL_FRAGMENT, f"{excinfo.value.selector!r}")
        check(CONTENT_IFRAME_URL_FRAGMENT in str(excinfo.value), f"mensaje: {excinfo.value}")
    finally:
        await browser.close()


async def test_a_content_frame_without_the_summary_table_says_so() -> None:
    # La otra mitad del mismo fallo, y la que faltaba: el frame de contenido
    # resuelve, pero `#tablaResumen` no aparece —un rediseño, o la tabla que el
    # script del propio frame nunca llega a inyectar—. Salía el `TimeoutError`
    # pelado de Patchright, que no nombra ni la página ni el frame, en la
    # herramienta cuyo trabajo es diagnosticar qué sirve el sitio.
    # `LiveSession._wait_for_summary_table` convierte este mismo caso.
    files = {OUTER_PAGE_FILENAME: OUTER_IFRAME_HTML, CONTENT_PAGE_FILENAME: NO_TABLE_HTML}
    browser = PersistentBrowserContext(headless=True, channel=None)
    try:
        page = await browser.open()
        with local_site(files) as base_url:
            # networkidle, no domcontentloaded: hace falta que el frame hijo haya
            # navegado ya a su src, o el fallo sería el de arriba (no hay frame)
            # y este camino no se tocaría.
            await page.goto(f"{base_url}/{OUTER_PAGE_FILENAME}", wait_until="networkidle")

            with pytest.raises(MeasurePageStructureError) as excinfo:
                # Un timeout corto: el sitio de fixture contesta al instante, y
                # esperar los 45 s de una captura real no prueba nada más.
                await _content_frame(page, timeout_ms=2000)

        check(excinfo.value.selector == SUMMARY_TABLE_SELECTOR, f"{excinfo.value.selector!r}")
        check(CONTENT_PAGE_FILENAME in str(excinfo.value), f"no nombra el frame: {excinfo.value}")
    finally:
        await browser.close()


async def test_a_failing_url_log_write_still_tears_the_browser_down() -> None:
    """El `finally` de `capture()`, que hasta ahora no tocaba nadie.

    El log de URLs se escribe en un `finally` para que una captura que revienta
    a medias lo deje igualmente — es la más barata de las tres respuestas que la
    herramienta busca. Pero esa escritura puede fallar ella misma (directorio de
    salida lleno o de sólo lectura), y sin su propio `try` se lleva por delante
    el cierre del navegador: `browser.close()` no llega a correr y la ejecución
    se deja un Chrome vivo y su perfil temporal en el disco.

    Aquí se provoca de verdad, poniendo un **directorio** donde va el fichero,
    que es lo que hace que `write_text` lance sin tener que tocar permisos (y
    sin depender de si el test corre como root, que en un contenedor de CI pasa).
    """
    before = leftover_profiles()
    files = {
        OUTER_PAGE_FILENAME: OUTER_IFRAME_HTML,
        CONTENT_PAGE_FILENAME: table_html(REINFORCED_MEASURE_ROW_HTML),
    }
    with TemporaryDirectory() as temporary:
        output_dir = Path(temporary) / "captura"
        # Un directorio con el nombre del fichero: `write_text` no puede.
        (output_dir / "responses.txt").mkdir(parents=True)

        with local_site(files) as base_url, pytest.raises(OSError):
            await capture(
                output_dir,
                f"{base_url}/{OUTER_PAGE_FILENAME}",
                headless=True,
                channel=None,
            )

    leaked = leftover_profiles() - before
    check(not leaked, f"la escritura fallida se dejó el navegador: {sorted(leaked)}")


async def test_a_failing_detail_click_is_recorded_and_propagated() -> None:
    """La captura conserva el diagnóstico sin convertir el fallo en éxito."""
    files = {OUTER_PAGE_FILENAME: OUTER_IFRAME_HTML, CONTENT_PAGE_FILENAME: MEASURE_TABLE_HTML}
    browser = PersistentBrowserContext(headless=True, channel=None)
    try:
        page = await browser.open()
        with local_site(files) as base_url:
            await page.goto(f"{base_url}/{OUTER_PAGE_FILENAME}", wait_until="networkidle")
            frame = await _content_frame(page)
        await page.goto("about:blank")

        with TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            with pytest.raises(PlaywrightError):
                await _capture_measure_detail(frame, output_dir)

            error = output_dir / "measure-detail-error.txt"
            check(error.is_file(), "el fallo no dejó measure-detail-error.txt")
            check(bool(error.read_text(encoding="utf-8")), "el diagnóstico del clic quedó vacío")
    finally:
        await browser.close()
