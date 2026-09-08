"""Audit questionnaire and evidence MCP tools."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from ensmcp.domain.models import Guia808, SystemCategory
from ensmcp.domain.queries import applicable_measures, required_maturity_level, system_category
from ensmcp.domain.repository import MeasureRepository
from ensmcp.mcp_server.annotations import READ_ONLY
from ensmcp.mcp_server.boundary import (
    _NO_SUCH_MEASURE,
    _filter_measure_codes,
    _known_code,
    _paginate,
    _parse_dimension_levels,
    _parse_optional_enum,
)
from ensmcp.mcp_server.presenters import (
    _article_to_dict,
    _audited_to_dict,
    _evidence_to_dict,
    _maturity_to_dict,
    _requirement_to_dict,
)


def register_audit_tools(
    server: MCPServer, repository: MeasureRepository, guia: Guia808 | None
) -> None:
    @server.tool(annotations=READ_ONLY, structured_output=True)
    async def alcance_auditoria(
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
        include_questions: bool = True,
        include_evidence: bool = False,
    ) -> dict[str, Any]:
        """El temario de auditoría de un sistema: qué le van a preguntar.

        Mismos argumentos que `declaracion_aplicabilidad` — el nivel de cada
        dimensión, u omitida si el sistema no la valora.

        Devuelve sólo las medidas que le aplican y, por cada una, los requisitos
        de verificación exigibles **acumulados**: los de "Categoría Básica" se
        exigen a todas las categorías, los de "Media" a MEDIA y ALTA, y los de
        "Alta" sólo a ALTA (CCN-STIC 808 §5). Un sistema medio responde los de
        básica y los de media.

        `nivel_madurez_requerido` es el mínimo CMM que el auditor exige a cada
        medida según la categoría (CCN-STIC 808 §6), con su `code` y su
        `name`: BÁSICA → L2 "Reproducible, pero intuitivo", MEDIA → L3
        "Proceso definido", ALTA → L4 "Gestionado y medible". `essential` marca los
        requisitos cuyo incumplimiento hace que la medida entera cuente como no
        implantada.
        """
        levels = _parse_dimension_levels(
            confidencialidad=confidencialidad,
            integridad=integridad,
            disponibilidad=disponibilidad,
            autenticidad=autenticidad,
            trazabilidad=trazabilidad,
        )
        _, measures = await repository.fetch_corpus()
        category = system_category(levels)
        audited = applicable_measures(_filter_measure_codes(measures, measure_codes), levels)
        page_items = [
            _audited_to_dict(
                item,
                compact=compact,
                include_norm_text=include_norm_text,
                include_questions=include_questions,
            )
            for item in audited
        ]
        if include_evidence and guia is not None:
            evidence_by_code = {
                item.measure_code: list(item.evidence) for item in guia.measure_evidence
            }
            for item in page_items:
                item["evidence"] = evidence_by_code.get(item["code"], [])
        return {
            "categoria_sistema": category.value,
            "nivel_madurez_requerido": _maturity_to_dict(required_maturity_level(category)),
            "measures": _paginate(page_items, limit, cursor),
        }

    @server.tool(annotations=READ_ONLY, structured_output=True)
    async def requisitos_auditoria(
        code: str | None = None,
        level: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        essential_only: bool = False,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Preguntas del cuestionario de auditoría (CCN-STIC 808), en bruto.

        code: una medida concreta, p. ej. "org.1". Omitido, devuelve el
            cuestionario entero. Un código que no sea una medida del Anexo II
            es un error, no una lista vacía: hay medidas cuyo cuestionario está
            legítimamente vacío en un tramo, y las dos cosas no pueden
            contestarse igual.
        level: "basica", "media" o "alta" — las categorías oficiales del sistema.
            tools. Filtra por **la sección** en que la guía imprime el
            requisito ("Categoría Básica", "Media" y "Alta" respectivamente),
            que NO es el temario de un sistema de esa categoría: los requisitos
            son acumulativos y los de "Categoría Básica" se exigen a todas.
            Para el temario real de un sistema usa `alcance_auditoria`.

        Cada elemento trae `essential`: si uno esencial no se cumple, el auditor
        considera la medida entera como no implantada. Ojo con `code` dentro de
        una medida — es la etiqueta que imprime el sitio y se repite (hay cinco
        "1.1" distintos en op.acc.5); lo que identifica un requisito es
        `position`.
        """
        _, measures = await repository.fetch_corpus()
        measure_code = _known_code(
            code, {measure.code for measure in measures}, "code", _NO_SUCH_MEASURE
        )
        if measure_code is not None:
            measures = [measure for measure in measures if measure.code == measure_code]
        wanted = _parse_optional_enum(SystemCategory, level, "level")
        requirements = [
            _requirement_to_dict(measure.code, requirement)
            for measure in measures
            for requirement in measure.audit_requirements
            if (wanted is None or requirement.level is wanted)
            and (not essential_only or requirement.essential)
        ]
        return _paginate(requirements, limit, cursor)

    if guia is not None:

        @server.tool(annotations=READ_ONLY, structured_output=True)
        async def requisitos_articulos() -> list[dict[str, Any]]:
            """Comprobaciones de auditoría sobre el articulado del RD 311/2022.

            Una auditoría verifica el articulado además del Anexo II, y esta es
            esa mitad: las preguntas documentales y de gobierno (Declaración de
            Aplicabilidad firmada, categorización, INES, perfiles...) por las
            que suele empezar el auditor.

            `evidence` son los documentos que la guía propone que pida.
            Fuente: CCN-STIC 808 §6.1, no el ENS Navegable.
            """
            return [_article_to_dict(article) for article in guia.articles]

        @server.tool(annotations=READ_ONLY, structured_output=True)
        async def evidencias_auditoria(code: str | None = None) -> list[dict[str, Any]]:
            """Qué documentación puede pedir el auditor, por medida.

            code: una medida concreta, p. ej. "org.1". Omitido, todas. Un
                código que no sea una medida del Anexo II es un error.

            Responde a "¿qué papeles preparo?", que es el trabajo de las
            semanas previas a la auditoría. Se une por `measure_code` con lo que
            devuelven `alcance_auditoria` y `declaracion_aplicabilidad`.
            Fuente: CCN-STIC 808 §6.2, no el ENS Navegable.
            """
            wanted = _known_code(
                code,
                {item.measure_code for item in guia.measure_evidence},
                "code",
                _NO_SUCH_MEASURE,
            )
            # No sorting here: Guia808 arrives in the Anexo II's own order (see
            # its codec), which is the order every other tool serves, so this
            # only filters.
            return [
                _evidence_to_dict(item)
                for item in guia.measure_evidence
                if wanted is None or item.measure_code == wanted
            ]
