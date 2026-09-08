"""Persistent Declaration of Applicability MCP tools."""

from __future__ import annotations

from base64 import b64encode
from datetime import date

from mcp.server.mcpserver import MCPServer

from ensmcp.application.dda import dda_summary, dda_to_dict
from ensmcp.domain.dda import (
    DDAMeasureUpdate,
    DDAStore,
    ExportFormat,
    ImplementationStatus,
    create_dda_record,
    update_dda_measure,
)
from ensmcp.domain.profiles import SystemProfile
from ensmcp.domain.repository import MeasureRepository
from ensmcp.mcp_server.annotations import READ_ONLY, WRITE
from ensmcp.mcp_server.boundary import _normalize, _profile_controls
from ensmcp.mcp_server.contracts import Clock, ExportHandler


def register_dda_tools(
    server: MCPServer,
    repository: MeasureRepository,
    dda_store: DDAStore | None,
    export_handler: ExportHandler | None,
    clock: Clock,
) -> None:
    if dda_store is not None:

        @server.tool(annotations=WRITE, structured_output=True)
        async def create_dda(
            record_id: str,
            profile: SystemProfile,
            subsystem_id: str | None = None,
        ) -> dict[str, object]:
            """Crea y persiste una DdA completa para un sistema o subsistema."""
            _, measures = await repository.fetch_corpus()
            record = create_dda_record(
                record_id,
                profile,
                measures,
                controls=_profile_controls(profile, measures),
                subsystem_id=subsystem_id,
                now=clock(),
            )
            dda_store.create(record)
            return dict(dda_summary(record))

        @server.tool(annotations=READ_ONLY, structured_output=True)
        async def list_dda() -> list[dict[str, object]]:
            """Lista las DdA persistidas y el recuento de sus estados."""
            return [dict(dda_summary(dda_store.load(item))) for item in dda_store.list_ids()]

        @server.tool(annotations=READ_ONLY, structured_output=True)
        async def get_dda(record_id: str) -> dict[str, object]:
            """Obtiene una DdA persistida con todas sus medidas y evidencias."""
            return dda_to_dict(dda_store.load(record_id))

        @server.tool(annotations=WRITE, structured_output=True)
        async def update_dda_measure_status(
            record_id: str,
            code: str,
            implementation_status: ImplementationStatus,
            justification: str = "",
            owner: str = "",
            evidence_references: list[str] | None = None,
            exclusion_reason: str = "",
            compensatory_measures: list[str] | None = None,
            surveillance_measures: list[str] | None = None,
            target_date: date | None = None,
            review_date: date | None = None,
        ) -> dict[str, object]:
            """Actualiza estado, responsable, evidencias, excepciones y fechas de una medida."""
            record = dda_store.load(record_id)
            updated = update_dda_measure(
                record,
                _normalize(code),
                DDAMeasureUpdate(
                    implementation_status=implementation_status,
                    justification=justification,
                    owner=owner,
                    evidence_references=tuple(evidence_references or ()),
                    exclusion_reason=exclusion_reason,
                    compensatory_measures=tuple(compensatory_measures or ()),
                    surveillance_measures=tuple(surveillance_measures or ()),
                    target_date=target_date,
                    review_date=review_date,
                ),
                clock(),
            )
            dda_store.save(updated)
            return dda_to_dict(updated)

        if export_handler is not None:

            @server.tool(annotations=READ_ONLY, structured_output=True)
            async def export_dda(record_id: str, output_format: ExportFormat) -> dict[str, str]:
                """Exporta una DdA como JSON, CSV, Markdown, XLSX, ODS o DOCX en base64."""
                document = export_handler(dda_store.load(record_id), output_format)
                return {
                    "filename": document.filename,
                    "mime_type": document.mime_type,
                    "encoding": "base64",
                    "content": b64encode(document.content).decode("ascii"),
                }
