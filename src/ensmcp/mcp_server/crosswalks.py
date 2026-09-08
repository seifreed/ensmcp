"""External compliance crosswalk MCP tools."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from mcp.server.mcpserver import MCPServer

from ensmcp.domain.crosswalk import DataPack, DataPackStatus
from ensmcp.domain.repository import MeasureRepository
from ensmcp.mcp_server.annotations import READ_ONLY
from ensmcp.mcp_server.boundary import (
    _normalize,
    _normalize_filter_value,
    _paginate,
    _require_measure,
)
from ensmcp.mcp_server.presenters import _data_pack_to_dict


def register_crosswalk_tools(
    server: MCPServer, repository: MeasureRepository, data_packs: Sequence[DataPack]
) -> None:
    @server.tool(annotations=READ_ONLY, structured_output=True)
    async def list_data_packs(include_inactive: bool = False) -> list[dict[str, Any]]:
        """Lista los crosswalks externos, su procedencia, cobertura y vigencia."""
        return [
            _data_pack_to_dict(pack)
            for pack in data_packs
            if include_inactive or pack.status is DataPackStatus.ACTIVE
        ]

    @server.tool(annotations=READ_ONLY, structured_output=True)
    async def query_crosswalk(
        pack_id: str,
        ens_code: str | None = None,
        external_reference: str | None = None,
        include_inactive: bool = False,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Consulta un crosswalk por medida ENS o referencia del otro marco."""
        wanted_pack = _normalize(pack_id)
        pack = next((item for item in data_packs if item.pack_id == wanted_pack), None)
        if pack is None:
            raise ValueError(f"data pack desconocido: {pack_id!r}")
        if pack.status is not DataPackStatus.ACTIVE and not include_inactive:
            raise ValueError(
                f"data pack {pack.pack_id!r} no está activo; "
                "use include_inactive para inspeccionarlo"
            )

        _, measures = await repository.fetch_corpus()
        known_codes = {measure.code for measure in measures}
        unknown_codes = sorted(
            {
                code
                for mapping in pack.mappings
                for code in mapping.ens_measure_codes
                if code not in known_codes
            }
        )
        if unknown_codes:
            raise ValueError(
                f"data pack {pack.pack_id!r} contiene medidas ENS desconocidas: {unknown_codes}"
            )
        normalized_code = _normalize_filter_value(ens_code)
        if normalized_code is not None:
            normalized_code = _require_measure(measures, normalized_code).code
        normalized_reference = external_reference.strip().casefold() if external_reference else None
        mappings = [
            mapping
            for mapping in pack.mappings
            if (normalized_code is None or normalized_code in mapping.ens_measure_codes)
            and (
                normalized_reference is None
                or mapping.external_reference.casefold() == normalized_reference
            )
        ]
        payload = _data_pack_to_dict(pack)
        payload["mappings"] = _paginate(
            [
                {
                    "external_reference": mapping.external_reference,
                    "ens_measure_codes": list(mapping.ens_measure_codes),
                    "relation": mapping.relation.value,
                    "source_reference": mapping.source_reference,
                    "notes": mapping.notes,
                }
                for mapping in mappings
            ],
            limit,
            cursor,
        )
        return payload
