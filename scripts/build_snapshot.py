"""Regenerate the committed Anexo II snapshot from the live site.

A maintenance task, like ``capture_dom.py`` — not something the server does.
The server never writes to disk: the snapshot lives inside the installed
package, which may sit in a read-only directory, and refreshing it in memory is
enough to keep a running session correct. Regenerating the *file* is a decision
a person (or CI) makes and commits.

Needs what any live scrape needs: Google Chrome installed, a display (``xvfb``
on a headless server) and network. See CLAUDE.md — ``curl`` cannot do this, the
site's WAF only lets a headed browser through.

Usage:
    python scripts/build_snapshot.py [output_path]
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from atomic_write import write_atomic

from ensmcp.domain.models import Category, SecurityMeasure
from ensmcp.scraping.live_session import LiveSession
from ensmcp.scraping.navegable_repository import NavegableRepository
from ensmcp.snapshot.codec import dump

DEFAULT_OUTPUT = (
    Path(__file__).resolve().parent.parent / "src" / "ensmcp" / "data" / "anexo_ii.json"
)

SCRAPE_TIMEOUT_MS = 60000


def _previous_counts(previous: str) -> tuple[int, int]:
    """Cuántas categorías y medidas tiene el fichero que se va a sobrescribir.

    Se cuenta sobre el JSON crudo y no con ``codec.load``, que es lo que hacía
    antes. ``load`` rechaza —con razón— cualquier fichero de otra
    ``SCHEMA_VERSION``, así que la guarda se desactivaba a sí misma justo cuando
    más falta hace: **el primer scrape después de subir el esquema**, que es
    cuando se regenera todo y cuando una equivocación cuesta más. Y no se
    desactivaba en silencio, sino peor: reventaba con "snapshot schema version 2,
    expected 3" *después* de un scrape completo con Chrome, un mensaje que parece
    culpar al scrape y no dice que lo que sobra es el fichero de destino.

    Un recuento no necesita el dominio: las dos listas están en el JSON tenga la
    forma que tenga. Un fichero que no se puede ni leer como JSON cuenta como
    cero, que es lo honesto —no hay nada que encoger— y deja regenerarlo, que es
    justo lo que se querrá hacer con un fichero corrupto. Un scrape vacío lo
    siguen parando las dos comprobaciones de no-vacío, que no dependen de esto.
    """
    try:
        document = json.loads(previous)
        return len(document["categories"]), len(document["measures"])
    except (json.JSONDecodeError, KeyError, TypeError):
        return 0, 0


def check(
    categories: Sequence[Category], measures: Sequence[SecurityMeasure], previous: str | None
) -> None:
    """Refuse to overwrite the snapshot with something that looks truncated.

    Lo mismo que ``build_guia_808._check`` hace con la extracción del PDF, y por
    la misma razón que allí está escrita: un scrape que se traga media tabla en
    silencio es peor que no tener nada. Este script no lo tenía, así que
    escribía lo que saliera — y ``dump([], [])`` es un fichero perfectamente
    válido de 110 bytes que carga sin una queja y deja el servidor sirviendo
    cero medidas.

    No es hipotético: ``LiveSession`` espera a que ``#tablaResumen`` sea
    *visible*, no a que tenga filas, y las filas las inyecta el script del
    propio frame después. Una carrera ahí devuelve una tabla vacía.

    Un recuento **exacto** no vale aquí, al revés que en la guía: que el ENS
    Navegable cambie es justo lo que este script existe para recoger. Lo que se
    exige es que no encoja respecto a lo que ya hay, porque una reducción real
    del Anexo II es una decisión que merece mirarla una persona — que es lo que
    dice el docstring del módulo sobre regenerar el fichero.
    """
    problems = []
    if not measures:
        problems.append("no se scrapeó ninguna medida")
    if not categories:
        problems.append("no se scrapeó ninguna categoría")
    if previous is not None:
        old_categories, old_measures = _previous_counts(previous)
        if len(measures) < old_measures:
            problems.append(f"medidas: {old_measures} en el fichero actual, {len(measures)} ahora")
        if len(categories) < old_categories:
            problems.append(
                f"categorías: {old_categories} en el fichero actual, {len(categories)} ahora"
            )
    if problems:
        raise ValueError(
            "el scrape parece incompleto y no se sobrescribe el snapshot:\n  "
            + "\n  ".join(problems)
            + "\nSi la reducción es real, borra el fichero de destino y vuelve a lanzarlo."
        )


def _previous(output_path: Path) -> str | None:
    """El snapshot que se va a sobrescribir, si ya existe."""
    return output_path.read_text(encoding="utf-8") if output_path.is_file() else None


async def build(output_path: Path, session: LiveSession | None = None) -> tuple[int, int]:
    """Scrape the live site and write the snapshot. Returns (categories, measures).

    ``session`` es un parámetro por lo mismo que ``build_wiring`` está separado
    de ``serve()`` en ``__main__``: sin él, todo lo que **decide** esta función
    —leer el fichero que se va a sobrescribir, y pasarlo por ``check`` antes de
    escribir— sólo era alcanzable con Chrome, un display y la web real delante,
    así que ningún test lo tocaba. Y las guardas se probaban sueltas, que es
    justo lo que no basta: quitarle a esta función la llamada a ``check``, o
    dejar de mirar el fichero anterior, dejaba la suite entera en verde con las
    dos guardas intactas y desconectadas. El mismo fallo que ``build_wiring``
    existe para impedir, en el otro script que decide qué se publica.

    La sesión se cierra aquí en cualquier caso; ``LiveSession.close`` es
    idempotente, así que un llamante que la gobierne con su propio ``async
    with`` no se ve afectado.
    """
    previous = _previous(output_path)
    session = session if session is not None else LiveSession(timeout_ms=SCRAPE_TIMEOUT_MS)
    try:
        repository = NavegableRepository(session)
        # Una sola lectura: pedir las dos mitades por separado scrapeaba la tabla
        # entera dos veces para escribir un fichero que las lleva juntas.
        categories, measures = await repository.fetch_corpus()
    finally:
        await session.close()
    check(categories, measures, previous)
    captured_at = datetime.now(UTC).isoformat(timespec="seconds")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # ``newline="\n"`` explícito, por lo mismo que ``encoding`` lo es: sin él,
    # ``write_text`` traduce cada ``\n`` a ``os.linesep``, así que en Windows —uno
    # de los tres sistemas que CLAUDE.md exige— este fichero saldría entero con
    # CRLF. Y eso contradice justo lo que ``dump`` promete: que "dos volcados del
    # mismo corpus sólo difieren cuando el corpus difiere", porque los bytes
    # pasarían a depender del sistema operativo y no del Anexo II. El repositorio
    # no lleva ``.gitattributes``, así que git tampoco lo normaliza: sería un
    # diff de las 14.000 líneas sin un solo cambio real, y ningún test lo vería
    # (los dos que comparan el snapshot leen en modo texto, que deshace la
    # traducción al leer).
    write_atomic(output_path, dump(categories, measures, captured_at))
    return len(categories), len(measures)


def main() -> None:
    """Entry point: resolve the output path and write the snapshot."""
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    categories, measures = asyncio.run(build(output_path))
    print(f"Wrote {measures} measures and {categories} categories to {output_path}")


if __name__ == "__main__":
    main()
