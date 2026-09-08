"""Measure lookup and base applicability MCP tools."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from ensmcp.domain.models import DimensionLevel, SecurityDimension
from ensmcp.domain.queries import (
    applicable_measures,
    filter_measures,
    search_measures_by_text,
    system_category,
)
from ensmcp.domain.repository import MeasureRepository
from ensmcp.mcp_server.annotations import READ_ONLY
from ensmcp.mcp_server.boundary import (
    _NO_SUCH_CATEGORY,
    _category_vocabulary,
    _filter_measure_codes,
    _known_code,
    _paginate,
    _parse_dimension_levels,
    _parse_optional_enum,
    _require_measure,
)
from ensmcp.mcp_server.presenters import (
    _applicable_to_dict,
    _category_to_dict,
    _measure_to_dict,
)


def register_measure_tools(server: MCPServer, repository: MeasureRepository) -> None:
    @server.tool(annotations=READ_ONLY, structured_output=True)
    async def list_categories() -> list[dict[str, str]]:
        """Lista todas las categorías del Anexo II (org, op.pl, mp.if, ...)."""
        categories, _ = await repository.fetch_corpus()
        return [_category_to_dict(category) for category in categories]

    @server.tool(annotations=READ_ONLY, structured_output=True)
    async def list_measures(
        category_code: str | None = None,
        dimension: str | None = None,
        level: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        compact: bool = False,
        include_norm_text: bool = True,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Lista medidas de seguridad con filtros opcionales.

        category_code: código de categoría o grupo, p. ej. "mp.if" o "mp".
            Uno que no sea una categoría del Anexo II es un error: todas las
            categorías tienen medidas, así que una lista vacía sólo podía
            significar que el argumento no era una categoría.
        dimension: "confidencialidad", "integridad", "disponibilidad",
            "autenticidad" o "trazabilidad".
        level: "bajo", "medio" o "alto". Se acepta "basico" por compatibilidad.
        """
        _, measures = await repository.fetch_corpus()
        filtered = filter_measures(
            measures,
            category_code=_known_code(
                category_code,
                _category_vocabulary(measures),
                "category_code",
                _NO_SUCH_CATEGORY,
            ),
            dimension=_parse_optional_enum(SecurityDimension, dimension, "dimension"),
            level=_parse_optional_enum(DimensionLevel, level, "level"),
        )
        return _paginate(
            [
                _measure_to_dict(measure, compact=compact, include_norm_text=include_norm_text)
                for measure in filtered
            ],
            limit,
            cursor,
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    async def get_measure(code: str) -> dict[str, Any]:
        """Obtiene una medida de seguridad por su código exacto, p. ej. "org.1"."""
        _, measures = await repository.fetch_corpus()
        return _measure_to_dict(_require_measure(measures, code))

    @server.tool(annotations=READ_ONLY, structured_output=True)
    async def search_measures(
        query: str,
        limit: int | None = None,
        cursor: str | None = None,
        compact: bool = False,
        include_norm_text: bool = True,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Busca medidas por texto: código, título, cuestionario, redacción del RD.

        Mira el `code`, el `title`, la `description` (el cuestionario de la
        CCN-STIC 808), el `norm_text` (lo que exige el RD 311/2022) y el `text`
        de cada refuerzo. Ignora mayúsculas y tildes.
        """
        _, measures = await repository.fetch_corpus()
        # Stripping and the "a query that means nothing matches nothing" rule
        # both live in ``search_measures_by_text``, on the folded needle: doing
        # it here, on what the caller typed, missed every query made only of
        # characters that folding removes (see that function's docstring).
        matches = search_measures_by_text(measures, query)
        return _paginate(
            [
                _measure_to_dict(measure, compact=compact, include_norm_text=include_norm_text)
                for measure in matches
            ],
            limit,
            cursor,
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    async def declaracion_aplicabilidad(
        confidencialidad: str | None = None,
        integridad: str | None = None,
        disponibilidad: str | None = None,
        autenticidad: str | None = None,
        trazabilidad: str | None = None,
        measure_codes: list[str] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        compact: bool = False,
        include_norm_text: bool = True,
    ) -> dict[str, Any]:
        """Medidas y refuerzos exigibles a un sistema, para su DdA.

        Cada dimensión toma "bajo", "medio" o "alto" — el nivel al que está
        valorada en ese sistema (Anexo I). Se acepta "basico" por compatibilidad
        y se omite si el sistema no la
        valora. Hay que valorar al menos una.

        Devuelve la categoría del sistema (el mayor de esos niveles) y, por
        cada medida exigible, el nivel al que se le exige y los refuerzos de
        ese nivel. Un refuerzo con `alternative: true` es una opción entre
        varias: basta implantar uno de los marcados así en ese nivel.
        """
        levels = _parse_dimension_levels(
            confidencialidad=confidencialidad,
            integridad=integridad,
            disponibilidad=disponibilidad,
            autenticidad=autenticidad,
            trazabilidad=trazabilidad,
        )
        _, measures = await repository.fetch_corpus()
        applicable = applicable_measures(_filter_measure_codes(measures, measure_codes), levels)
        page: list[dict[str, Any]] | dict[str, Any] = _paginate(
            [
                _applicable_to_dict(item, compact=compact, include_norm_text=include_norm_text)
                for item in applicable
            ],
            limit,
            cursor,
        )
        return {
            "categoria_sistema": system_category(levels).value,
            "measures": page,
        }
