"""Live MeasureRepository backed by a warm Patchright session over ENS Navegable.

A ``LiveSession`` owns the headed Chrome and the content iframe for the whole
server lifetime; this repository asks it for the cached frame, scrapes the
``#tablaResumen`` rows, and merges in the texts the table itself does not
carry: each measure's audit questionnaire (from ``requisitos.js``) and, from
``norms/ens.js``, both the RD's wording for the measure and for each of its
refuerzos. Each asset is fetched once and cached per session, and every cache
is cleared on ``refresh``.
"""

from __future__ import annotations

import asyncio
from typing import TypedDict, cast

from ensmcp.domain.models import Category, SecurityMeasure
from ensmcp.scraping.live_session import LiveSession
from ensmcp.scraping.norm_texts import ParsedNormTexts, parse_norm_texts
from ensmcp.scraping.parsers import parse_category, parse_measure
from ensmcp.scraping.requisitos import ParsedRequisitos, parse_requisitos
from ensmcp.scraping.selectors import (
    ENS_NORM_JS_PATH,
    GROUP_HEADER_ROW_CLASS,
    MEASURE_ROW_CLASS,
    REQUISITOS_JS_PATH,
    SUBCATEGORY_HEADER_ROW_CLASS,
    TABLE_ROW_SELECTOR,
)

_CATEGORY_ROW_CLASSES = frozenset({GROUP_HEADER_ROW_CLASS, SUBCATEGORY_HEADER_ROW_CLASS})


class _RawRow(TypedDict):
    """One ``#tablaResumen`` row as returned by ``_EXTRACT_ROWS_JS``."""

    row_class: str
    texts: list[str]


# Read the whole table in a single round trip. The previous version held an
# ElementHandle per row and awaited inner_text() on every <td>, which is one
# CDP round trip per cell: ~3ms each, so 438 of them (73 rows x 6 cells) cost
# ~2.15s for the real table — paid again on *every* tool call, since each
# query re-scrapes by design. Worse, it grew linearly with the row count while
# the repository lock was held, so a hostile or compromised origin serving an
# oversized table could stall every concurrent tool call for minutes (measured:
# ~59s at 3000 rows, extrapolating to ~49min at 100k).
#
# Reading it atomically also removes the detached-element window the lock was
# there to narrow: no handle is held across an await any more, so a reload
# mid-scrape can no longer invalidate a row being read.
#
# ``innerText`` (not ``textContent``) matches what ElementHandle.inner_text()
# returned, so the text handed to the parsers is unchanged; stripping stays in
# Python so it keeps Python's exact whitespace semantics.
_EXTRACT_ROWS_JS = """
(selector) => Array.from(document.querySelectorAll(selector), (row) => {
    const cells = Array.from(row.querySelectorAll("td"));
    return {
        row_class: cells.length ? (cells[0].getAttribute("class") ?? "") : "",
        texts: cells.map((cell) => cell.innerText),
    };
})
"""


