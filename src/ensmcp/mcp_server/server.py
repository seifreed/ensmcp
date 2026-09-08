"""Compose the MCP server from its capability registrars."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from mcp.server.mcpserver import MCPServer

from ensmcp.domain.crosswalk import DataPack
from ensmcp.domain.dda import DDAStore
from ensmcp.domain.models import Guia808
from ensmcp.domain.repository import MeasureRepository
from ensmcp.mcp_server.audit import register_audit_tools
from ensmcp.mcp_server.contracts import Clock, ExportHandler, RefreshHandler, StatusHandler
from ensmcp.mcp_server.crosswalks import register_crosswalk_tools
from ensmcp.mcp_server.declarations import register_dda_tools
from ensmcp.mcp_server.measures import register_measure_tools
from ensmcp.mcp_server.profiles import register_profile_tools
from ensmcp.mcp_server.resources import register_resources
from ensmcp.mcp_server.runtime import register_runtime_tools

SCHEMA_VERSION = "1.0.0"


def build_server(
    repository: MeasureRepository,
    *,
    refresh: RefreshHandler | None = None,
    status: StatusHandler | None = None,
    guia: Guia808 | None = None,
    dda_store: DDAStore | None = None,
    export_handler: ExportHandler | None = None,
    data_packs: Sequence[DataPack] = (),
    clock: Clock = lambda: datetime.now(UTC),
) -> MCPServer:
    """Build the MCP server with the configured domain and adapter capabilities."""
    server: MCPServer = MCPServer(
        name="ensmcp",
        instructions="Consulta las medidas de seguridad del ENS Navegable (CCN-CERT).",
    )
    register_resources(server, repository, status, guia)
    register_measure_tools(server, repository)
    register_profile_tools(server, repository)
    register_audit_tools(server, repository, guia)
    register_crosswalk_tools(server, repository, data_packs)
    register_dda_tools(server, repository, dda_store, export_handler, clock)
    register_runtime_tools(server, refresh, status, guia)
    return server
