"""MCP server exposing ENS domain queries as tools.

This is the only place domain objects get flattened into plain dicts for
the wire — the domain layer stays free of any MCP/JSON concern, and the
scraping layer is only referenced through the MeasureRepository port.
"""

from __future__ import annotations

import json
from base64 import b64encode
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ensmcp.domain.crosswalk import DataPack, DataPackStatus
from ensmcp.domain.dda import (
    DDAMeasureUpdate,
    DDARecord,
    DDAStore,
    ExportedDocument,
    ExportFormat,
    ImplementationStatus,
    create_dda_record,
    dda_summary,
    dda_to_dict,
    update_dda_measure,
)
from ensmcp.domain.models import (
    DimensionLevel,
    Guia808,
    SecurityDimension,
    SystemCategory,
)
from ensmcp.domain.profiles import (
    SystemProfile,
    evaluate_profile_scope,
    explain_profile_measure,
    resolve_profile_scope,
)
from ensmcp.domain.queries import (
    applicable_measures,
    filter_measures,
    required_maturity_level,
    search_measures_by_text,
    system_category,
)
from ensmcp.domain.repository import MeasureRepository
from ensmcp.mcp_server.boundary import (
    _NO_SUCH_CATEGORY,
    _NO_SUCH_MEASURE,
    _category_vocabulary,
    _filter_measure_codes,
    _known_code,
    _load_schema_catalog,
    _normalize,
    _normalize_filter_value,
    _paginate,
    _parse_dimension_levels,
    _parse_optional_enum,
    _profile_controls,
    _require_measure,
)
from ensmcp.mcp_server.presenters import (
    _applicable_to_dict,
    _article_to_dict,
    _audited_to_dict,
    _category_to_dict,
    _data_pack_to_dict,
    _evidence_to_dict,
    _maturity_to_dict,
    _measure_to_dict,
    _profile_measure_to_dict,
    _profile_scope_to_dict,
    _requirement_to_dict,
)

RefreshHandler = Callable[[], Awaitable[None]]
# Returns whatever the data source wants to report about its own freshness. The
# server forwards it untouched, so nothing here has to know that a snapshot
# exists — the same reason ``refresh`` is a callable and not a repository method.
StatusHandler = Callable[[], dict[str, object]]
ExportHandler = Callable[[DDARecord, ExportFormat], ExportedDocument]
Clock = Callable[[], datetime]

_READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
_EXTERNAL_READ = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)
_WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)
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
    """Build the MCP server, wiring each tool to ``repository``.

    Each query re-fetches from ``repository`` rather than caching here: it is
    ``NavegableRepository`` that re-scrapes the live DOM on every call (see
    its own docstring), so a query made right after ``refresh_live_page``
    always reflects the reloaded page.

    ``refresh`` and ``status`` are infrastructure (reloading the live page,
    reporting how fresh the served data is), not domain queries, so they stay
    here in the server wiring rather than on the ``MeasureRepository`` port.
    Each one supplied exposes its tool; each one left ``None`` (e.g. a plain
    fixture server) omits it. ``guia`` is the same idea for the CCN-STIC 808
    data, which comes from the guide rather than from the site: without it the
    server still answers everything the ENS Navegable publishes.
    """
    server: MCPServer = MCPServer(
        name="ensmcp",
        instructions="Consulta las medidas de seguridad del ENS Navegable (CCN-CERT).",
    )

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

    @server.tool(annotations=_READ_ONLY, structured_output=True)
    async def list_categories() -> list[dict[str, str]]:
        """Lista todas las categorías del Anexo II (org, op.pl, mp.if, ...)."""
        categories, _ = await repository.fetch_corpus()
        return [_category_to_dict(category) for category in categories]

    @server.tool(annotations=_READ_ONLY, structured_output=True)
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

    @server.tool(annotations=_READ_ONLY, structured_output=True)
    async def get_measure(code: str) -> dict[str, Any]:
        """Obtiene una medida de seguridad por su código exacto, p. ej. "org.1"."""
        _, measures = await repository.fetch_corpus()
        return _measure_to_dict(_require_measure(measures, code))

    @server.tool(annotations=_READ_ONLY, structured_output=True)
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

    @server.tool(annotations=_READ_ONLY, structured_output=True)
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

    @server.tool(annotations=_READ_ONLY, structured_output=True)
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

    @server.tool(annotations=_READ_ONLY, structured_output=True)
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

    @server.tool(annotations=_READ_ONLY, structured_output=True)
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

    @server.tool(annotations=_READ_ONLY, structured_output=True)
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

    @server.tool(annotations=_READ_ONLY, structured_output=True)
    async def list_data_packs(include_inactive: bool = False) -> list[dict[str, Any]]:
        """Lista los crosswalks externos, su procedencia, cobertura y vigencia."""
        return [
            _data_pack_to_dict(pack)
            for pack in data_packs
            if include_inactive or pack.status is DataPackStatus.ACTIVE
        ]

    @server.tool(annotations=_READ_ONLY, structured_output=True)
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

    if dda_store is not None:

        @server.tool(annotations=_WRITE, structured_output=True)
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

        @server.tool(annotations=_READ_ONLY, structured_output=True)
        async def list_dda() -> list[dict[str, object]]:
            """Lista las DdA persistidas y el recuento de sus estados."""
            return [dict(dda_summary(dda_store.load(item))) for item in dda_store.list_ids()]

        @server.tool(annotations=_READ_ONLY, structured_output=True)
        async def get_dda(record_id: str) -> dict[str, object]:
            """Obtiene una DdA persistida con todas sus medidas y evidencias."""
            return dda_to_dict(dda_store.load(record_id))

        @server.tool(annotations=_WRITE, structured_output=True)
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

            @server.tool(annotations=_READ_ONLY, structured_output=True)
            async def export_dda(record_id: str, output_format: ExportFormat) -> dict[str, str]:
                """Exporta una DdA como JSON, CSV, Markdown, XLSX, ODS o DOCX en base64."""
                document = export_handler(dda_store.load(record_id), output_format)
                return {
                    "filename": document.filename,
                    "mime_type": document.mime_type,
                    "encoding": "base64",
                    "content": b64encode(document.content).decode("ascii"),
                }

    if guia is not None:

        @server.tool(annotations=_READ_ONLY, structured_output=True)
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

        @server.tool(annotations=_READ_ONLY, structured_output=True)
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

    if refresh is not None:

        @server.tool(annotations=_EXTERNAL_READ, structured_output=True)
        async def refresh_live_page() -> dict[str, str]:
            """Comprueba ahora la página live de ENS Navegable y actualiza si cambió."""
            await refresh()
            return {"status": "ok"}

    if status is not None:

        @server.tool(annotations=_READ_ONLY, structured_output=True)
        async def snapshot_status() -> dict[str, object]:
            """Origen y frescura de los datos servidos.

            `captured_at`: cuándo se capturó lo que se está sirviendo — la fecha
                del snapshot, o el momento de la comprobación si la web difería
                y ya se sirve lo suyo (`live_check: "updated"`).
            `live_check`: "pending" (aún sin comprobar), "unchanged" (la web
                coincide), "updated" (la web difería y se sirve ya lo nuevo) o
                "unavailable" (no se pudo abrir la web: sin Chrome, sin display
                o sin red — se sigue sirviendo el snapshot).
            `guia_808`: de qué edición de la CCN-STIC 808 salieron
                `requisitos_articulos` y `evidencias_auditoria`. Sólo aparece si
                el servidor lleva la guía cargada.
            """
            payload = status()
            # La atribución de la guía se extrae de su portada, viaja en el
            # fichero y hasta aquí no salía del proceso: ninguna tool la ponía en
            # el cable. Y la edición es justo lo que decide si el dato vale — la
            # serie 800 sigue circulando en ediciones escritas para el RD 3/2010,
            # que el RD 311/2022 derogó, y ``guia.codec`` descarta cuatro por eso.
            # Sin esto, quien audita no puede saber contra qué versión lo hace,
            # que es literalmente lo que ``parse_source`` dice que este campo
            # existe para impedir. Va aquí, donde ya se contesta el origen y la
            # frescura del otro corpus, y como clave nueva: nada de lo que ya se
            # servía cambia de forma.
            if guia is not None:
                payload["guia_808"] = guia.source
            return payload

    return server
