"""Tests for the snapshot repositories.

``SnapshotRepository`` runs against the real committed snapshot — the file the
installed package ships — because "does the shipped data actually load and hold
the corpus" is the only question worth asking of it.

``RefreshingRepository`` runs against a real local fixture site through a real
browser (``tests.support.local_repository``), so the live side is real code, not
a stand-in: one site that differs from the snapshot, and one that is broken.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ensmcp.domain.models import ApplicabilityLevel
from ensmcp.scraping.live_session import LiveSession
from ensmcp.scraping.navegable_repository import NavegableRepository
from ensmcp.snapshot.codec import dump
from ensmcp.snapshot.repository import (
    LiveCheck,
    RefreshingRepository,
    SnapshotRepository,
    default_snapshot_text,
)
from tests.support import (
    CHOICE_MEASURE_ROW_HTML,
    CONTENT_PAGE_FILENAME,
    ENS_NORM_JS_FILENAME,
    HEADER_ONLY_TABLE_HTML,
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
    site_files,
    table_html,
    threaded_http_server,
)

_SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "src" / "ensmcp" / "data" / "anexo_ii.json"

# A fixture site whose two measures are nothing like the real Anexo II, so a
# live check against it must report a difference from the shipped snapshot.
_DIFFERENT_TABLE = table_html(REINFORCED_MEASURE_ROW_HTML, CHOICE_MEASURE_ROW_HTML)
_DIFFERENT_SITE = site_files(_DIFFERENT_TABLE, MINIMAL_REQUISITOS_JS, MINIMAL_ENS_NORM_JS)


def _shipped() -> SnapshotRepository:
    return SnapshotRepository.from_package_data()


async def test_the_shipped_snapshot_holds_the_whole_anexo_ii() -> None:
    repository = _shipped()

    categories, measures = await repository.fetch_corpus()

    check(len(measures) == 73, f"expected 73 measures, got {len(measures)}")
    check(len(categories) == 18, f"expected 18 categories, got {len(categories)}")
    check(repository.captured_at.startswith("20"), f"captured_at was {repository.captured_at!r}")


def test_a_measureless_snapshot_is_refused_at_startup() -> None:
    with pytest.raises(ValueError, match="snapshot has no categories or security measures"):
        SnapshotRepository(dump([], [], "2026-01-01T00:00:00+00:00"))


def test_a_snapshot_with_a_category_in_the_wrong_group_is_refused() -> None:
    document = json.loads(default_snapshot_text())
    document["categories"][0]["group"] = "mp"

    with pytest.raises(ValueError, match="inconsistent category"):
        SnapshotRepository(json.dumps(document))


def test_a_snapshot_with_a_measure_in_the_wrong_category_is_refused() -> None:
    document = json.loads(default_snapshot_text())
    document["measures"][0]["category_code"] = "mp"

    with pytest.raises(ValueError, match="inconsistent measure code"):
        SnapshotRepository(json.dumps(document))


def test_a_snapshot_whose_parsed_levels_contradict_the_raw_cells_is_refused() -> None:
    document = json.loads(default_snapshot_text())
    document["measures"][0]["raw_levels"][0] = "n.a."

    with pytest.raises(ValueError, match="contradicts its raw levels"):
        SnapshotRepository(json.dumps(document))


def test_a_snapshot_with_an_unrecognized_raw_level_cell_is_refused() -> None:
    document = json.loads(default_snapshot_text())
    document["measures"][0]["raw_levels"][0] = "aplicación desconocida"

    with pytest.raises(ValueError, match=r"neither 'aplica' nor 'n.a.'"):
        SnapshotRepository(json.dumps(document))


@pytest.mark.parametrize("field", ["dimensions", "levels"])
def test_a_snapshot_with_an_empty_measure_field_is_refused(field: str) -> None:
    document = json.loads(default_snapshot_text())
    document["measures"][0][field] = []

    with pytest.raises(ValueError, match="has no dimensions or levels"):
        SnapshotRepository(json.dumps(document))


def test_a_snapshot_may_omit_raw_level_cells() -> None:
    document = json.loads(default_snapshot_text())
    document["measures"][0]["raw_levels"] = []

    SnapshotRepository(json.dumps(document))


def test_a_snapshot_whose_reinforcements_contradict_the_raw_cells_is_refused() -> None:
    document = json.loads(default_snapshot_text())
    measure = next(item for item in document["measures"] if item["reinforcements"])
    measure["reinforcements"] = []

    with pytest.raises(ValueError, match="contradicts its raw reinforcements"):
        SnapshotRepository(json.dumps(document))


def test_a_snapshot_with_a_reinforcement_at_an_excluded_level_is_refused() -> None:
    document = json.loads(default_snapshot_text())
    measure = next(item for item in document["measures"] if item["reinforcements"])
    excluded = measure["reinforcements"][0]["level"]
    measure["levels"] = [level for level in ("basico", "medio", "alto") if level != excluded]
    measure["raw_levels"] = []

    with pytest.raises(ValueError, match="reinforcement at an excluded level"):
        SnapshotRepository(json.dumps(document))


@pytest.mark.parametrize(
    ("captured_at", "message"),
    [("ayer", "not an ISO 8601 timestamp"), ("2026-01-01T00:00:00", "has no timezone")],
)
def test_a_snapshot_with_an_invalid_capture_time_is_refused(captured_at: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        SnapshotRepository(
            dump(list(_shipped().categories), list(_shipped().measures[:1]), captured_at)
        )


async def test_both_snapshot_repositories_answer_the_whole_corpus() -> None:
    live_session_free = SnapshotRepository.from_package_data()
    refreshing = RefreshingRepository(_shipped(), live_session_free)

    for repository in (live_session_free, refreshing):
        categories, measures = await repository.fetch_corpus()

        check(len(categories) == 18 and len(measures) == 73, f"{len(categories)}/{len(measures)}")


async def test_the_shipped_snapshot_answers_without_a_browser() -> None:
    # The claim the whole snapshot exists for: a full answer, including the
    # reinforcement wording, with no Chrome, no display and no network.
    measures = (await _shipped().fetch_corpus())[1]

    op_acc_5 = require(next((m for m in measures if m.code == "op.acc.5"), None))
    basico = {
        r.code: r.alternative
        for r in op_acc_5.reinforcements
        if r.level is ApplicabilityLevel.BASICO
    }
    check(basico == {"R1": True, "R2": True, "R3": True, "R4": True}, f"basico was {basico}")
    check(all(r.text for r in op_acc_5.reinforcements), "a reinforcement shipped with no wording")


async def test_the_shipped_snapshot_carries_the_rd_wording_of_every_measure() -> None:
    # El corpus commiteado tiene que traer las 73 redacciones, no sólo poder
    # traerlas: el test de red lo comprueba contra la web, y esto contra el
    # fichero que de verdad se sirve cuando no hay navegador.
    measures = (await _shipped().fetch_corpus())[1]

    untexted = sorted(measure.code for measure in measures if not measure.norm_text)
    check(not untexted, f"medidas sin la redacción del RD en el snapshot: {untexted}")
    org_4 = require(next((m for m in measures if m.code == "org.4"), None))
    check(
        org_4.norm_text.startswith("Se establecerá un proceso formal de autorizaciones"),
        f"org.4 empezaba por {org_4.norm_text[:60]!r}",
    )
    check(org_4.description != org_4.norm_text, "description y norm_text coinciden")


def test_package_data_and_the_repo_file_are_the_same_snapshot() -> None:
    # importlib.resources reads from the installed package; the build script
    # writes to the source tree. An editable install makes those the same file,
    # and this is what notices if that ever stops being true.
    check(default_snapshot_text() == _SNAPSHOT_PATH.read_text(encoding="utf-8"))


async def test_the_loaded_corpus_cannot_be_mutated_through_the_repository() -> None:
    # Regresión: `measures` y `categories` eran listas públicas, así que
    # `repository.measures.clear()` vaciaba el repositorio y todo
    # Una lectura posterior respondía con nada porque la copia se hacía después
    # de la mutación. Y no era teórico:
    # `RefreshingRepository` recibe esas mismas listas por referencia y las
    # sostiene lo que dura el proceso.
    repository = _shipped()

    for corpus in (repository.measures, repository.categories):
        check(isinstance(corpus, tuple), f"el corpus llegó como {type(corpus).__name__}")

    # Lo que sí se entrega es una copia: mutarla no toca al repositorio.
    served = (await repository.fetch_corpus())[1]
    served.clear()

    check(len((await repository.fetch_corpus())[1]) == 73, "el repositorio sirvió estado mutado")


async def test_refreshing_serves_the_snapshot_before_any_check_runs() -> None:
    # No background check started: the repository must already be answering.
    async with local_repository(_DIFFERENT_SITE) as live:
        repository = RefreshingRepository(_shipped(), live)

        measures = (await repository.fetch_corpus())[1]

        check(len(measures) == 73)
        status = repository.status_payload()
        check(status["live_check"] == LiveCheck.PENDING.value)
        check(status["measures"] == 73)


async def test_refreshing_adopts_a_live_page_that_differs() -> None:
    async with local_repository(_DIFFERENT_SITE) as live:
        repository = RefreshingRepository(_shipped(), live)

        await repository.refresh()

        measures = (await repository.fetch_corpus())[1]
        check(
            {m.code for m in measures} == {"mp.s.4", "op.acc.5"}, "the live rows were not adopted"
        )
        status = repository.status_payload()
        check(status["live_check"] == LiveCheck.UPDATED.value)
        check(status["measures"] == 2)
        check("build_snapshot.py" in str(status["detail"]))


async def test_check_updates_does_not_adopt_live_data() -> None:
    async with local_repository(_DIFFERENT_SITE) as live:
        repository = RefreshingRepository(_shipped(), live, adopt_live=False)

        await repository.refresh()

        measures = (await repository.fetch_corpus())[1]
        check(len(measures) == 73, "check-updates sustituyó el snapshot")
        status = repository.status_payload()
        check(status["live_check"] == LiveCheck.UPDATED.value)
        check(status["captured_at"] == _shipped().captured_at)
        check("no se han adoptado" in str(status["detail"]))


async def test_adopting_the_live_page_restamps_what_is_being_served() -> None:
    # `captured_at` describe lo que se sirve, y tras adoptar la web eso ya no es
    # el fichero. Antes seguía reportando la fecha del snapshot descartado, con
    # lo que el payload mezclaba dos corpus: `measures` contaba las filas de la
    # web y `captured_at` fechaba el snapshot. Justo en la tool cuyo trabajo es
    # decir cómo de fresca es la respuesta.
    #
    # Sin mocks: reloj real, y se comprueba que la marca cae dentro de la
    # ventana en la que de verdad ocurrió el refresh.
    async with local_repository(_DIFFERENT_SITE) as live:
        repository = RefreshingRepository(_shipped(), live)
        del_fichero = _shipped().captured_at
        antes = datetime.now(UTC).replace(microsecond=0)

        await repository.refresh()

        despues = datetime.now(UTC)
        marca = datetime.fromisoformat(str(repository.status_payload()["captured_at"]))
        check(marca != datetime.fromisoformat(del_fichero), "sigue fechando el snapshot descartado")
        check(antes <= marca <= despues, f"{antes} <= {marca} <= {despues}")


async def test_an_unchanged_check_keeps_the_files_own_date() -> None:
    # La otra mitad: si la web coincide, lo que se sirve *es* el snapshot, así
    # que su fecha es la del fichero y no debe moverse.
    repository = RefreshingRepository(_shipped(), _shipped())

    await repository.refresh()

    check(repository.status_payload()["captured_at"] == _shipped().captured_at)


async def test_refreshing_reports_unchanged_when_the_live_page_matches() -> None:
    # Serve the snapshot's own corpus as the fixture site's snapshot: the live
    # repository here is another SnapshotRepository holding identical data, so
    # the comparison has to come out equal and leave the data untouched.
    repository = RefreshingRepository(_shipped(), _shipped())

    await repository.refresh()

    check(repository.status_payload()["live_check"] == LiveCheck.UNCHANGED.value)
    check(len((await repository.fetch_corpus())[1]) == 73)


async def test_a_second_check_still_reports_the_file_as_outdated() -> None:
    # El veredicto es sobre el **fichero**, y una segunda comprobación no lo
    # cambia: la web sigue difiriendo del snapshot commiteado aunque ya coincida
    # con lo que hay en memoria. Antes se comparaba contra la memoria, así que
    # la primera comprobación adoptaba la web y la segunda —un simple
    # ``refresh_live_page`` después del arranque— contestaba "coincide con el
    # snapshot" y se llevaba por delante el aviso de regenerar el fichero, que
    # es la única señal de que hay algo que commitear.
    async with local_repository(_DIFFERENT_SITE) as live:
        repository = RefreshingRepository(_shipped(), live)

        await repository.refresh()
        await repository.refresh()

        status = repository.status_payload()
        check(status["live_check"] == LiveCheck.UPDATED.value, "la segunda pasada se dio por buena")
        check("build_snapshot.py" in str(status["detail"]))
        check(status["measures"] == 2)


async def test_an_empty_live_page_does_not_replace_the_snapshot() -> None:
    # La carrera que el docstring de `build_snapshot` describe, ejecutada: la
    # tabla es visible en cuanto se pinta su cabecera —lo único que espera
    # `LiveSession`— y las filas las inyecta después el script del frame. Una
    # comprobación que cae en ese hueco leía cero filas, y el corpus vacío
    # entraba directo en memoria: todas las tools contestaban con 0 medidas
    # mientras `snapshot_status` decía "updated", o sea "la web difiere y estoy
    # sirviéndola". Un servidor con datos perfectamente buenos en disco,
    # sirviendo nada, y diciendo que todo fue bien.
    #
    # `build_snapshot.check` se niega a *escribir* eso desde el principio; esta
    # ruta, que decide qué se **sirve**, no tenía la contraparte.
    files = site_files(HEADER_ONLY_TABLE_HTML, MINIMAL_REQUISITOS_JS, MINIMAL_ENS_NORM_JS)
    async with local_repository(files) as live:
        repository = RefreshingRepository(_shipped(), live)

        # La puerta síncrona levanta el error, como con un navegador que no
        # arranca: quien preguntó explícitamente merece saberlo.
        with pytest.raises(ValueError, match="no sirvió ninguna medida"):
            await repository.refresh()

        # Y además lo anota. Anotarlo sólo en la ruta de fondo dejaba muda esta
        # puerta: tras un `refresh_live_page` que falla, `snapshot_status` seguía
        # contestando "pending", o sea "aún sin comprobar", cuando se acababa de
        # comprobar y había fallado. Lo que se reporta es el último intento, no
        # el último que salió bien.
        after = repository.status_payload()
        check(after["live_check"] == LiveCheck.UNAVAILABLE.value, f"status: {after}")
        check("no sirvió ninguna medida" in str(after["detail"]), f"detail: {after['detail']}")

        categories, measures = await repository.fetch_corpus()
        check(len(measures) == 73, "se vació el corpus servido")
        check(len(categories) == 18)

        # Y la de fondo sólo degrada el estado, sin tirar el servidor.
        repository.start_background_check()
        await require(repository._task)
        status = repository.status_payload()

        check(status["live_check"] == LiveCheck.UNAVAILABLE.value, f"status: {status}")
        check(status["measures"] == 73, "el payload contó un corpus vacío")
        check(status["captured_at"] == _shipped().captured_at, "se movió la fecha del fichero")
        check(len((await repository.fetch_corpus())[1]) == 73)


async def test_refreshing_survives_a_live_page_it_cannot_scrape() -> None:
    # A page with no iframe: exactly what a WAF interstitial or a redesign
    # looks like from here. The snapshot must keep being served.
    with local_site({OUTER_PAGE_FILENAME: "<p>no iframe here</p>"}) as base_url:
        async with local_session(base_url, timeout_ms=2000) as session:
            repository = RefreshingRepository(_shipped(), NavegableRepository(session))

            repository.start_background_check()
            # Idempotent: a second call must not schedule a second browser run.
            # Se afirma, que es lo que faltaba: llamarlo dos veces y no mirar el
            # resultado dejaba pasar la guarda entera. Sin ella la segunda tarea
            # pisa a la primera en `_task`, así que `close()` sólo cancela la
            # segunda y la primera se queda corriendo contra un navegador que se
            # está cerrando por debajo — justo lo que `close()` existe para
            # evitar.
            scheduled = repository._task
            repository.start_background_check()
            check(repository._task is scheduled, "la segunda llamada programó otra comprobación")
            await repository.close()
            # Closing twice is safe, which is what __main__'s finally relies on.
            await repository.close()

            check(len((await repository.fetch_corpus())[1]) == 73)


async def test_a_failed_check_is_reported_not_raised() -> None:
    with local_site({OUTER_PAGE_FILENAME: "<p>no iframe here</p>"}) as base_url:
        async with local_session(base_url, timeout_ms=2000) as session:
            repository = RefreshingRepository(_shipped(), NavegableRepository(session))

            # The synchronous door does raise — a caller who asked explicitly
            # deserves the error...
            with pytest.raises(Exception, match=r"tablaResumen|iframe"):
                await repository.refresh()

            # ...but the background one only downgrades the reported status.
            repository.start_background_check()
            await require(repository._task)
            status = repository.status_payload()
            check(status["live_check"] == LiveCheck.UNAVAILABLE.value)
            check("no se pudo comprobar" in str(status["detail"]))
            check(len((await repository.fetch_corpus())[1]) == 73)


async def test_a_background_check_that_succeeds_swaps_the_data_in() -> None:
    async with local_repository(_DIFFERENT_SITE) as live:
        repository = RefreshingRepository(_shipped(), live)

        repository.start_background_check()
        await require(repository._task)

        check(repository.status_payload()["live_check"] == LiveCheck.UPDATED.value)
        check(len((await repository.fetch_corpus())[1]) == 2)


async def test_closing_cancels_a_check_still_in_flight() -> None:
    # A page whose iframe never grows a #tablaResumen keeps the scrape waiting
    # for the whole timeout, so the check is provably still running when close()
    # cancels it. That is the shutdown path __main__ relies on: cancel the task
    # before the finally tears down the browser it is using.
    with local_site(site_files(NO_TABLE_HTML)) as base_url:
        async with local_session(base_url, timeout_ms=30000) as session:
            repository = RefreshingRepository(_shipped(), NavegableRepository(session))

            repository.start_background_check()
            await asyncio.sleep(0.5)
            await repository.close()

            # Cancelled, not failed: the status never moved off its initial value.
            check(repository.status_payload()["live_check"] == LiveCheck.PENDING.value)
            check(len((await repository.fetch_corpus())[1]) == 73)


@pytest.mark.network
async def test_the_shipped_snapshot_still_matches_the_live_site() -> None:
    # The question the snapshot has to keep answering: does serving from a file
    # give the same result as scraping? Regenerating from the live site and
    # comparing byte for byte is what turns "should be the same" into a build
    # failure the day the ENS Navegable changes. Fix by rerunning
    # scripts/build_snapshot.py and committing the diff.
    #
    # Una sola lectura, con `fetch_corpus`, y por lo mismo que la usa
    # `build_snapshot.py`: el fichero contra el que se compara sale de **una**
    # lectura de la tabla, así que pedir aquí las dos mitades por separado
    # comparaba un corpus de dos lecturas contra un artefacto de una. Además de
    # scrapear las 73 filas dos veces contra el sitio real, dejaba abierta la
    # ventana que `MeasureRepository.fetch_corpus` existe para cerrar: si la
    # página se re-renderiza entre las dos, esto falla acusando al ENS Navegable
    # de haber cambiado cuando lo único que pasó fue leerlo dos veces.
    shipped = _shipped()
    async with live_session(timeout_ms=60000) as session:
        categories, measures = await NavegableRepository(session).fetch_corpus()

    stamp = shipped.captured_at
    check(
        dump(categories, measures, stamp) == dump(shipped.categories, shipped.measures, stamp),
        "the live site no longer matches the committed snapshot — "
        "rerun scripts/build_snapshot.py",
    )


async def test_closing_a_repository_that_never_checked_is_a_no_op() -> None:
    repository = RefreshingRepository(_shipped(), _shipped())

    await repository.close()

    check(repository.status_payload()["live_check"] == LiveCheck.PENDING.value)


async def test_live_session_is_never_started_by_serving_the_snapshot() -> None:
    # The point of the whole design: constructing the live side must not launch
    # anything, so a machine with no Chrome still serves every query.
    session = LiveSession()
    repository = RefreshingRepository(_shipped(), NavegableRepository(session))

    categories, measures = await repository.fetch_corpus()
    check(len(measures) == 73)
    check(len(categories) == 18)
    await session.close()


@contextmanager
def _versioned_site() -> Iterator[str]:
    """Sirve un #tablaResumen que marca su número de carga en categoría y medida.

    Un servidor HTTP real cuya respuesta **cambia entre peticiones**, que es lo
    único que distingue "leyó dos veces el mismo estado de la página" de "leyó
    dos estados distintos". Ningún fixture estático lo distingue.
    """
    state = {"loads": 0}

    class _Handler(Utf8RequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler protocol
            if self.path == f"/{OUTER_PAGE_FILENAME}":
                self._send(OUTER_IFRAME_HTML, "text/html")
            elif self.path == f"/{CONTENT_PAGE_FILENAME}":
                state["loads"] += 1
                label = f"carga {state['loads']}"
                self._send(
                    table_html(
                        '<tr class="fondo_oscuro"><td class="fondo_oscuro">org</td>'
                        f"<td>{label}</td></tr>",
                        '<tr class="cuerpo_tabla_izq"><td class="cuerpo_tabla_izq">org.1</td>'
                        f"<td>{label}</td><td>Categoría</td>"
                        "<td>aplica</td><td>aplica</td><td>aplica</td></tr>",
                    ),
                    "text/html",
                )
            elif self.path == f"/{REQUISITOS_JS_FILENAME}":
                self._send(MINIMAL_REQUISITOS_JS, "application/javascript")
            elif self.path == f"/{ENS_NORM_JS_FILENAME}":
                self._send(MINIMAL_ENS_NORM_JS, "application/javascript")
            else:
                self.send_error(404)

    with threaded_http_server(_Handler) as base_url:
        yield base_url


async def test_a_reload_landing_mid_check_cannot_mix_two_page_states() -> None:
    # Dos lecturas distintas dejaban un corpus mitad de un estado de la página
    # y mitad del siguiente. El puerto de una sola operación lo hace imposible.
    #
    # Cada carga marca su número en la categoría y en el título de la medida, así
    # que dos números distintos en un mismo corpus **son** la mezcla. Pedir el
    # par de una vez lo vuelve irrepresentable, no sólo improbable.
    with _versioned_site() as base_url:
        async with local_session(base_url) as session:
            live = NavegableRepository(session)
            repository = RefreshingRepository(_shipped(), live, live.refresh)

            reloading = True

            async def reload_until_done() -> None:
                # Recargas continuas mientras corre la comprobación: una sola
                # recarga se consume en la cola del lock antes del hueco entre
                # las dos lecturas, así que no lo descubre. Martilleando, el
                # hueco se acierta siempre — y con una sola lectura no hay hueco
                # que acertar.
                while reloading:
                    await live.refresh()

            for _ in range(5):
                hammer = asyncio.create_task(reload_until_done())
                try:
                    await repository.refresh()
                finally:
                    reloading = False
                    await hammer

                categories, measures = await repository.fetch_corpus()
                category_names = {category.name for category in categories}
                titles = {measure.title for measure in measures}
                check(
                    category_names == titles,
                    f"corpus mezclado: categorías {category_names}, medidas {titles}",
                )
                reloading = True


async def test_no_text_the_server_serves_carries_a_run_of_spaces() -> None:
    # Guarda sobre el fichero que de verdad se distribuye. Una racha de espacios
    # no es cosmética: la búsqueda es por subcadena, así que un texto con dos
    # espacios donde el lector teclea uno queda inalcanzable. Las había en la
    # `description` y en una nota de op.exp.4 y mp.info.3, y no las veía nadie.
    measures = (await _shipped().fetch_corpus())[1]

    offenders = [
        measure.code
        for measure in measures
        for text in (
            measure.description,
            measure.norm_text,
            *(requirement.question for requirement in measure.audit_requirements),
            *(requirement.note for requirement in measure.audit_requirements),
            *(reinforcement.text for reinforcement in measure.reinforcements),
        )
        if re.search(r"[ ]{2,}", text)
    ]
    check(not offenders, f"medidas con espacios múltiples: {sorted(set(offenders))}")


async def test_the_comparison_baseline_never_moves_with_the_clock() -> None:
    # `_serialised` es la vara de medir contra la que se decide si la web difiere
    # del fichero, así que no puede depender de nada que se mueva. Usa una fecha
    # constante y no `self._captured_at` justamente por eso: ese valor cambia al
    # adoptar la web, y una vara que se mueve deja de ser una vara.
    #
    # Con `self._captured_at` el caso que se rompe es el retorno: la web difiere
    # (se adopta, la fecha pasa a ser la de la captura), luego la web **vuelve**
    # al corpus del fichero — y la comparación seguiría diciendo "updated",
    # porque el texto de la izquierda lleva la fecha de la adopción y el de
    # `_snapshot` la del fichero. Ninguno de los tests de arriba lo veía: todos
    # comparan una sola vez, o comparan tras adoptar algo que sigue difiriendo.
    #
    # Afirmarlo sobre el helper es lo que hace falta para cazarlo sin un sitio
    # que cambie dos veces bajo el navegador.
    snapshot = _shipped()
    repository = RefreshingRepository(snapshot, snapshot)

    before = repository._serialised(snapshot.categories, snapshot.measures)
    repository._captured_at = "1999-01-01T00:00:00+00:00"
    after = repository._serialised(snapshot.categories, snapshot.measures)

    check(before == after, "la vara de medir cambió al mover la fecha de lo servido")
    check(before == repository._snapshot, "el texto del fichero no coincide con su propia vara")


async def test_going_back_to_the_file_gives_back_the_files_own_date() -> None:
    """El retorno: la web difería, se adoptó, y luego vuelve a coincidir.

    ``captured_at`` describe **lo que se sirve**, así que en cuanto lo servido
    vuelve a ser el fichero byte a byte, su fecha tiene que volver a ser la del
    fichero. Si se quedara la de la adopción, ``snapshot_status`` fecharía como
    recién capturado un corpus que está en disco desde hace meses — y sería
    justo la tool cuyo trabajo es decir cómo de fresco es el dato.

    Los tests de arriba no lo veían: uno comprueba la ida (se adopta y la fecha
    se mueve) y otro el caso limpio (coincide a la primera y no se mueve). El
    que falta es el que pasa por los dos, y borrar la línea que restaura la
    fecha no hacía fallar la suite entera.

    La adopción previa se representa moviendo ``_captured_at``, como hace
    ``test_the_comparison_baseline_never_moves_with_the_clock`` con la misma
    justificación: montar un sitio que cambie dos veces bajo el navegador no
    haría más cierto lo que se afirma aquí.
    """
    snapshot = _shipped()
    repository = RefreshingRepository(snapshot, _shipped())
    repository._captured_at = "1999-01-01T00:00:00+00:00"

    await repository.refresh()

    status = repository.status_payload()
    check(status["live_check"] == LiveCheck.UNCHANGED.value, f"quedó {status['live_check']!r}")
    check(
        status["captured_at"] == snapshot.captured_at,
        f"sirve el fichero pero lo fecha como {status['captured_at']!r}",
    )
