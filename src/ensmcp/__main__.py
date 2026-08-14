"""CLI entry point: runs the ENS Navegable MCP server over stdio.

Queries are answered from the snapshot shipped with the package, so the server
starts and responds with no Chrome, no display and no network. The live site is
still wired in, but only to keep that snapshot honest: one background check
compares the two and swaps the live data in if the site has changed, and the
``refresh_live_page`` tool runs the same comparison on demand. Chrome is
launched by those two paths and by nothing else.
"""

from __future__ import annotations

import asyncio
import os

from mcp.server.mcpserver import MCPServer

from ensmcp.guia.loader import load_packaged_guide
from ensmcp.mcp_server.server import build_server
from ensmcp.scraping.live_session import LiveSession
from ensmcp.scraping.navegable_repository import NavegableRepository
from ensmcp.snapshot.repository import RefreshingRepository, SnapshotRepository

# Set to "0" to keep the server from ever launching Chrome on its own. The
# startup check already degrades quietly when no browser is available, so this
# is for the case where launching one is unwanted rather than impossible: a
# machine where the window would pop up in someone's face, or a test run that
# must not reach the network. `refresh_live_page` still works on demand.
LIVE_CHECK_ENV_VAR = "ENSMCP_LIVE_CHECK"


def build_wiring(session: LiveSession) -> tuple[MCPServer, RefreshingRepository]:
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
    repo = RefreshingRepository(SnapshotRepository.from_package_data(), live, live.refresh)
    server = build_server(
        repo,
        refresh=repo.refresh,
        status=repo.status_payload,
        guia=load_packaged_guide(),
    )
    return server, repo


async def serve() -> None:
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
    session = LiveSession()
    server, repo = build_wiring(session)
    # Scheduled here, inside the serving loop, so the task is bound to the same
    # loop the browser will be started on — the constraint this whole function
    # exists to respect.
    if os.environ.get(LIVE_CHECK_ENV_VAR) != "0":
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
            await session.close()


def main() -> None:
    """Run the stdio MCP server until the client closes stdin."""
    asyncio.run(serve())


if __name__ == "__main__":
    main()
