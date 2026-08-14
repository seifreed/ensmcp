"""Repositories that serve the captured snapshot and keep it honest.

``SnapshotRepository`` answers from a JSON file with no browser at all, which is
what lets the server run on a laptop with no Chrome, no display and no network.
``RefreshingRepository`` wraps it with a live check so "offline by default"
never turns into "silently stale": it answers from the snapshot immediately and
compares against the live site in the background.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from enum import Enum

from ensmcp import package_data
from ensmcp.domain.applicability import parse_levels, parse_reinforcements
from ensmcp.domain.models import Category, SecurityMeasure
from ensmcp.domain.repository import MeasureRepository
from ensmcp.snapshot.codec import dump, load

SNAPSHOT_FILENAME = "anexo_ii.json"

# How the live source is told to re-read its origin, before a comparison.
ReloadHandler = Callable[[], Awaitable[None]]

# ``dump`` needs a date and the comparison does not compare dates, so both sides
# carry this one. It has to be a constant and not ``self._captured_at``: that
# value moves when the live page is adopted, and a baseline that moves is not a
# baseline — it is what let a second check report "coincide con el snapshot"
# about a file the first check had already replaced in memory.
_COMPARISON_DATE = ""
_MEASURE_CODE = re.compile(r"^(?:org|op|mp)(?:\.[a-z]+)*\.\d+$")
_CATEGORY_CODE = re.compile(r"^(?:org|op|mp)(?:\.[a-z]+)*$")


def _validate_corpus(categories: Sequence[Category], measures: Sequence[SecurityMeasure]) -> None:
    if not categories or not measures:
        raise ValueError("snapshot has no categories or security measures")
    category_codes = {category.code for category in categories}
    for category in categories:
        if (
            not _CATEGORY_CODE.fullmatch(category.code)
            or category.code.split(".")[0] != category.group.value
        ):
            raise ValueError(f"snapshot has an inconsistent category: {category.code!r}")
    for measure in measures:
        expected_category = measure.code.rsplit(".", 1)[0]
        if (
            not _MEASURE_CODE.fullmatch(measure.code)
            or measure.category_code != expected_category
            or measure.category_code not in category_codes
        ):
            raise ValueError(f"snapshot has an inconsistent measure code: {measure.code!r}")
        if not measure.dimensions or not measure.levels:
            raise ValueError(f"snapshot measure {measure.code!r} has no dimensions or levels")
        if measure.raw_levels:
            if parse_levels(measure.raw_levels) != measure.levels:
                raise ValueError(f"snapshot measure {measure.code!r} contradicts its raw levels")
            raw_reinforcements = {
                (item.code, item.level, item.alternative)
                for item in parse_reinforcements(measure.raw_levels)
            }
            stored_reinforcements = {
                (item.code, item.level, item.alternative) for item in measure.reinforcements
            }
            if raw_reinforcements != stored_reinforcements:
                raise ValueError(
                    f"snapshot measure {measure.code!r} contradicts its raw reinforcements"
                )
        if any(item.level not in measure.levels for item in measure.reinforcements):
            raise ValueError(
                f"snapshot measure {measure.code!r} has a reinforcement at an excluded level"
            )


class LiveCheck(Enum):
    """How the comparison against the live site went, for ``snapshot_status``."""

    PENDING = "pending"
    UNCHANGED = "unchanged"
    UPDATED = "updated"
    UNAVAILABLE = "unavailable"


def default_snapshot_text() -> str:
    """Read the snapshot shipped inside the installed package."""
    return package_data.read(SNAPSHOT_FILENAME)


class SnapshotRepository:
    """Serves a captured corpus from memory. No browser, no network.

    Parses the JSON once at construction rather than per call: the whole point
    is that a query costs a dictionary lookup. A malformed or stale-schema file
    therefore fails at startup, where it is obvious, instead of on the first
    tool call.

    The corpus is frozen into tuples on the way in. It is read-only state that
    lives for the whole process and is handed straight to ``RefreshingRepository``
    by reference, so a plain list left it mutable through a public attribute:
    ``repository.measures.clear()`` emptied the repository, and every later read
    answered with nothing. Same reason ``Guia808`` holds a tuple and not a dict.
    """

    def __init__(self, snapshot_text: str) -> None:
        categories, measures, self.captured_at = load(snapshot_text)
        _validate_corpus(categories, measures)
        try:
            captured_at = datetime.fromisoformat(self.captured_at)
        except ValueError as exc:
            raise ValueError(
                f"snapshot captured_at is not an ISO 8601 timestamp: {self.captured_at!r}"
            ) from exc
        if captured_at.tzinfo is None:
            raise ValueError(f"snapshot captured_at has no timezone: {self.captured_at!r}")
        self.categories: tuple[Category, ...] = tuple(categories)
        self.measures: tuple[SecurityMeasure, ...] = tuple(measures)

    @classmethod
    def from_package_data(cls) -> SnapshotRepository:
        """Build from the snapshot shipped with the package."""
        return cls(default_snapshot_text())

    async def fetch_corpus(self) -> tuple[list[Category], list[SecurityMeasure]]:
        return list(self.categories), list(self.measures)


class RefreshingRepository:
    """Answers from a snapshot, and checks the live site behind the request.

    The check is deliberately **not** on the path of the first tool call. Making
    it blocking would put Chrome, a display and network back on the critical
    path — exactly the dependency the snapshot exists to remove — and a client
    on a train would wait out the timeout before getting data it already had on
    disk. So the first query answers from the snapshot at once, and the
    comparison runs as a background task that swaps the data in if it differs
    and does nothing at all if the browser cannot start.

    ``refresh()`` is the synchronous door onto the same comparison, for a caller
    who wants to know *now* (the ``refresh_live_page`` tool).

    ``reload_live`` is how the live source is made to re-read its origin, and it
    arrives as a callable because ``MeasureRepository`` deliberately has none:
    the port is for *reading* a corpus, and a snapshot has nothing to reload. It
    is optional so a caller with no such source (a plain fixture) still gets the
    comparison.
    """

    def __init__(
        self,
        snapshot: SnapshotRepository,
        live: MeasureRepository,
        reload_live: ReloadHandler | None = None,
    ) -> None:
        self._live = live
        self._reload_live = reload_live
        # Tuples throughout, like the snapshot they come from: this corpus is
        # swapped wholesale by ``refresh`` and never edited in place, and being
        # unable to edit it in place is what makes that true by construction.
        self._categories: tuple[Category, ...] = snapshot.categories
        self._measures: tuple[SecurityMeasure, ...] = snapshot.measures
        self._captured_at = snapshot.captured_at
        # The file, kept as the fixed thing every later check is measured
        # against — it is the file, not what happens to be in memory, that
        # ``snapshot_status`` is reporting the freshness of.
        self._snapshot = self._serialised(snapshot.categories, snapshot.measures)
        self._snapshot_captured_at = snapshot.captured_at
        self._check = LiveCheck.PENDING
        self._detail = "aún no se ha comprobado la página en vivo"
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    def start_background_check(self) -> None:
        """Schedule the live comparison without waiting for it. Idempotent."""
        if self._task is None:
            self._task = asyncio.create_task(self._check_quietly())

    async def close(self) -> None:
        """Cancel the background check if it is still running.

        Without this, shutting the server down mid-check leaves a task awaiting
        a browser that the ``finally`` in ``__main__`` is closing underneath it.
        """
        if self._task is not None:
            self._task.cancel()
            # _check_quietly already swallows its own failures, so this await
            # only collects the cancellation.
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def status_payload(self) -> dict[str, str | int]:
        """What ``snapshot_status`` reports: where the data came from, and when.

        Built here rather than in the MCP layer because this class owns the
        ``LiveCheck`` vocabulary; the server just forwards whatever it returns,
        so nothing above the repository has to know about snapshots at all.

        ``captured_at`` describes **what is being served**, which is not always
        the file: once the live check has swapped its data in, the file's date
        belongs to a corpus this repository has already discarded. Reporting it
        anyway put two different corpora in one payload — ``measures`` counting
        the live rows next to the snapshot's timestamp — in the one tool whose
        whole job is to say how fresh the answer is.
        """
        return {
            "captured_at": self._captured_at,
            "measures": len(self._measures),
            "live_check": self._check.value,
            "detail": self._detail,
        }

    async def fetch_corpus(self) -> tuple[list[Category], list[SecurityMeasure]]:
        return list(self._categories), list(self._measures)

    async def refresh(self) -> None:
        """Re-read the live site now, and adopt it if it differs.

        One read, via ``fetch_corpus``. Separate reads could produce a corpus
        half from one page state and half from the next; the port no longer
        permits that composition.

        The startup check reloads too, though its page was loaded moments
        earlier. That is one extra navigation in a background task already off
        the critical path — the price of one ordering for every caller instead
        of an "on demand reloads, startup does not" special case to re-derive at
        each entry point, which is the reasoning that produced the bug.

        El veredicto se mide **contra el fichero**, nunca contra lo que ya hay
        en memoria. Comparar contra la memoria se contradice a sí mismo en
        cuanto una comprobación adopta la web: la siguiente encuentra la web
        igual a lo que acaba de adoptar y contesta "coincide con el snapshot"
        —y se lleva por delante el aviso de regenerar el fichero— cuando el
        fichero sigue exactamente igual de desfasado. Basta un
        ``refresh_live_page`` después del arranque para llegar ahí, en la única
        tool cuyo trabajo es decir la verdad sobre la frescura del dato.

        Y un corpus sin medidas no se adopta. ``build_snapshot.check`` ya se niega a
        *escribir* uno —"un scrape que se traga media tabla en silencio es peor
        que no tener nada"— pero esta ruta, que es la que decide qué se
        **sirve**, no tenía la contraparte, así que era estrictamente más fácil
        vaciar el servidor en marcha que estropear el fichero.

        Y un fallo se **anota** antes de propagarse. Anotarlo sólo dentro de
        ``_check_quietly`` dejaba muda la otra puerta: un ``refresh_live_page``
        que no consigue abrir el navegador le devuelve el error a quien preguntó
        —correcto— pero ``snapshot_status`` seguía contestando ``pending``, que
        su propio docstring define como "aún sin comprobar". Se acababa de
        comprobar, y había fallado. Es la misma mentira que el párrafo de arriba
        por la otra puerta: lo que se reporta tiene que ser el último intento, no
        el último que salió bien.
        """
        async with self._lock:
            try:
                await self._compare()
            except Exception as exc:
                # ``Exception`` y no ``BaseException``: una cancelación no es un
                # veredicto sobre la web, y ``close()`` cancela esta misma tarea
                # durante el apagado.
                self._check = LiveCheck.UNAVAILABLE
                self._detail = f"no se pudo comprobar la página en vivo: {exc}"
                raise

    async def _compare(self) -> None:
        """Re-read the live site and adopt it if it differs. Assumes the lock."""
        if self._reload_live is not None:
            await self._reload_live()
        categories, measures = await self._live.fetch_corpus()
        self._reject_a_measureless_corpus(measures)
        self._categories = tuple(categories)
        self._measures = tuple(measures)
        if self._serialised(categories, measures) == self._snapshot:
            # Lo que se sirve es, byte a byte, el fichero: su fecha es la del
            # fichero. También cuando se vuelve a él tras haber adoptado una web
            # que luego revirtió.
            self._captured_at = self._snapshot_captured_at
            self._check = LiveCheck.UNCHANGED
            self._detail = "la página en vivo coincide con el snapshot"
            return
        # Lo que se sirve a partir de ahora se acaba de capturar, así que su
        # fecha es esta.
        self._captured_at = datetime.now(UTC).isoformat(timespec="seconds")
        self._check = LiveCheck.UPDATED
        self._detail = (
            f"la página en vivo difiere del snapshot: {len(measures)} medidas servidas "
            "desde memoria; regenera el fichero con scripts/build_snapshot.py"
        )

    @staticmethod
    def _reject_a_measureless_corpus(measures: Sequence[SecurityMeasure]) -> None:
        """Refuse to replace what is being served with nothing.

        The rule ``scripts/build_snapshot.check`` applies before writing the
        file, brought to the path that decides what is *served* — which had none,
        so emptying a running server was strictly easier than corrupting the
        file.

        It is the documented race, not a hypothesis. ``LiveSession`` waits for
        ``#tablaResumen`` to be **visible**, and the real table is visible as
        soon as its header renders; the rows arrive afterwards, injected by the
        frame's own ``cargarTablaAplicabilidad()``. A check landing in that
        window read a table of zero rows — reproduced against a fixture with a
        header and an empty ``tbody`` — and the corpus went straight into
        memory: every tool then answered from 0 measures while
        ``snapshot_status`` said ``updated``, i.e. "the live page differs and I
        am serving it". A server with perfectly good data on disk, serving
        nothing, and reporting success.

        The measures alone, not the pair ``build_snapshot`` checks: it is the
        measures whose absence empties every tool, and a table can legitimately
        carry measure rows with no category header above them — rows are
        recognised by their own class, never by what precedes them, and the
        fixtures rely on exactly that.

        Raising is what keeps the snapshot: the background check downgrades to
        ``UNAVAILABLE`` and goes on serving it, and ``refresh_live_page`` hands
        the error to the caller who asked — the split this class already makes
        for a browser that will not start.
        """
        if measures:
            return
        raise ValueError(
            "la página en vivo no sirvió ninguna medida: se sigue sirviendo el snapshot "
            "(la tabla es visible en cuanto se pinta su cabecera, y sus filas llegan después)"
        )

    async def _check_quietly(self) -> None:
        """``refresh()``, but a failure only downgrades the reported status.

        Every reason this can fail — no Chrome installed, no display, the WAF
        serving its interstitial, no network — is a reason the user chose a
        snapshot in the first place. Letting it raise would take down a server
        that has perfectly good data already loaded.

        El estado lo anota ya ``refresh()``, para las dos puertas y no sólo para
        ésta, así que aquí no queda más que tragarse el error. ``suppress`` no
        alcanza a ``CancelledError``, que no hereda de ``Exception``: ``close()``
        cancela esta tarea durante el apagado y esa cancelación tiene que
        propagarse, no confundirse con "la web no responde".
        """
        with contextlib.suppress(Exception):
            await self.refresh()

    def _serialised(
        self, categories: Sequence[Category], measures: Sequence[SecurityMeasure]
    ) -> str:
        """The canonical text of a corpus, which is what the comparison uses.

        Comparing the objects directly would report a difference for a mere
        reordering of the scraped rows; comparing the canonical text answers the
        only question that matters — would regenerating the file change it?
        """
        return dump(categories, measures, _COMPARISON_DATE)
