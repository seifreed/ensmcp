"""One-off helper: dump everything ENS Navegable serves, for offline study.

Run this from a network where the site is actually reachable (this sandbox's
egress is blocked by the site's WAF). It saves what the live page renders and
loads, so real selectors and real parsers can be written against a captured
artifact instead of guessed ones — that is where
``src/ensmcp/scraping/selectors.py`` and
``tests/scraping/fixtures/requisitos.js`` came from.

The open question it exists to answer right now: **where does the text that
defines each refuerzo live?** The measures' applicability already comes from
``#tablaResumen`` (the "+ R1" notes in the Bajo/Medio/Alto cells), but the
wording of each refuerzo is nowhere in the summary table nor in
``requisitos.js`` (that asset is the CCN-STIC 808 questionnaire). So this
capture covers all three places it could be:

1. ``responses.txt`` — every URL the page fetched. A sibling build asset next
   to ``requisitos.js`` would be the cheap answer: one fetch, cached per
   session, exactly like ``scraping/requisitos.py`` does today.
2. ``content-frame.html`` — the content iframe's own DOM, not just the outer
   page's, in case the text ships inline with the table.
3. ``measure-detail.html`` — the DOM after clicking a measure known to carry a
   refuerzo. The expensive answer: if the text only exists here, it costs one
   navigation per measure and cannot sit on the path of every tool call.

What every build asset the site loads holds — the whole list, checked one by
one, so nobody re-investigates them:

- ``requisitos.js`` — the CCN-STIC 808 audit questionnaire. Parsed for each
  measure's ``description``. No refuerzos.
- ``norms/ens.js`` — the RD 311/2022 text. Parsed twice: for each measure's own
  ``norm_text`` (what the RD demands, as opposed to what the questionnaire asks)
  and for each refuerzo's wording. Its ``[medida.k]`` and ``[medida.rN.k]``
  markers are what tell the two apart.
- ``decaplica.js`` and ``control/menucontrol.js`` — presentation HTML for the
  16 category buttons, twice. The only ENS codes in them are the buttons' own
  ``id`` attributes. No data.
- ``control/control.js`` — the dashboard's UI logic (``cargarControlInterno``,
  ``abrirModalCatalogoPaneles``, ``cargarDatosPanel``). Not one ENS code.
- ``control/medidasControl.js`` — the 73 measures as structured data in a
  global. Loading it (``(0,eval)(src)`` in the frame, since the content page
  does not pull it in) hands back 73 objects with ``cod``/``nombre``/``apl``/
  ``dimensiones``/``refuerzos``, already de-mangled. Tempting, and **wrong**.

  It was cross-checked field by field against ``#tablaResumen`` and against the
  RD text. Codes and titles agree 73/73; everything else disagrees in seven
  measures, and in all seven the summary table is the one that is right:

  * ``op.ext.1`` — claims it applies at BÁSICA. The RD says
    "Categoría BÁSICA: no aplica", and the table's cell is "n.a.".
  * ``mp.info.6`` — claims all five dimensions. The table's column reads "D",
    and the measure is "Copias de seguridad".
  * ``mp.per.3``, ``mp.per.4``, ``mp.s.1`` — claim an R1 at MEDIA for measures
    whose three cells are a plain "aplica", with no reinforcement anywhere.
  * ``op.exp.5`` — claims R1 at MEDIA. Its **own** ``apl`` prose, quoting the
    RD, says "Categoría MEDIA: op.exp.5. Categoría ALTA: op.exp.5 + R1": the
    asset contradicts itself, so its ``ref`` arrays are not merely lossy.
  * ``op.nub.1`` — claims R2 at MEDIA where the cell reads "+ R1".

  On top of that it loses the choice form: where the table says
  "+ [R2 o R3 o R4] + R5" this asset says ``["R2","R5"]``, keeping the first
  option and dropping the rest. That was the original reason to refuse it; the
  seven above are the stronger one.
- ``norms/{eidas,nis,regnis,lopd,leypic,leypicley}.js`` — the plain text of
  those other norms, article by article, and **nothing else**: not one Anexo II
  code (org.*/op.*/mp.*) appears in any of them, so the site draws no
  correspondence between the ENS and NIS2, LOPD or eIDAS. There is no normative
  mapping here to harvest.
- ``ar/*`` — the MAGERIT risk-analysis module, a separate feature:
  ``catActivos``/``catAmenazas``/``catSalvaguardas`` are its catalogues (the
  last one being the measures again, plus which threats each mitigates), and
  ``Plantilla_sistema_*`` are worked example analyses for a fictitious
  municipality. Despite the "PCE" in two filenames, none of them define a
  perfil de cumplimiento: "perfil", "883" and "884" appear zero times.
- ``js/*`` — third-party libraries (d3, billboard, xlsx, FileSaver,
  tableexport). Nothing of the ENS in them.
- ``tabla.js`` — the summary table pre-rendered as HTML, which is the same data
  already scraped from the live DOM, and ``script.js`` — the content frame's own
  bootstrap. Neither adds anything.

Everything the site serves is therefore either consumed above or accounted for
here; there is no unexploited source left in ENS Navegable.

Uses the same ``PersistentBrowserContext`` as the main scraper
(``src/ensmcp/scraping/live_session.py``) so the site's WAF does not detect
the browser as automation. The WAF blocks headless browsers, so this
launches a headed real Chrome (``channel="chrome"``), which requires Google
Chrome installed and a display.

Usage:
    python scripts/capture_dom.py [output_dir]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from patchright.async_api import Frame, Page
from patchright.async_api import TimeoutError as PlaywrightTimeoutError

from ensmcp.scraping.errors import MeasurePageStructureError
from ensmcp.scraping.live_session import STEALTH_CHANNEL, STEALTH_HEADLESS
from ensmcp.scraping.persistent_context import PersistentBrowserContext
from ensmcp.scraping.selectors import (
    CONTENT_IFRAME_URL_FRAGMENT,
    ENS_NAVEGABLE_URL,
    SUMMARY_TABLE_SELECTOR,
    TABLE_ROW_SELECTOR,
)

DEFAULT_OUTPUT_DIR = Path("captura_ens_navegable")

# A measure whose Alto cell carries a reinforcement note in the real table, so
# whatever the site shows about refuerzos has to be reachable from its row.
REINFORCED_MEASURE_CODE = "mp.s.4"

NAVIGATION_TIMEOUT_MS = 45000
# The table is injected by the frame's own script after load, and the detail
# view is a client-side render: both need a settle window that no selector
# reliably signals.
SETTLE_MS = 3000


async def _content_frame(page: Page, timeout_ms: int = NAVIGATION_TIMEOUT_MS) -> Frame:
    """Return the content iframe once its summary table is visible.

    ``timeout_ms`` is a parameter for the same reason ``LiveSession`` takes one:
    a fixture site answers instantly and a test of the *timeout* should not sit
    out the 45 s a live capture is willing to wait.

    El fallo tiene que decir qué faltó, como hace
    ``LiveSession._resolve_content_frame`` en producción. Con un ``next()`` sin
    defecto, una página sin el iframe —una interstitial del WAF, un rediseño—
    levantaba un ``StopIteration`` dentro de una corrutina, que Python convierte
    en ``RuntimeError: coroutine raised StopIteration``: un mensaje que no
    menciona ni el frame ni la página, en la herramienta cuyo trabajo es
    precisamente diagnosticar qué sirve el sitio.
    """
    frame = next((f for f in page.frames if CONTENT_IFRAME_URL_FRAGMENT in f.url), None)
    if frame is None:
        raise MeasurePageStructureError(page.url, CONTENT_IFRAME_URL_FRAGMENT, timeout_ms)
    # La otra mitad de la misma promesa, y estaba sin hacer: con el frame ya
    # resuelto pero sin ``#tablaResumen`` —un rediseño, la tabla que su script
    # nunca llega a inyectar— salía el ``TimeoutError`` pelado de Patchright, que
    # no nombra ni la página ni el frame. ``LiveSession._wait_for_summary_table``
    # convierte exactamente este caso en producción.
    try:
        await frame.wait_for_selector(SUMMARY_TABLE_SELECTOR, state="visible", timeout=timeout_ms)
    except PlaywrightTimeoutError as exc:
        raise MeasurePageStructureError(frame.url, SUMMARY_TABLE_SELECTOR, timeout_ms) from exc
    return frame


async def _capture_measure_detail(frame: Frame, output_dir: Path) -> None:
    """Click the reinforced measure's row and dump whatever it opens.

    Failing here must not lose the rest of the capture: the row may not be
    clickable at all, which is itself part of the answer (the detail view is
    not reachable that way), and the response log and frame dump already
    written are the more likely place the refuerzo text turns up.
    """
    row = frame.locator(TABLE_ROW_SELECTOR).filter(has_text=REINFORCED_MEASURE_CODE).first
    try:
        await row.click(timeout=NAVIGATION_TIMEOUT_MS)
    except Exception as exc:
        (output_dir / "measure-detail-error.txt").write_text(repr(exc), encoding="utf-8")
        raise
    await frame.page.wait_for_timeout(SETTLE_MS)
    (output_dir / "measure-detail.html").write_text(await frame.content(), encoding="utf-8")


async def capture(
    output_dir: Path,
    url: str = ENS_NAVEGABLE_URL,
    *,
    headless: bool = STEALTH_HEADLESS,
    channel: str | None = STEALTH_CHANNEL,
) -> None:
    """Render ``url`` in a real browser and dump it to output_dir.

    The three keyword defaults are the live capture; they are parameters for the
    same reason ``_content_frame``'s ``timeout_ms`` is one. What has no other way
    of being exercised is the teardown below — the ``finally`` that must close
    the browser even when writing the URL log fails — and reaching it needs a
    site that answers and an output directory that refuses to be written to.
    Hardcoding the real URL put that path behind the WAF, a display and a
    network, so nothing tested it.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    browser = PersistentBrowserContext(headless=headless, channel=channel)
    page = await browser.open()
    urls: list[str] = []
    page.on("response", lambda response: urls.append(response.url))
    try:
        await page.goto(url, wait_until="networkidle", timeout=NAVIGATION_TIMEOUT_MS)
        await page.wait_for_timeout(SETTLE_MS)
        (output_dir / "outer.html").write_text(await page.content(), encoding="utf-8")
        frame = await _content_frame(page)
        (output_dir / "content-frame.html").write_text(await frame.content(), encoding="utf-8")
        await _capture_measure_detail(frame, output_dir)
    finally:
        # Written in the finally so a failure mid-capture still leaves the URL
        # log behind — it is the cheapest of the three answers and the one most
        # likely to already hold what this run was after.
        #
        # And wrapped in its own ``try`` so that write cannot take the teardown
        # down with it: a read-only or full output directory made ``write_text``
        # raise, ``browser.close()`` never ran, and the run leaked its Chrome and
        # its temp profile directory — verified, one orphaned ``ensmcp-*`` dir
        # left behind. ``PersistentBrowserContext.close`` guards each of its own
        # steps the same way and for the same reason.
        try:
            (output_dir / "responses.txt").write_text("\n".join(urls), encoding="utf-8")
        finally:
            await browser.close()


def main() -> None:
    """Entry point: resolve the output directory and run the capture."""
    output_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT_DIR
    asyncio.run(capture(output_dir))
    print(f"Saved capture to {output_dir}/")
    for name in sorted(p.name for p in output_dir.iterdir()):
        print(f"  {name}")


if __name__ == "__main__":
    main()
