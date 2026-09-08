"""System profile MCP tools."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from ensmcp.domain.profiles import (
    SystemProfile,
    evaluate_profile_scope,
    explain_profile_measure,
    resolve_profile_scope,
)
from ensmcp.domain.repository import MeasureRepository
from ensmcp.mcp_server.annotations import READ_ONLY
from ensmcp.mcp_server.boundary import _profile_controls, _require_measure
from ensmcp.mcp_server.presenters import _profile_measure_to_dict, _profile_scope_to_dict


def register_profile_tools(server: MCPServer, repository: MeasureRepository) -> None:
    @server.tool(annotations=READ_ONLY, structured_output=True)
    async def evaluate_system_profile(profile: SystemProfile) -> dict[str, Any]:
        """Evalúa un perfil completo y sus subsistemas contra el Anexo II.

        Los máximos se calculan con las dimensiones del sistema, sus activos de
        información y sus servicios. Cada resultado conserva la justificación y
        el origen que llevó a ese nivel. Los subsistemas pueden heredar esos
        máximos o evaluarse de forma aislada.
        """
        _, measures = await repository.fetch_corpus()
        controls = _profile_controls(profile, measures)

        def evaluate(subsystem_id: str | None = None) -> dict[str, Any]:
            scope = resolve_profile_scope(profile, controls, subsystem_id)
            return _profile_scope_to_dict(evaluate_profile_scope(scope, measures))

        return {
            "profile_id": profile.profile_id,
            "system": profile.system,
            **evaluate(),
            "subsystems": [
                {
                    "name": subsystem.name,
                    "inheritance": subsystem.inheritance,
                    **evaluate(subsystem.subsystem_id),
                }
                for subsystem in profile.subsystems
            ],
        }

    @server.tool(annotations=READ_ONLY, structured_output=True)
    async def explain_applicability(
        code: str,
        profile: SystemProfile,
        subsystem_id: str | None = None,
    ) -> dict[str, Any]:
        """Explica por qué una medida aplica, no aplica o fue forzada por un perfil."""
        _, measures = await repository.fetch_corpus()
        measure = _require_measure(measures, code)
        scope = resolve_profile_scope(
            profile,
            _profile_controls(profile, measures),
            subsystem_id,
        )
        return _profile_measure_to_dict(explain_profile_measure(measure, scope))
