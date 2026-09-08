"""Register tools backed by optional runtime infrastructure."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ensmcp.domain.models import Guia808
from ensmcp.mcp_server.annotations import EXTERNAL_READ, READ_ONLY
from ensmcp.mcp_server.contracts import RefreshHandler, StatusHandler


def register_runtime_tools(
    server: MCPServer,
    refresh: RefreshHandler | None,
    status: StatusHandler | None,
    guia: Guia808 | None,
) -> None:
    """Register live-refresh and source-status tools when configured."""
    if refresh is not None:

        @server.tool(annotations=EXTERNAL_READ, structured_output=True)
        async def refresh_live_page() -> dict[str, str]:
            """Comprueba ahora la página live de ENS Navegable y actualiza si cambió."""
            await refresh()
            return {"status": "ok"}

    if status is not None:

        @server.tool(annotations=READ_ONLY, structured_output=True)
        async def snapshot_status() -> dict[str, object]:
            """Devuelve el origen y la frescura de los datos servidos."""
            payload = status()
            if guia is not None:
                payload["guia_808"] = guia.source
            return payload
