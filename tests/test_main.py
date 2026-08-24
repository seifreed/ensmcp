"""Real subprocess test for the `python -m ensmcp` entry point.

Spawns the actual CLI over real stdio pipes (no mocks) and talks to it
with a real MCP client session. COVERAGE_PROCESS_START makes the
subprocess itself contribute to the coverage report via the .pth hook
installed by tests/conftest.py.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult

from ensmcp.__main__ import MODE_ENV_VAR, ServerMode, _parse_mode, build_wiring, main
from tests.support import (
    CHOICE_MEASURE_ROW_HTML,
    CONTENT_PAGE_FILENAME,
    ENS_NORM_JS_FILENAME,
    MEASURE_ROW_HTML,
    MINIMAL_ENS_NORM_JS,
    MINIMAL_REQUISITOS_JS,
    OUTER_IFRAME_HTML,
    OUTER_PAGE_FILENAME,
    REINFORCED_MEASURE_ROW_HTML,
    REQUISITOS_JS_FILENAME,
    Utf8RequestHandler,
    check,
    leftover_profiles,
    local_session,
    require,
    table_html,
    threaded_http_server,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Las tres filas que sirve el sitio de abajo, una más en cada carga.
_GROWING_ROWS = (MEASURE_ROW_HTML, REINFORCED_MEASURE_ROW_HTML, CHOICE_MEASURE_ROW_HTML)


@contextmanager
def growing_content_site() -> Iterator[str]:
    """Sirve un #tablaResumen con una fila más en cada petición de la página.

    Un servidor HTTP real sobre un socket real, no un doble. Es lo que hace
    falta para distinguir "el servidor ha vuelto a leer la página" de "el
    servidor ha vuelto a leer el DOM que ya tenía en el navegador": lo segundo
    devuelve siempre lo mismo por muchas veces que se pida.
    """
    state = {"loads": 0}

    class _Handler(Utf8RequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler protocol
            if self.path == f"/{OUTER_PAGE_FILENAME}":
                self._send(OUTER_IFRAME_HTML, "text/html")
            elif self.path == f"/{CONTENT_PAGE_FILENAME}":
                state["loads"] = min(state["loads"] + 1, len(_GROWING_ROWS))
                self._send(table_html(*_GROWING_ROWS[: state["loads"]]), "text/html")
            elif self.path == f"/{REQUISITOS_JS_FILENAME}":
                self._send(MINIMAL_REQUISITOS_JS, "application/javascript")
            elif self.path == f"/{ENS_NORM_JS_FILENAME}":
                self._send(MINIMAL_ENS_NORM_JS, "application/javascript")
            else:
                self.send_error(404)

    with threaded_http_server(_Handler) as base_url:
        yield base_url


async def _structured(server: MCPServer, name: str) -> dict[str, Any]:
    """El contenido estructurado de una tool sin argumentos."""
    result = await server.call_tool(name, {})
    if not isinstance(result, CallToolResult):
        raise TypeError(f"se esperaba un CallToolResult, llegó {type(result).__name__}")
    return result.structured_content or {}


async def _measures(server: MCPServer) -> list[dict[str, Any]]:
    """Las medidas que sirve `list_measures`, que llegan envueltas en "result"."""
    payload: list[dict[str, Any]] = (await _structured(server, "list_measures"))["result"]
    return payload


async def test_refresh_live_page_actually_rereads_the_page() -> None:
    # Regresión sobre el **cableado**, no sobre las piezas. `refresh_live_page`
    # llamaba a `RefreshingRepository.refresh()` a secas, que re-scrapea a través
    # de `LiveSession.frame()` — y esa frame se cachea al arrancar y no se vuelve
    # a cargar nunca. Resultado: la tool cuyo trabajo es comprobar *ahora*
    # respondía "unchanged" sobre una página que nadie había vuelto a leer, y
    # `NavegableRepository.refresh()` era código muerto en producción.
    #
    # Ningún test lo veía porque todos montaban un `NavegableRepository` pelado,
    # cuyo propio `refresh` sí recarga. Éste monta lo que monta el CLI.
    with growing_content_site() as base_url:
        async with local_session(base_url) as session:
            server, repo = build_wiring(session)

            first = await _structured(server, "refresh_live_page")
            after_first = await _measures(server)
            second = await _structured(server, "refresh_live_page")
            after_second = await _measures(server)

    check(first == second == {"status": "ok"}, f"la tool devolvió {first} y {second}")
    # Con el bug las dos llamadas devolvían el mismo corpus: la página se leía
    # una vez, al arrancar, y de ahí no se movía.
    check(
        len(after_second) > len(after_first),
        f"la segunda comprobación no vio la página nueva: {len(after_first)} y "
        f"{len(after_second)} medidas",
    )
    codes = {measure["code"] for measure in after_second}
    check("op.acc.5" in codes, f"faltaba la fila que sólo sirve la tercera carga: {sorted(codes)}")
    check(repo.status_payload()["live_check"] == "updated", f"status: {repo.status_payload()}")


async def test_closing_stdin_stops_the_real_cli_promptly() -> None:
    # End to end against the actual CLI over real pipes, exercising the way an
    # MCP client actually stops a stdio server: closing stdin. run_stdio_async
    # returns on EOF and serve()'s finally tears the browser down.
    #
    # This is the regression guard for a shutdown that hangs. serve() has to
    # await the teardown on the same loop that served: closing a Playwright
    # session from a fresh asyncio.run (what a finally around the synchronous
    # server.run() did) blocks forever instead of failing, so the process would
    # never exit at all once the browser had started.
    env = dict(os.environ)
    env["COVERAGE_PROCESS_START"] = str(_REPO_ROOT / "pyproject.toml")
    # create_subprocess_exec, not the subprocess module: argv list, no shell,
    # and native async pipes so the readiness round-trip needs no worker thread.
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "ensmcp",
        cwd=str(_REPO_ROOT),
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    stdin = require(process.stdin, "a stdin pipe was requested")
    stdout = require(process.stdout, "a stdout pipe was requested")
    try:
        # A real initialize round-trip is the readiness signal: once a
        # response comes back, the server is up and serving.
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "regression", "version": "1"},
            },
        }
        stdin.write((json.dumps(request) + "\n").encode())
        await stdin.drain()
        response = await asyncio.wait_for(stdout.readline(), timeout=30)
        check(response.strip() != b"", "the server never answered initialize")

        stdin.close()
        returncode = await asyncio.wait_for(process.wait(), timeout=30)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()

    check(returncode == 0, f"EOF should exit cleanly, got returncode {returncode}")


def test_console_script_entry_point_resolves_to_main() -> None:
    # pyproject declares `ensmcp = "ensmcp.__main__:main"`, and nothing
    # exercised that link: every other test launches the CLI as
    # `python -m ensmcp`, which resolves through __main__ instead. A stale or
    # mistyped entry point would therefore ship a broken `ensmcp` command with
    # the whole suite still green.
    scripts = {
        entry.name: entry for entry in importlib.metadata.entry_points(group="console_scripts")
    }
    entry_point = require(scripts.get("ensmcp"), "pyproject declares an `ensmcp` console script")

    check(
        entry_point.load() is main,
        f"the console script points at {entry_point.value}, not ensmcp.__main__:main",
    )


def test_cli_modes_are_explicit_and_offline_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MODE_ENV_VAR, raising=False)
    monkeypatch.delenv("ENSMCP_LIVE_CHECK", raising=False)

    check(_parse_mode([]) is ServerMode.OFFLINE)
    check(_parse_mode(["--check-updates"]) is ServerMode.CHECK_UPDATES)
    check(_parse_mode(["--live"]) is ServerMode.LIVE)


def test_importing_the_module_does_not_start_the_server() -> None:
    # Covers the `if __name__ == "__main__":` guard's false branch: a plain
    # import (as opposed to `python -m ensmcp`) must not call main().
    module = importlib.import_module("ensmcp.__main__")

    check(hasattr(module, "main"))


async def test_module_entry_point_serves_the_expected_tools() -> None:
    # Deliberately *not* marked network: this spawns the real CLI over real
    # stdio pipes, but only calls list_tools(). No tool is invoked, and
    # ENSMCP_LIVE_CHECK=0 keeps the startup comparison from launching Chrome,
    # so nothing is fetched. Keeping it in the offline subset is what puts
    # __main__.py's shutdown `finally` (which tears down the browser and its
    # temp profile dir) under test without needing the live site.
    env = dict(os.environ)
    env["COVERAGE_PROCESS_START"] = str(_REPO_ROOT / "pyproject.toml")
    env[MODE_ENV_VAR] = "offline"
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "ensmcp"], cwd=str(_REPO_ROOT), env=env
    )

    async with (
        stdio_client(params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()

    tool_names = {tool.name for tool in tools.tools}
    check(
        tool_names
        == {
            "list_categories",
            "list_measures",
            "get_measure",
            "search_measures",
            "declaracion_aplicabilidad",
            "alcance_auditoria",
            "requisitos_auditoria",
            "requisitos_articulos",
            "evidencias_auditoria",
            "snapshot_status",
        }
    )


@pytest.mark.network
async def test_the_cli_checks_the_live_site_on_startup() -> None:
    # The other half of the env-var branch, and the end-to-end proof of the
    # design: with the check enabled the CLI still answers from the snapshot
    # immediately, and the comparison against the real site lands behind it.
    env = dict(os.environ)
    env["COVERAGE_PROCESS_START"] = str(_REPO_ROOT / "pyproject.toml")
    env[MODE_ENV_VAR] = "live"
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "ensmcp"], cwd=str(_REPO_ROOT), env=env
    )
    before = leftover_profiles()

    async with (
        stdio_client(params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        # Answered from the snapshot while the browser is still starting.
        measures = await session.call_tool("list_measures", {})
        payload = (measures.structured_content or {})["result"]
        check(len(payload) == 73, f"served {len(payload)} measures before the check finished")

        # Now wait the comparison out and read what it concluded.
        for _ in range(60):
            status = (await session.call_tool("snapshot_status", {})).structured_content or {}
            if status.get("live_check") != "pending":
                break
            await asyncio.sleep(1)

        check(
            status.get("live_check") in {"unchanged", "updated"},
            f"the live check did not complete: {status}",
        )

    # Y al salir, el navegador que esa comprobación arrancó tiene que quedar
    # desmontado. Es lo único que `serve()` existe para garantizar —su docstring
    # está entera dedicada a que cerrar desde otro bucle de eventos no falla,
    # bloquea, y deja "el proceso de Chrome headed y su perfil temporal detrás en
    # cada apagado"— y nadie lo comprobaba: este test esperaba a que la
    # comprobación terminase y se iba, y el de stdin sólo mira el código de
    # salida, que es 0 igual con un Chrome huérfano vivo.
    #
    # Es aquí y no en aquél porque este es el único que garantiza que el
    # navegador llegó a arrancar: espera a que la comprobación en vivo termine.
    # Se sondea en vez de mirar una vez porque el hijo desmonta después de que el
    # cliente suelte las tuberías; si el desmontaje no ocurre, el perfil no
    # desaparece y esto falla igual, sólo que unos segundos más tarde.
    for _ in range(30):
        leaked = leftover_profiles() - before
        if not leaked:
            break
        await asyncio.sleep(1)

    check(not leaked, f"el apagado dejó el perfil temporal del navegador: {sorted(leaked)}")


async def test_the_env_var_keeps_the_cli_from_ever_checking_the_live_site() -> None:
    """`ENSMCP_LIVE_CHECK=0` promete no arrancar Chrome, y nadie lo comprobaba.

    El otro lado del `if` sí estaba afirmado —el test de red de abajo quita la
    variable y espera a que la comprobación termine—, pero el lado que la
    respeta no: **borrar la guarda entera dejaba la suite en verde**. Y el
    subproceso lanzaba un navegador mientras tanto, incluido el del test de aquí
    arriba, cuyo propio comentario dice que con la variable a "0" no se arranca
    ninguno.

    Lo que se afirma es que la comprobación no llega ni a empezar: `live_check`
    se queda en "pending" y no se mueve. Sin la guarda pasa a "unchanged",
    "updated" o "unavailable" —cualquiera de los tres sirve para delatarla—, en
    cuanto la comprobación de fondo termina, que con la web real es cosa de un
    par de segundos.

    Es la razón de ser de la variable: una máquina donde la ventana saltaría a
    la cara de alguien, o una ejecución que no puede tocar la red.
    """
    env = dict(os.environ)
    env["COVERAGE_PROCESS_START"] = str(_REPO_ROOT / "pyproject.toml")
    env[MODE_ENV_VAR] = "offline"
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "ensmcp"], cwd=str(_REPO_ROOT), env=env
    )

    async with (
        stdio_client(params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        seen = []
        for _ in range(12):
            status = (await session.call_tool("snapshot_status", {})).structured_content or {}
            seen.append(status.get("live_check"))
            if seen[-1] != "pending":
                break
            await asyncio.sleep(0.5)

    check(set(seen) == {"pending"}, f"la comprobación arrancó pese a la variable: {seen}")
