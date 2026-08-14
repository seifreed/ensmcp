"""Lifecycle tests for the warm LiveSession.

Only the two tests marked ``network`` hit the real site, to confirm a scrape
and a refresh work against it end to end. Everything else — self-healing
after the browser dies, concurrent starts, a refresh that fails midway, the
description fetch timing out, and the ``close()`` no-op that never starts a
browser at all — runs against local fixture servers, because each one needs
the page to misbehave in a specific way on cue.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from patchright.async_api import Error as PlaywrightError

from ensmcp.scraping.errors import MeasurePageStructureError
from ensmcp.scraping.live_session import LiveSession
from ensmcp.scraping.navegable_repository import NavegableRepository
from tests.support import (
    CONTENT_PAGE_FILENAME,
    ENS_NORM_JS_FILENAME,
    HARNESS_GUARD_S,
    MEASURE_TABLE_HTML,
    MINIMAL_ENS_NORM_JS,
    MINIMAL_REQUISITOS_JS,
    NO_TABLE_HTML,
    OUTER_IFRAME_HTML,
    OUTER_PAGE_FILENAME,
    REQUISITOS_JS_FILENAME,
    Utf8RequestHandler,
    check,
    leftover_profiles,
    live_session,
    local_session,
    local_site,
    require,
    site_files,
    threaded_http_server,
)

# The stalling fixture below must outlast the harness guard, so that "the
# request never gets answered" is what the test actually exercises.
# ``HARNESS_GUARD_S`` vive en ``tests.support`` porque hay dos tests con la misma
# necesidad, y el otro es el de la fecha límite de ``_resolve_content_frame``.
STALL_SECONDS = 60.0


@contextmanager
def mutable_content_site() -> Iterator[str]:
    """Serve a local site whose content frame answers #tablaResumen once only.

    A real HTTP server over a real socket (not a mock): the content page is
    served with the summary table on its first request and without it on every
    later one, so a page reload deterministically makes
    ``LiveSession._wait_for_summary_table`` time out — a refresh failure —
    while the first scrape still resolves a content frame successfully. The
    outer page and requisitos.js are served the same on every request.
    """
    state = {"content_requests": 0}

    class _Handler(Utf8RequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler protocol
            if self.path == f"/{OUTER_PAGE_FILENAME}":
                self._send(OUTER_IFRAME_HTML, "text/html")
            elif self.path == f"/{CONTENT_PAGE_FILENAME}":
                state["content_requests"] += 1
                body = MEASURE_TABLE_HTML if state["content_requests"] == 1 else NO_TABLE_HTML
                self._send(body, "text/html")
            elif self.path == f"/{REQUISITOS_JS_FILENAME}":
                self._send(MINIMAL_REQUISITOS_JS, "application/javascript")
            elif self.path == f"/{ENS_NORM_JS_FILENAME}":
                self._send(MINIMAL_ENS_NORM_JS, "application/javascript")
            else:
                self.send_error(404)

    with threaded_http_server(_Handler) as base_url:
        yield base_url


@contextmanager
def stalling_requisitos_site() -> Iterator[str]:
    """Serve a local site whose requisitos.js accepts the request but stalls.

    A real HTTP server that answers the outer and content pages normally, so a
    scrape gets as far as parsing a measure row, and then holds the
    requisitos.js connection open without ever responding. That is what a
    hung, throttled or hostile origin looks like from the browser's side, and
    it is the case an in-page ``fetch`` with no abort signal waits on forever.
    """

    class _Handler(Utf8RequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler protocol
            if self.path == f"/{OUTER_PAGE_FILENAME}":
                self._send(OUTER_IFRAME_HTML, "text/html")
            elif self.path == f"/{CONTENT_PAGE_FILENAME}":
                self._send(MEASURE_TABLE_HTML, "text/html")
            elif self.path == f"/{REQUISITOS_JS_FILENAME}":
                # Accept, then never answer. Daemon thread, so it cannot keep
                # the suite alive once the test has moved on.
                time.sleep(STALL_SECONDS)
            else:
                self.send_error(404)

    with threaded_http_server(_Handler) as base_url:
        yield base_url


async def test_description_fetch_times_out_instead_of_hanging_forever() -> None:
    # Every other browser call in LiveSession passes timeout=self._timeout_ms;
    # the in-page requisitos.js fetch did not, and page.evaluate() takes no
    # timeout of its own. An origin that accepts the connection and never
    # responds therefore blocked the fetch forever — and because
    # NavegableRepository._scrape() holds its lock across the lazy description
    # fetch, that one stall wedged *every* later tool call too. The abort
    # signal must cut it loose at timeout_ms.
    with stalling_requisitos_site() as base_url:
        async with local_session(base_url, timeout_ms=2000) as session:
            repository = NavegableRepository(session)
            started = time.monotonic()

            # asyncio.wait_for is only the harness guard: if the fetch still
            # hangs, this raises TimeoutError instead of the expected
            # PlaywrightError and the test fails loudly rather than blocking
            # the whole suite.
            with pytest.raises(PlaywrightError):
                await asyncio.wait_for(repository.fetch_corpus(), timeout=HARNESS_GUARD_S)

            elapsed = time.monotonic() - started
            check(elapsed < HARNESS_GUARD_S / 2, f"fetch took {elapsed:.1f}s, expected ~2s")


@pytest.mark.network
async def test_two_scrapes_reuse_the_same_browser_page() -> None:
    async with live_session(timeout_ms=60000) as session:
        repository = NavegableRepository(session)
        first = (await repository.fetch_corpus())[1]
        second = (await repository.fetch_corpus())[1]

        check(len(first) == len(second))
        check(first == second)
        check(await session.frame() is await session.frame())


async def test_concurrent_start_calls_land_on_one_consistent_session() -> None:
    # Nothing in the MCP protocol serialises tool calls, so several coroutines
    # can call start() (via frame()/fetch_corpus()/...) before any of them
    # has set _started — this is what start()'s lock guards against. Whichever
    # of the gathered calls acquires the lock first does the real launch and
    # sets _started before releasing it; asyncio.Lock's own handoff ordering
    # guarantees every other one only gets the lock afterwards and takes the
    # early-return branch — deterministic, not a timing gamble. What's
    # observable without reaching into internals is that all of them land on
    # one working, self-consistent session rather than raising or diverging.
    #
    # A local fixture site, not the live one: what is under test is the lock in
    # start(), which behaves the same whichever page is being loaded.
    with local_site(
        site_files(MEASURE_TABLE_HTML, MINIMAL_REQUISITOS_JS, MINIMAL_ENS_NORM_JS)
    ) as base_url:
        async with local_session(base_url) as session:
            await asyncio.gather(*(session.start() for _ in range(5)))

            check(await session.frame() is await session.frame())


async def test_start_relaunches_after_the_browser_dies_externally() -> None:
    # A real crash simulation, not a mock: closing the actual Patchright page
    # is exactly what an external crash, an OOM kill, or someone closing the
    # browser window all look like from LiveSession's side (verified by hand
    # first: page.is_closed() flips to True, and a plain start() afterwards
    # transparently relaunches). The next call must self-heal instead of
    # failing forever on a dead page.
    with local_site(
        site_files(MEASURE_TABLE_HTML, MINIMAL_REQUISITOS_JS, MINIMAL_ENS_NORM_JS)
    ) as base_url:
        async with local_session(base_url) as session:
            repository = NavegableRepository(session)
            await repository.fetch_corpus()

            dead_page = require(session._page, "the first scrape must have opened a page")
            await dead_page.close()
            check(dead_page.is_closed())

            measures = (await repository.fetch_corpus())[1]

            check(len(measures) == 1)
            check(measures[0].code == "org.1")
            new_page = require(session._page, "the session must have relaunched")
            check(new_page is not dead_page)
            check(not new_page.is_closed())


@pytest.mark.network
async def test_refresh_reloads_the_live_page() -> None:
    async with live_session(timeout_ms=60000) as session:
        repository = NavegableRepository(session)
        await repository.fetch_corpus()
        await repository.refresh()
        measures = (await repository.fetch_corpus())[1]

        check(len(measures) > 0)


async def test_close_is_a_noop_when_never_started() -> None:
    session = LiveSession()
    await session.close()
    await session.close()


async def test_refresh_failure_does_not_leave_a_stale_detached_frame_cached() -> None:
    # The first scrape resolves a content frame (the local server serves
    # #tablaResumen on the first content request). refresh() then reloads; the
    # content frame's second request serves no #tablaResumen, so
    # _wait_for_summary_table times out and refresh raises. page.reload() has
    # already detached the old frame by then, so unless refresh drops the cached
    # frame first, that stale detached frame stays cached and the next
    # fetch_corpus() crashes on an opaque detached-element error instead of
    # re-resolving and surfacing a clear MeasurePageStructureError.
    with mutable_content_site() as base_url:
        async with local_session(base_url, timeout_ms=2000) as session:
            repository = NavegableRepository(session)
            measures = (await repository.fetch_corpus())[1]
            check(len(measures) == 1)

            with pytest.raises(MeasurePageStructureError):
                await repository.refresh()

            # The session must not be wedged on a detached frame: the next
            # scrape re-resolves the content frame, finds no table again, and
            # raises a clear structural error rather than an opaque crash.
            with pytest.raises(MeasurePageStructureError):
                await repository.fetch_corpus()


async def test_a_failed_first_navigation_does_not_leave_a_browser_behind() -> None:
    """`start()` abre el navegador antes de navegar, así que si la navegación
    falla es suyo el trabajo de cerrarlo.

    El caso no es raro: es exactamente lo que sirve el WAF del sitio real a
    cualquier cosa que no sea un Chrome headed —una interstitial sin el iframe—
    y también lo que dejaría un rediseño. Sin el `except BaseException` que
    cierra y relanza, cada intento fallido se deja un Chrome vivo y su perfil
    temporal en el disco, y quien reintenta los va acumulando.

    Se mira el disco y no los campos internos, por lo mismo que
    ``test_open_cleans_up_after_a_failed_launch``. Y se mira **dentro** del
    bloque, antes de que ningún cierre de cortesía tape la fuga: aquí no hay
    ninguno, porque la sesión nunca llegó a quedar arrancada.
    """
    before = leftover_profiles()
    with local_site({OUTER_PAGE_FILENAME: "<p>no iframe here</p>"}) as base_url:
        session = LiveSession(
            url=f"{base_url}/{OUTER_PAGE_FILENAME}", timeout_ms=2000, headless=True, channel=None
        )

        with pytest.raises(MeasurePageStructureError):
            await session.start()

        leaked = leftover_profiles() - before
        check(not leaked, f"la navegación fallida se dejó el navegador: {sorted(leaked)}")


async def test_a_close_that_throws_still_leaves_the_session_restartable() -> None:
    """El reset de estado va en un ``finally``, y sin él la sesión miente.

    ``close()`` puede reventar: el navegador ya muerto por su cuenta es el caso
    del que esta clase dice recuperarse, y ``PersistentBrowserContext.close``
    propaga ese fallo a propósito. Si el reset no fuese incondicional, la sesión
    se quedaría con ``_started`` en cierto y una página muerta, así que el
    siguiente ``start()`` se creería arrancada y devolvería esa página — la
    sesión diría estar viva con el navegador cerrado, que es exactamente lo que
    ``_is_dead()`` existe para evitar.

    Nada lo comprobaba: quitando el ``finally`` la suite entera seguía en verde.
    Se afirma por el comportamiento —que se pueda volver a usar— y no por los
    campos.
    """
    with local_site(site_files(MEASURE_TABLE_HTML, MINIMAL_REQUISITOS_JS, MINIMAL_ENS_NORM_JS)) as (
        base_url
    ):
        async with local_session(base_url) as session:
            await session.start()
            # Parar el driver por debajo hace que el cierre falle de verdad, sin
            # doblar nada: es el mismo mecanismo que usa test_persistent_context.
            await require(session._browser._playwright, "start() no dejó el driver").stop()

            with pytest.raises(PlaywrightError):
                await session.close()

            measures = (await NavegableRepository(session).fetch_corpus())[1]
            check(len(measures) == 1, f"la sesión no se pudo reusar: {len(measures)} medidas")