class NavegableRepository:
    """Scrapes categories and measures from the live ENS Navegable page.

    Reuses the warm frame of an injected ``LiveSession`` instead of opening a
    fresh browser per call, so repeated MCP queries no longer relaunch Chrome.
    Each measure's free-text description is merged in by code from
    ``requisitos.js`` (a 1:1 match with the 73 rows of ``#tablaResumen``).

    A single ``asyncio.Lock`` serialises ``_scrape()`` against ``refresh()``:
    the MCP server dispatches tool calls concurrently against this one shared
    repository, and a ``refresh_live_page`` reloads the page and clears the
    cached description map. The lock is still what stops an in-flight
    description fetch from overwriting that invalidation with stale text —
    silent data corruption. It no longer has to guard against detached
    elements: ``_scrape`` reads the whole table in one ``evaluate`` and holds
    no handle across an await (see ``_EXTRACT_ROWS_JS``).
    """

    def __init__(self, session: LiveSession) -> None:
        self._session = session
        # Una caché por asset y no una por lectura, porque las dos lecturas de un
        # asset se hacen juntas o no se hacen: salen de la misma respuesta y se
        # invalidan a la vez. Con cuatro campos sueltos eso era una convención, y
        # los ``if a is None or b is None`` que la sostenían tenían una mitad que
        # no podía decidir nunca — condiciones muertas. Guardando el par, "las dos
        # o ninguna" lo garantiza el tipo, y de paso desaparecen los cuatro
        # ``cast`` que hacían falta para convencer a mypy de lo mismo.
        self._requisitos: ParsedRequisitos | None = None
        self._norms: ParsedNormTexts | None = None
        self._lock = asyncio.Lock()

    async def fetch_corpus(self) -> tuple[list[Category], list[SecurityMeasure]]:
        """Return both halves from one scrape."""
        return await self._scrape()

    async def refresh(self) -> None:
        """Reload the live page and drop every cached text map."""
        async with self._lock:
            # Invalidate the description cache *before* reloading, not after.
            # session.refresh() may raise (timeout, WAF, table never reappears);
            # resetting afterwards would leave the stale pre-refresh descriptions
            # cached, so the next scrape would pair freshly re-scraped measures
            # with descriptions from the old requisitos.js — a silent mismatch if
            # the asset changed. Dropping first means a failed refresh forces a
            # re-fetch on the next scrape, same as the frame cache in LiveSession.
            self._requisitos = None
            self._norms = None
            await self._session.refresh()

    async def _scrape(self) -> tuple[list[Category], list[SecurityMeasure]]:
        async with self._lock:
            frame = await self._session.frame()
            rows = cast("list[_RawRow]", await frame.evaluate(_EXTRACT_ROWS_JS, TABLE_ROW_SELECTOR))
            categories: list[Category] = []
            measures: list[SecurityMeasure] = []
            category_codes: set[str] = set()
            measure_codes: set[str] = set()
            for row in rows:
                parsed = await self._parse_row(row)
                if isinstance(parsed, Category):
                    if parsed.code in category_codes:
                        raise ValueError(f"duplicate category code: {parsed.code}")
                    category_codes.add(parsed.code)
                    categories.append(parsed)
                elif isinstance(parsed, SecurityMeasure):
                    if parsed.code in measure_codes:
                        raise ValueError(f"duplicate measure code: {parsed.code}")
                    measure_codes.add(parsed.code)
                    measures.append(parsed)
            return categories, measures

    async def _load_requisitos(self) -> ParsedRequisitos:
        """Fetch requisitos.js once and keep both readings of it.

        One request, one parse pass: the descriptions and the audit
        requirements come out of the same asset, so fetching it twice would
        double the cost for nothing — y por eso se guardan como un par, que es
        lo que hace imposible tener una sin la otra.
        """
        if self._requisitos is None:
            source = await self._session.fetch_asset(REQUISITOS_JS_PATH)
            self._requisitos = parse_requisitos(source)
        return self._requisitos

    async def _load_norms(self) -> ParsedNormTexts:
        """Fetch norms/ens.js once and keep both readings of it.

        The same one-request-one-parse shape as ``_load_requisitos``: the
        measure's wording in the RD and each refuerzo's come out together.
        """
        if self._norms is None:
            source = await self._session.fetch_asset(ENS_NORM_JS_PATH)
            parsed = parse_norm_texts(source)
            # An asset that yields not one measure is not the norm asset, and
            # accepting it means publishing every measure with its wording
            # blank — silently, because nothing downstream counts words. The
            # HTTP status is checked at the fetch, so what is left here is a
            # 200 carrying the wrong body: an interstitial from the WAF that
            # guards this origin is exactly that shape. ``requisitos.py`` holds
            # its own asset to the same contract, by checking the literal's
            # shape; this is that contract for a file with no shape to check.
            if not parsed.measure_texts:
                raise ValueError(
                    f"{ENS_NORM_JS_PATH} carries no measure wording at all "
                    f"({len(source)} characters): it is not the RD norm asset"
                )
            self._norms = parsed
        return self._norms

    async def _parse_row(self, row: _RawRow) -> Category | SecurityMeasure | None:
        texts = [text.strip() for text in row["texts"]]
        if not texts:
            return None
        row_class = row["row_class"]

        if row_class in _CATEGORY_ROW_CLASSES:
            # A recognized class makes this a data row, not a spacer: too few
            # cells is a broken known shape, which must surface as a clear
            # error instead of an opaque IndexError on texts[1] or being
            # silently skipped (which would lose the category without a signal).
            if len(texts) < 2:
                raise ValueError(
                    f"category header row has {len(texts)} cell(s), "
                    "expected at least 2 (code, name)"
                )
            return parse_category(texts[0], texts[1])
        if row_class == MEASURE_ROW_CLASS:
            # Same contract as the category case: a cuerpo_tabla_izq row with
            # fewer than six cells is a broken measure row (the IndexError on
            # texts[2] would otherwise crash the whole scrape), not an unknown
            # row to skip. Fetched lazily, here rather than upfront in
            # _scrape(): Category carries no description, so requisitos.js is
            # only ever needed once a measure row actually shows up.
            if len(texts) < 6:
                raise ValueError(
                    f"measure row has {len(texts)} cell(s), expected at least 6 "
                    "(code, title, dimension, bajo, medio, alto)"
                )
            requisitos = await self._load_requisitos()
            norms = await self._load_norms()
            descriptions = requisitos.descriptions
            requirements = requisitos.audit_requirements
            norm_texts = norms.measure_texts
            reinforcement_texts = norms.reinforcement_texts
            # Every row of the table has its wording in the norm asset — all 73,
            # and a network test says so. Defaulting a missing one to "" instead
            # is what let a *partial* asset through: refusing only an empty parse
            # catches a 404 or an interstitial, but a body truncated mid-transfer
            # still yields plenty of blocks, and the measures past the cut were
            # published with their wording blank and no complaint (measured: half
            # the asset, 32 of 73 silently empty). Refusing here keeps the
            # snapshot being served rather than replacing it with less.
            if not norm_texts.get(texts[0]):
                raise ValueError(
                    f"{ENS_NORM_JS_PATH} has no wording for {texts[0]}: the asset is "
                    f"incomplete (it defines {len(norm_texts)}) or the table names a "
                    "measure it does not define"
                )
            # The same contract on the questionnaire asset, which was held to a
            # weaker one: ``requisitos.js`` matches the 73 rows 1:1 (see
            # ``requisitos``' own docstring), but a missing block defaulted to
            # "" and to no requirements at all. So a measure the asset stopped
            # naming — a code spelling that drifts, which this corpus does
            # ("[mp.s. 4.r1.2]", "artáculo") — shipped with a blank description
            # *and* an empty audit scope, and nothing complained: zero questions
            # is not by itself anomalous, three real measures have none at
            # básico. ``alcance_auditoria`` would have told an auditor there is
            # nothing to ask about that measure.
            if not descriptions.get(texts[0]):
                raise ValueError(
                    f"{REQUISITOS_JS_PATH} has no questionnaire for {texts[0]}: the asset is "
                    f"incomplete (it defines {len(descriptions)}) or the table names a "
                    "measure it does not define"
                )
            measure = parse_measure(
                code=texts[0],
                title=texts[1],
                dimension_column=texts[2],
                level_cells=texts[3:6],
                description=descriptions[texts[0]],
                norm_text=norm_texts[texts[0]],
                reinforcement_texts=reinforcement_texts.get(texts[0]),
                audit_requirements=requirements.get(texts[0], ()),
            )
            # La tercera mitad del mismo contrato, y era la que faltaba. Las dos
            # guardas de arriba sólo miran la redacción **de la medida**, así que
            # un asset cortado a medias las pasaba de largo mientras los refuerzos
            # de esa misma medida salían con el texto en blanco: los dos parsers
            # leen el mismo fichero, pero el bloque de una medida y el de sus
            # refuerzos están en sitios distintos, y un corte puede caer entre
            # ellos. Medido sobre el asset real: hasta 12 medidas pasan la guarda
            # con sus refuerzos mudos, ``op.acc.5`` entre ellas — cuya celda de
            # nivel bajo es "+ [R1 o R2 o R3 o R4]", así que una Declaración de
            # Aplicabilidad diría "implanta uno de los cuatro" sin decir qué es
            # ninguno. Y en silencio: ``Reinforcement.text`` es opcional, nada
            # aguas abajo cuenta palabras, y ``search_measures`` simplemente
            # dejaría de encontrar "OTP" o "doble factor", que es justo lo que su
            # docstring dice que busca ahí.
            #
            # Se comprueba sobre la medida ya construida y no sobre el mapa: lo
            # que importa no es cuántos refuerzos define el asset, sino que tengan
            # redacción los que **esta fila** exige, que es lo que ``parse_measure``
            # acaba de resolver. Las 92 parejas (medida, refuerzo) del corpus vivo
            # la tienen, así que un vacío aquí es siempre un asset incompleto.
            mute = sorted({item.code for item in measure.reinforcements if not item.text})
            if mute:
                raise ValueError(
                    f"{ENS_NORM_JS_PATH} has no wording for {texts[0]} refuerzos "
                    f"{', '.join(mute)}: the asset is incomplete or the table names a "
                    "refuerzo it does not define"
                )
            return measure
        return None
