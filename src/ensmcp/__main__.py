"""CLI entry point: runs the ENS Navegable MCP server over stdio.

Queries are answered from the snapshot shipped with the package, so the server
starts and responds with no Chrome, no display and no network. Live checks are
opt-in through ``--check-updates`` or ``--live``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence
from enum import StrEnum
from typing import TYPE_CHECKING

from mcp.server.mcpserver import MCPServer

from ensmcp.guia.loader import load_packaged_guide
from ensmcp.mcp_server.server import build_server
from ensmcp.snapshot.repository import RefreshingRepository, SnapshotRepository

if TYPE_CHECKING:
    from ensmcp.scraping.live_session import LiveSession

MODE_ENV_VAR = "ENSMCP_MODE"
LIVE_CHECK_ENV_VAR = "ENSMCP_LIVE_CHECK"  # backwards-compatible override


class ServerMode(StrEnum):
    OFFLINE = "offline"
    CHECK_UPDATES = "check-updates"
    LIVE = "live"


def _parse_mode(argv: Sequence[str] | None = None) -> ServerMode:
    parser = argparse.ArgumentParser(description="Servidor MCP del ENS")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--offline", dest="mode", action="store_const", const=ServerMode.OFFLINE)
    modes.add_argument(
        "--check-updates", dest="mode", action="store_const", const=ServerMode.CHECK_UPDATES
    )
    modes.add_argument("--live", dest="mode", action="store_const", const=ServerMode.LIVE)
    args = parser.parse_args(argv)
    if isinstance(args.mode, ServerMode):
        return args.mode
    configured = os.environ.get(MODE_ENV_VAR)
    if configured is None:
        legacy = os.environ.get(LIVE_CHECK_ENV_VAR)
        configured = "offline" if legacy == "0" else ("live" if legacy else "offline")
    try:
        return ServerMode(configured)
    except ValueError as exc:
        valid = ", ".join(mode.value for mode in ServerMode)
        raise SystemExit(f"{MODE_ENV_VAR} debe ser uno de: {valid}") from exc


def build_wiring(
    session: LiveSession | None, *, adopt_live: bool = True
) -> tuple[MCPServer, RefreshingRepository]:
    """Wire the snapshot, the live site and the tools onto one ``session``.

    Split out of ``serve()`` so the wiring can be exercised in-process against a
    fixture site. ``serve()`` only ever runs over stdio, so everything it decided
    was reachable from a test solely as a subprocess against the real ENS — and
    what a subprocess cannot do is *change* the page and check the server
    noticed. That gap is exactly where ``refresh_live_page`` sat reporting
    "unchanged" about a page it never re-read: every test of the tool used a bare
    ``NavegableRepository``, whose own ``refresh`` does reload, so the composition
    root could be wrong with the suite green.

    Returns the repository alongside the server because ``serve()`` owns its
    lifecycle (the startup check, the shutdown cancel) and a test wants to read
    its status.
    """
    snapshot = SnapshotRepository.from_package_data()
    if session is None:
        repo = RefreshingRepository(snapshot, snapshot, adopt_live=False)
        server = build_server(
            repo,
            status=repo.status_payload,
            guia=load_packaged_guide(),
        )
        return server, repo

    from ensmcp.scraping.navegable_repository import NavegableRepository

    live = NavegableRepository(session)
    # ``live.refresh`` is the half that was missing, and without it the tools did
    # not do what their names say. ``LiveSession`` loads the page once and caches
    # the content frame, and ``NavegableRepository`` caches both build assets,
    # all for the whole process — that is the design, it is what makes a query
    # cost one DOM read instead of a page load. So a comparison that only
    # re-scraped answered "unchanged" about a page nobody had re-read, hours
    # later, in the one tool whose whole job is to check *now*. Handing the
    # reload to ``RefreshingRepository`` rather than doing it here is what keeps
    # it inside the lock that the two reads it feeds already run under.
    repo = RefreshingRepository(snapshot, live, live.refresh, adopt_live=adopt_live)
    server = build_server(
        repo,
        refresh=repo.refresh,
        status=repo.status_payload,
        guia=load_packaged_guide(),
    )
    return server, repo


async def serve(mode: ServerMode = ServerMode.OFFLINE) -> None:
    """Serve over stdio, tearing the browser down on the *same* event loop.

    ``MCPServer.run()`` is just ``anyio.run(run_stdio_async)``: it spins up its
    own event loop and closes it on return. The browser is started lazily
    *inside* that loop, so Playwright's connection is bound to it — and closing
    the session afterwards from a fresh ``asyncio.run`` (what a ``finally``
    wrapped around ``server.run()`` does) does not merely fail, it blocks
    forever. Measured: a same-loop close returns in ~0.5s, while a cross-loop
    close had still not returned at a 25s cut-off, leaving the headed Chrome
    process and its temp profile directory behind on every shutdown.

    Awaiting ``run_stdio_async()`` here keeps serving and teardown on one loop,
    so the ``finally`` can actually reach the browser.

    Shutdown is driven by the client closing stdin, which is how MCP clients
    stop a stdio server: ``run_stdio_async()`` returns on EOF and the
    ``finally`` runs. Signals deliberately get no handler here — the stdin read
    that keeps the server alive happens on a worker thread, so neither SIGTERM
    nor SIGINT can interrupt it (verified against the real CLI: both leave the
    process running, with or without a handler installed). Installing one would
    only advertise a shutdown path that does not work.
    """
    session: LiveSession | None = None
    if mode is not ServerMode.OFFLINE:
        from ensmcp.scraping.live_session import LiveSession

        session = LiveSession()
    server, repo = build_wiring(session, adopt_live=mode is ServerMode.LIVE)
    # Scheduled here, inside the serving loop, so the task is bound to the same
    # loop the browser will be started on — the constraint this whole function
    # exists to respect.
    if mode is not ServerMode.OFFLINE:
        repo.start_background_check()
    try:
        await server.run_stdio_async()
    finally:
        # Cancel the check before closing the browser it may still be using —
        # and in its own ``try``, so that ordering cannot become a way to skip
        # the teardown. This whole function exists to make sure the browser is
        # actually closed (see the docstring: a cross-loop close leaves the
        # headed Chrome and its temp profile behind on every shutdown), and a
        # bare sequence here means one raising step silently forfeits that.
        try:
            await repo.close()
        finally:
            if session is not None:
                await session.close()


def main(argv: Sequence[str] | None = None) -> None:
    """Run the stdio MCP server until the client closes stdin."""
    asyncio.run(serve(_parse_mode(argv)))


if __name__ == "__main__":
    main()
