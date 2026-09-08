"""Read-only MCP resources."""

from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer

from ensmcp.domain.models import Guia808
from ensmcp.domain.repository import MeasureRepository
from ensmcp.mcp_server.boundary import _load_schema_catalog, _normalize, _require_measure
from ensmcp.mcp_server.contracts import StatusHandler
from ensmcp.mcp_server.presenters import (
    _article_to_dict,
    _category_to_dict,
    _evidence_to_dict,
    _measure_to_dict,
)


def register_resources(
    server: MCPServer,
    repository: MeasureRepository,
    status: StatusHandler | None,
    guia: Guia808 | None,
) -> None:
    def _resource_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    @server.resource(
        "ens://anexo-ii",
        name="anexo-ii",
        title="ENS Anexo II",
        description="Snapshot completo de categorías y medidas del Anexo II.",
        mime_type="application/json",
    )
    async def anexo_ii_resource() -> str:
        categories, measures = await repository.fetch_corpus()
        return _resource_json(
            {
                "categories": [_category_to_dict(category) for category in categories],
                "measures": [_measure_to_dict(measure) for measure in measures],
            }
        )

    @server.resource(
        "ens://schemas/v1/tools",
        name="tool-schemas",
        title="ensmcp tool schemas v1",
        description="Catálogo versionado de schemas de entrada y salida de las tools.",
        mime_type="application/schema+json",
    )
    async def tool_schemas_resource() -> str:
        return _resource_json(_load_schema_catalog())

    @server.resource(
        "ens://schemas/v1/tools/{name}",
        name="tool-schema",
        title="ensmcp tool schema v1",
        description="Schema versionado de una tool concreta.",
        mime_type="application/schema+json",
    )
    async def tool_schema_resource(name: str) -> str:
        schemas = _load_schema_catalog()["tools"]
        if name not in schemas:
            raise ValueError(f"{name!r} no es una tool publicada en el schema v1")
        return _resource_json(schemas[name])

    @server.resource(
        "ens://measures/{code}",
        name="measure",
        title="ENS measure",
        description="Una medida del Anexo II por código.",
        mime_type="application/json",
    )
    async def measure_resource(code: str) -> str:
        _, measures = await repository.fetch_corpus()
        return _resource_json(_measure_to_dict(_require_measure(measures, code)))

    @server.resource(
        "ens://categories/{code}",
        name="category",
        title="ENS category",
        description="Una categoría del Anexo II por código.",
        mime_type="application/json",
    )
    async def category_resource(code: str) -> str:
        categories, _ = await repository.fetch_corpus()
        wanted = _normalize(code)
        category = next((item for item in categories if item.code == wanted), None)
        if category is None:
            raise ValueError(f"{code!r} no es una categoría del Anexo II")
        return _resource_json(_category_to_dict(category))

    @server.resource(
        "ens://data/status",
        name="data-status",
        title="ENS data status",
        description="Origen y estado de frescura del corpus servido.",
        mime_type="application/json",
    )
    async def data_status_resource() -> str:
        payload = status() if status is not None else {"source": "repository"}
        return _resource_json(payload)

    if guia is not None:

        @server.resource(
            "ens://guide/808/articles",
            name="guide-808-articles",
            title="CCN-STIC 808 articles",
            description="Comprobaciones sobre el articulado del RD 311/2022.",
            mime_type="application/json",
        )
        async def guide_articles_resource() -> str:
            return _resource_json([_article_to_dict(article) for article in guia.articles])

        @server.resource(
            "ens://guide/808/evidence/{code}",
            name="guide-808-evidence",
            title="CCN-STIC 808 evidence",
            description="Evidencias de auditoría de una medida.",
            mime_type="application/json",
        )
        async def guide_evidence_resource(code: str) -> str:
            wanted = _normalize(code)
            items = [
                _evidence_to_dict(item)
                for item in guia.measure_evidence
                if item.measure_code == wanted
            ]
            if not items:
                raise ValueError(f"{code!r} no tiene evidencias en CCN-STIC 808")
            return _resource_json(items[0])
