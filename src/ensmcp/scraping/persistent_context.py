"""Patchright persistent-context lifecycle for headed Chrome.

Both ``LiveSession`` (the production warm session) and
``scripts/capture_dom.py`` (a one-off dev capture) need the same "launch a
Chrome persistent context in a fresh temp profile, hand back its one page,
tear it all down safely and idempotently" mechanics — factored out here so
neither reimplements it by hand.
"""

from __future__ import annotations

import shutil
import tempfile

from patchright.async_api import BrowserContext, Page, Playwright, async_playwright

DEFAULT_TIMEOUT_MS = 30000


class PersistentBrowserContext:
    """Owns one Patchright persistent context and its temp profile dir.

    ``open()``/``close()`` rather than an async context manager: callers
    differ on how long the page should stay alive (one call vs. the whole
    server lifetime), so the owner decides that, this only owns the launch
    and teardown mechanics.
    """

    def __init__(self, *, headless: bool, channel: str | None) -> None:
        self._headless = headless
        self._channel = channel
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._user_data_dir: str | None = None

    async def open(self) -> Page:
        """Launch the browser in a fresh temp profile and return its one page."""
        try:
            self._playwright = await async_playwright().start()
            self._user_data_dir = tempfile.mkdtemp(prefix="ensmcp-")
            self._context = await self._playwright.chromium.launch_persistent_context(
                self._user_data_dir,
                headless=self._headless,
                channel=self._channel,
                # Patchright/Playwright pass --no-sandbox unless told otherwise,
                # which both drops Chrome's own renderer sandbox — real
                # exposure here, since this browser visits live, external
                # pages — and shows a persistent "unsupported flag" infobar.
                chromium_sandbox=True,
            )
            # ponytail: persistent context always opens one page. Reading
            # .pages[0] here, still inside the try, matters: right after
            # launch_persistent_context() returns is exactly when pages could
            # in principle be empty, and an IndexError raised outside the try
            # would skip the except below and leak the browser process and
            # temp profile dir it exists to clean up.
            return self._context.pages[0]
        except BaseException:
            # A failure partway through (e.g. no "chrome" channel installed)
            # would otherwise leak whatever already got as far as being
            # assigned — the driver process, the temp profile dir — since
            # nothing else owns them yet. close() tolerates any subset of
            # these being None, so it's safe to call unconditionally here.
            await self.close()
            raise

    async def close(self) -> None:
        """Tear the browser, driver and temp profile down. Safe to call more than once.

        Each step is wrapped in its own ``try/finally``: ``context.close()`` on
        a browser that already died externally (crash, OOM, the window closed
        by hand — exactly what ``LiveSession`` is built to recover from) can
        raise, and a bare call without the ``finally`` would leave that step's
        field un-reset and skip every step after it — the driver process and
        the temp profile dir would never get torn down, and a second close()
        call would just fail the same way forever instead of finishing the job.

        Y cada paso va además dentro del ``finally`` del anterior, que era la
        otra mitad y faltaba. Reponer el campo hace que un **reintento** termine
        el trabajo, pero no que lo termine la llamada que falló: ésta seguía
        saltándose los pasos siguientes. Y nadie reintenta — ``LiveSession.close``
        llama a esto una sola vez, desde el ``finally`` de ``serve()``, así que
        un ``context.close()`` que revienta dejaba vivo el proceso del driver y
        el perfil temporal en disco. Medido: tras un solo ``close()`` con el
        driver caído, ``_playwright`` seguía puesto y el directorio
        ``ensmcp-*`` seguía ahí.

        Es exactamente el razonamiento que ``__main__.serve()`` ya escribe sobre
        su propio par de ``finally`` anidados —"una secuencia pelada aquí
        significa que un paso que lanza renuncia en silencio a lo demás"—
        aplicado al sitio que de verdad posee el navegador, el driver y el
        directorio.
        """
        try:
            if self._context is not None:
                try:
                    await self._context.close()
                finally:
                    self._context = None
        finally:
            try:
                if self._playwright is not None:
                    try:
                        await self._playwright.stop()
                    finally:
                        self._playwright = None
            finally:
                if self._user_data_dir is not None:
                    try:
                        shutil.rmtree(self._user_data_dir)
                    except FileNotFoundError:
                        self._user_data_dir = None
                    else:
                        self._user_data_dir = None
