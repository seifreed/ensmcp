"""Long-lived headed-Chrome session over the ENS Navegable content iframe.

``LiveSession`` keeps one headed real Chrome warm for the whole MCP server
lifetime: the page holding ``#tablaResumen`` is loaded once and then reused, so
repeated tool calls no longer relaunch the browser (no flicker, no per-call WAF
handshake). A ``refresh`` reloads the live page in place.

The WAF of ENS Navegable serves a "Bienvenidos" interstitial to any headless
browser instead of the real page, so production launches a **headed** real
Chrome (``channel="chrome"``) via a persistent context — the same approach
``scripts/capture_dom.py`` uses. The session starts lazily, inside whichever
event loop first asks for a frame, so the Patchright instance is bound to the
server's own loop rather than to import time.
"""

from __future__ import annotations

import asyncio
from typing import cast

from patchright.async_api import Frame, Page
from patchright.async_api import TimeoutError as PlaywrightTimeoutError

from ensmcp.scraping.errors import MeasurePageStructureError
from ensmcp.scraping.persistent_context import DEFAULT_TIMEOUT_MS, PersistentBrowserContext
from ensmcp.scraping.selectors import (
    CONTENT_IFRAME_SELECTOR,
    CONTENT_IFRAME_URL_FRAGMENT,
    ENS_NAVEGABLE_URL,
    SUMMARY_TABLE_SELECTOR,
)

# The site's WAF serves a "Bienvenidos" interstitial to any headless browser
# instead of the real page that holds #tablaResumen. Only a headed real Chrome
# (channel="chrome") gets through, so production launches headed. Requires
# Google Chrome installed and a display (xvfb on headless servers).
STEALTH_CHANNEL = "chrome"
STEALTH_HEADLESS = False

# After page.reload(), the outer <iframe> element is visible before its child
# frame has finished navigating to its own src: page.frames briefly reports it
# with an empty url. Polling at this interval until timeout_ms elapses gives
# that navigation time to land instead of failing on the first, too-early look.
FRAME_POLL_INTERVAL_S = 0.1

# In-page fetch expression: pull requisitos.js through the page's own fetch so
# the same cookies/session that already passed the WAF are reused (an external
# HTTP client would be blocked). Returns the raw JS source text.
#
# The abort signal is load-bearing, not decorative: page.evaluate() takes no
# timeout of its own, so a bare fetch() here was the one browser call in this
# class not bounded by timeout_ms. An origin that accepts the connection and
# then never answers would block it forever — and NavegableRepository holds
# its lock across this call, so that single stall wedged every later tool call
# too, permanently, until the process was killed.
# An error page is not an asset, and ``fetch`` will not say so on its own: it
# resolves happily on a 404 or a 500 and hands back the error body. That body
# then went to the parsers as if it were the file. ``requisitos.js`` survived it
# by luck — its parser checks the literal's shape and refused — but the norm
# asset's has no such shape to check, so a 404 there produced 73 measures whose
# wording was silently empty, and a corpus that passed every count-based guard:
# ``build_snapshot`` would have written it, and a startup check would have
# adopted it over data that was correct.
_FETCH_JS_EXPRESSION = """
async ([path, timeoutMs]) => {
    const response = await fetch(path, { signal: AbortSignal.timeout(timeoutMs) });
    if (!response.ok) {
        throw new Error(`HTTP ${response.status} ${response.statusText} for ${path}`);
    }
    return await response.text();
}
"""


class LiveSession:
    """A warm headed-Chrome session over the ENS Navegable content iframe.

    Lazily started on the first ``frame()``/``fetch_asset()`` call and
    kept alive until ``close()``. The content frame is resolved once and cached
    until the next ``refresh()`` so repeated scrapes reuse the exact same page.
    If the browser dies externally (crash, OOM, someone closing the window)
    between calls, the next ``start()`` (called internally by every public
    method) notices the dead page and relaunches instead of handing back a
    page that would fail on first use — the server recovers on its own
    instead of staying wedged until the process is restarted.
    """

    def __init__(
        self,
        url: str = ENS_NAVEGABLE_URL,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        headless: bool = STEALTH_HEADLESS,
        channel: str | None = STEALTH_CHANNEL,
    ) -> None:
        self._url = url
        self._timeout_ms = timeout_ms
        self._browser = PersistentBrowserContext(headless=headless, channel=channel)
        self._started = False
        self._start_lock = asyncio.Lock()
        self._page: Page | None = None
        self._frame: Frame | None = None

    async def start(self) -> None:
        """Launch the browser and load the page once. Idempotent, self-healing.

        The MCP server can dispatch several tool calls concurrently against
        this one shared session (nothing in the protocol serialises them),
        so two coroutines can both see ``_started`` as ``False`` before
        either sets it — the lock, not the flag alone, is what actually
        prevents launching two browsers onto the same ``_browser``. The same
        lock also guards the recovery path below: if the browser died since
        the last successful start, this relaunches it instead of handing
        back a page that would fail on first use.
        """
        if self._started and not self._is_dead():
            return
        async with self._start_lock:
            if self._started and not self._is_dead():
                return
            if self._started:
                # Recovering from a dead browser: drop the stale handles so
                # this proceeds exactly like a fresh start below.
                await self._browser.close()
                self._started = False
                self._page = None
                self._frame = None
            page = await self._browser.open()
            try:
                await self._navigate_and_wait_iframe(page, reload=False)
            except BaseException:
                await self._browser.close()
                raise
            self._page = page
            self._started = True

    def _is_dead(self) -> bool:
        return self._page is not None and self._page.is_closed()

    async def frame(self) -> Frame:
        """Return the content iframe with #tablaResumen visible, cached per session."""
        await self.start()
        if self._frame is None:
            self._frame = await self._resolve_content_frame(self._started_page())
        return self._frame

    async def refresh(self) -> None:
        """Reload the live page in place and re-resolve the content frame."""
        await self.start()
        page = self._started_page()
        # Drop the cached frame *before* reloading. page.reload() detaches the
        # old content frame immediately; if the reload or the re-resolve below
        # then raises (timeout, WAF interstitial, the table never reappearing),
        # a stale detached frame would otherwise stay cached, and every later
        # frame() call would hand back that same corpse instead of looking at
        # the page's current state.
        #
        # Lo que **no** hace es curar la sesión sola, y eso decía antes esta
        # nota. Medido: tras una recarga que falla a nivel de red, el frame
        # viejo sigue listado en ``page.frames`` con su url de siempre y con
        # ``is_detached()`` todavía en falso —el desmontaje llega después—, así
        # que ``_resolve_content_frame`` vuelve a dar con él y el error sale
        # igual. Filtrar por ``is_detached()`` ahí no arregla nada: se comprobó,
        # y es una carrera, no un estado. Quien cura la sesión es el
        # ``refresh()`` siguiente, que sí vuelve a navegar; hasta entonces
        # ``RefreshingRepository`` sirve de memoria, que es justo para lo que
        # está.
        self._frame = None
        await self._navigate_and_wait_iframe(page, reload=True)
        self._frame = await self._resolve_content_frame(page)

    async def fetch_asset(self, path: str) -> str:
        """Fetch a same-origin build asset through the live page's own fetch.

        Takes the path rather than hardcoding one: the measures' descriptions
        and the refuerzo texts live in two different assets fetched the exact
        same way, through the session that already got past the WAF.
        """
        await self.start()
        page = self._started_page()
        return cast(
            "str",
            await page.evaluate(_FETCH_JS_EXPRESSION, [path, self._timeout_ms]),
        )

    async def close(self) -> None:
        """Tear the browser down. Safe to call more than once.

        Takes ``_start_lock`` for the same reason ``start()`` does: nothing
        serialises tool calls, so a ``close()`` racing a concurrent
        ``start()``/``refresh()`` could otherwise interleave with the
        launch/recovery section below and hand a caller back a session that
        looks started right after it was supposedly torn down. The state reset
        is in a ``finally`` so a failure inside ``self._browser.close()``
        (the browser already dead — same case ``_is_dead()`` recovers from)
        still clears ``_started``, instead of leaving this session believing
        it is still up when its browser is gone.
        """
        async with self._start_lock:
            try:
                await self._browser.close()
            finally:
                self._page = None
                self._frame = None
                self._started = False

    def _started_page(self) -> Page:
        # ponytail: every caller runs `await self.start()` right before this,
        # which always sets self._page — an internal invariant, not a boundary
        # input, so cast instead of adding an unreachable-in-practice guard.
        return cast("Page", self._page)

    async def _navigate_and_wait_iframe(self, page: Page, *, reload: bool) -> None:
        try:
            if reload:
                await page.reload(wait_until="domcontentloaded", timeout=self._timeout_ms)
            else:
                await page.goto(self._url, wait_until="domcontentloaded", timeout=self._timeout_ms)
            await page.wait_for_selector(
                CONTENT_IFRAME_SELECTOR, state="visible", timeout=self._timeout_ms
            )
        except PlaywrightTimeoutError as exc:
            raise MeasurePageStructureError(
                self._url, CONTENT_IFRAME_SELECTOR, self._timeout_ms
            ) from exc

    async def _resolve_content_frame(self, page: Page) -> Frame:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout_ms / 1000
        while True:
            for frame in page.frames:
                if CONTENT_IFRAME_URL_FRAGMENT in frame.url:
                    await self._wait_for_summary_table(frame)
                    return frame
            if loop.time() >= deadline:
                raise MeasurePageStructureError(
                    self._url, CONTENT_IFRAME_URL_FRAGMENT, self._timeout_ms
                )
            await asyncio.sleep(FRAME_POLL_INTERVAL_S)

    async def _wait_for_summary_table(self, frame: Frame) -> None:
        try:
            await frame.wait_for_selector(
                SUMMARY_TABLE_SELECTOR, state="visible", timeout=self._timeout_ms
            )
        except PlaywrightTimeoutError as exc:
            raise MeasurePageStructureError(
                frame.url, SUMMARY_TABLE_SELECTOR, self._timeout_ms
            ) from exc
