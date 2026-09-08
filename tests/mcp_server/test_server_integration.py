"""End-to-end tests: real repository, real server, real tool calls."""

from __future__ import annotations

import json
import re
from base64 import b64decode
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.types import CallToolResult

from ensmcp.data_packs import load_data_pack
from ensmcp.dda_export import export_dda
from ensmcp.dda_store import FileDDAStore
from ensmcp.guia.loader import load_packaged_guide
from ensmcp.mcp_server.server import build_server
from ensmcp.scraping.live_session import LiveSession
from ensmcp.scraping.navegable_repository import NavegableRepository
from ensmcp.snapshot.repository import RefreshingRepository, SnapshotRepository
from tests.support import (
    CHOICE_MEASURE_ROW_HTML,
    MINIMAL_ENS_NORM_JS,
    MINIMAL_REQUISITOS_JS,
    OUTER_PAGE_FILENAME,
    REINFORCED_MEASURE_ROW_HTML,
    check,
    local_repository,
    local_session,
    local_site,
    require,
    site_files,
    table_html,
)

# A two-category, two-measure table: enough shape for every filter the tools
# expose (a whole-category measure that applies at all three levels, and a
# single-dimension one that does not apply at "basico"), mirroring real rows.
_ORG_HEADER = '<tr class="fondo_oscuro"><td class="fondo_oscuro">org</td><td>Marco</td></tr>'
_ORG_1 = (
    '<tr class="cuerpo_tabla_izq"><td class="cuerpo_tabla_izq">org.1</td>'
    "<td>Política de seguridad</td><td>Categoría</td>"
    "<td>aplica</td><td>aplica</td><td>aplica</td></tr>"
)
_MP_IF_HEADER = '<tr class="sombra"><td class="sombra">mp.if</td><td>Instalaciones</td></tr>'
_MP_IF_3 = (
    '<tr class="cuerpo_tabla_izq"><td class="cuerpo_tabla_izq">mp.if.3</td>'
    "<td>Protección de las instalaciones</td><td>D</td>"
    "<td>n.a.</td><td>aplica</td><td>aplica</td></tr>"
)
# mp.s.4 rides along with no category header of its own on purpose: rows are
# recognised by their class, not by what precedes them, so this adds the one
# shape the table lacked — a measure carrying a reinforcement — without
# disturbing the two categories the list_categories test pins exactly.
FIXTURE_TABLE = table_html(
    _ORG_HEADER,
    _ORG_1,
    _MP_IF_HEADER,
    _MP_IF_3,
    REINFORCED_MEASURE_ROW_HTML,
    CHOICE_MEASURE_ROW_HTML,
)


@pytest.fixture
async def server() -> AsyncIterator[MCPServer]:
    # One warm session + one repository shared across every tool call in a
    # test: the browser starts lazily on the first call and is torn down after.
    session = LiveSession(timeout_ms=60000)
    repo = NavegableRepository(session)
    yield build_server(repo, refresh=repo.refresh)
    await session.close()


@pytest.fixture
async def local_server() -> AsyncIterator[MCPServer]:
    """A real server, real repository and real browser, over a local fixture site.

    Everything these tools do above the repository — enum coercion, boundary
    normalization, filtering, flattening to dicts — is independent of *which*
    page was scraped, so it does not need the live ENS site. Only the tests
    that assert on the real corpus keep the ``network`` marker and the
    ``server`` fixture above.
    """
    files = site_files(FIXTURE_TABLE, MINIMAL_REQUISITOS_JS, MINIMAL_ENS_NORM_JS)
    async with local_repository(files) as repo:
        yield build_server(repo, refresh=repo.refresh)


async def _call_result(server: MCPServer, name: str, arguments: dict[str, Any]) -> CallToolResult:
    result = await server.call_tool(name, arguments)
    if not isinstance(result, CallToolResult):
        raise TypeError(f"Expected a CallToolResult, got {type(result).__name__}")
    return result


def _resource_text(contents: object) -> str:
    if not isinstance(contents, list) or not contents:
        raise TypeError(f"se esperaba contenido de resource, llegó {contents!r}")
    content = contents[0]
    value = getattr(content, "content", None)
    if not isinstance(value, str):
        raise TypeError(f"se esperaba contenido textual, llegó {type(content).__name__}")
    return value


async def _call_list(
    server: MCPServer, name: str, arguments: dict[str, Any]
) -> list[dict[str, Any]]:
    result = await _call_result(server, name, arguments)
    payload: list[dict[str, Any]] = (result.structured_content or {})["result"]
    return payload


async def _call_measure(server: MCPServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await _call_result(server, name, arguments)
    payload: dict[str, Any] = result.structured_content or {}
    return payload


@pytest.mark.network
async def test_list_categories_tool_returns_real_categories(server: MCPServer) -> None:
    categories = await _call_list(server, "list_categories", {})

    check(len(categories) > 0)
    check(all(category["code"].split(".")[0] in {"org", "op", "mp"} for category in categories))
    check(all(category["group"] in {"org", "op", "mp"} for category in categories))


async def test_list_categories_tool_flattens_categories_to_dicts(
    local_server: MCPServer,
) -> None:
    # The fixture table carries one group header (org) and one subcategory
    # header (mp.if), so this covers both recognised category row shapes and
    # the code -> group derivation, without needing the real corpus.
    categories = await _call_list(local_server, "list_categories", {})

    by_code = {category["code"]: category for category in categories}
    check(set(by_code) == {"org", "mp.if"}, f"unexpected categories: {sorted(by_code)}")
    check(by_code["org"]["group"] == "org")
    check(by_code["mp.if"]["group"] == "mp")
    check(by_code["mp.if"]["name"] == "Instalaciones")


async def test_tool_payloads_keep_their_wire_contract(local_server: MCPServer) -> None:
    # These dict keys *are* the contract an MCP client reads by name, so
    # renaming or dropping one breaks every consumer silently. Asserting the
    # exact key set rather than a few keys also catches an accidental
    # *addition*, which spot-checks miss. "title" and "description" had no
    # assertion anywhere at all before this.
    measures = await _call_list(local_server, "list_measures", {})
    categories = await _call_list(local_server, "list_categories", {})

    check(
        set(measures[0])
        == {
            "code",
            "title",
            "description",
            "norm_text",
            "category_code",
            "dimensions",
            "levels",
            "reinforcements",
            "raw_levels",
        },
        f"measure payload keys changed: {sorted(measures[0])}",
    )
    check(
        set(categories[0]) == {"code", "name", "group"},
        f"category payload keys changed: {sorted(categories[0])}",
    )

    # ...and that the two unasserted ones actually carry the scraped text
    # through, rather than being present but always empty.
    org_1 = require(next((m for m in measures if m["code"] == "org.1"), None))
    check(org_1["title"] == "Política de seguridad", f"title was {org_1['title']!r}")
    check(org_1["description"] == "texto", f"description was {org_1['description']!r}")


async def test_measure_payload_carries_reinforcements_and_verbatim_level_cells(
    local_server: MCPServer,
) -> None:
    # The end-to-end proof of what a Declaración de Aplicabilidad needs: the
    # "+ R1" note in mp.s.4's Alto cell survives the DOM, the parser and the
    # wire, instead of being collapsed into a bare "applies at alto".
    measures = await _call_list(local_server, "list_measures", {})

    mp_s_4 = require(next((m for m in measures if m["code"] == "mp.s.4"), None))
    check(
        mp_s_4["reinforcements"]
        == [
            {
                "code": "R1",
                "level": "alto",
                "alternative": False,
                # Merged in from the norm asset, not the table: the table only
                # names "+ R1", it never says what R1 requires.
                "text": "R1-Protección frente a denegación de servicio.\n"
                "\u2013 [mp.s.4.r1.1] Se contratará un servicio de protección frente a DoS.",
            }
        ],
        f"reinforcements were {mp_s_4['reinforcements']}",
    )
    check(mp_s_4["raw_levels"] == {"bajo": "n.a.", "medio": "aplica", "alto": "+ R1"})

    # A measure with no note reports an empty list, not a missing key.
    org_1 = require(next((m for m in measures if m["code"] == "org.1"), None))
    check(org_1["reinforcements"] == [])
    check(org_1["raw_levels"] == {"bajo": "aplica", "medio": "aplica", "alto": "aplica"})


async def test_measure_payload_carries_what_the_rd_demands_of_the_measure(
    local_server: MCPServer,
) -> None:
    # El mismo camino que el `text` de un refuerzo, pero para la medida entera:
    # el bloque `[mp.s.4.k]` del asset de la norma llega al payload sin
    # confundirse con el bloque del refuerzo, que sale del mismo fichero.
    measures = await _call_list(local_server, "list_measures", {})

    mp_s_4 = require(next((m for m in measures if m["code"] == "mp.s.4"), None))
    check(
        mp_s_4["norm_text"] == "Se establecerán medidas preventivas frente a DoS. Para ello:\n"
        "\u2013 [mp.s.4.1] Se planificará la capacidad del sistema.",
        f"norm_text era {mp_s_4['norm_text']!r}",
    )
    check("R1-" not in mp_s_4["norm_text"], "se coló el bloque del refuerzo")
    # Y sigue siendo distinto del cuestionario de la 808, que va en description.
    check(mp_s_4["description"] != mp_s_4["norm_text"])

    # Y llega para **todas** las filas, no sólo para la que lleva refuerzo: el
    # repositorio se niega a publicar una medida cuya redacción no esté en el
    # asset, que es lo que caza un asset truncado a medio descargar.
    org_1 = require(next((m for m in measures if m["code"] == "org.1"), None))
    check(
        org_1["norm_text"] == "[org.1.1] Se aprobará una política de seguridad.",
        f"norm_text era {org_1['norm_text']!r}",
    )


async def test_measure_payload_distinguishes_a_choice_from_a_requirement(
    local_server: MCPServer,
) -> None:
    # op.acc.5 Bajo is "+ [R1 o R2 o R3 o R4]" (pick one) and Medio is
    # "+ [R2 o R3 o R4] + R5" (pick one, and R5 as well). A client reading
    # `alternative` has to be able to tell those apart from op.exp.8-style
    # "+ R1 + R2", or it will state four requirements where the ENS has one.
    measure = require(await _call_measure(local_server, "get_measure", {"code": "op.acc.5"}))

    basico = [r for r in measure["reinforcements"] if r["level"] == "bajo"]
    check(sorted(r["code"] for r in basico) == ["R1", "R2", "R3", "R4"])
    check(all(r["alternative"] for r in basico), f"basico was {basico}")

    medio = {
        r["code"]: r["alternative"] for r in measure["reinforcements"] if r["level"] == "medio"
    }
    check(medio == {"R2": True, "R3": True, "R4": True, "R5": False}, f"medio was {medio}")


async def test_list_measures_tool_filters_by_level(local_server: MCPServer) -> None:
    measures = await _call_list(local_server, "list_measures", {"level": "basico"})

    check(len(measures) > 0)
    check(all("bajo" in measure["levels"] for measure in measures))


async def test_list_measures_tool_normalizes_category_code(local_server: MCPServer) -> None:
    # category_code is normalized at the boundary: surrounding whitespace is
    # stripped and uppercase is casefolded, so "  MP  " matches the "mp" group
    # instead of silently returning [] (the pre-fix behaviour).
    measures = await _call_list(local_server, "list_measures", {"category_code": "  MP  "})

    check(len(measures) > 0)
    check(all(measure["category_code"].split(".")[0] == "mp" for measure in measures))


async def test_list_measures_tool_rejects_an_invalid_dimension_as_a_tool_error(
    local_server: MCPServer,
) -> None:
    # dimension/level are the one place user input crosses into domain enums
    # (list_measures' own docstring documents the valid values) — an unknown
    # one must surface as a clear ToolError, not an opaque crash.
    #
    # "Clear" es lo que este test decía y no comprobaba: casaba contra
    # "not a valid SecurityDimension", que es el mensaje del enum de Python.
    # Nombra una clase que no aparece en ningún esquema, docstring ni payload
    # del servidor, y no dice cuáles son los valores buenos — o sea que fijaba
    # justo el mensaje opaco del que el comentario se quejaba.
    with pytest.raises(ToolError, match="dimension='no-existe'") as raised:
        await local_server.call_tool("list_measures", {"dimension": "no-existe"})

    check("confidencialidad, integridad" in str(raised.value), str(raised.value))
    check("SecurityDimension" not in str(raised.value), str(raised.value))


async def test_list_measures_tool_rejects_an_invalid_level_as_a_tool_error(
    local_server: MCPServer,
) -> None:
    with pytest.raises(ToolError, match="level='no-existe'") as raised:
        await local_server.call_tool("list_measures", {"level": "no-existe"})

    check("bajo, medio, alto" in str(raised.value), str(raised.value))
    check("ApplicabilityLevel" not in str(raised.value), str(raised.value))


async def test_get_measure_tool_returns_a_known_foundational_measure(
    local_server: MCPServer,
) -> None:
    measure = await _call_measure(local_server, "get_measure", {"code": "org.1"})

    check(measure["code"] == "org.1")
    check(len(measure["dimensions"]) > 0)
    check(len(measure["levels"]) > 0)


async def test_get_measure_tool_rejects_an_unknown_code(local_server: MCPServer) -> None:
    with pytest.raises(ToolError, match=r"code='zz\.999'.*no es ninguna medida"):
        await local_server.call_tool("get_measure", {"code": "zz.999"})


@pytest.mark.parametrize("code", [" org.1 ", "ORG.1", " ORG.1 "])
async def test_get_measure_tool_normalizes_the_code(local_server: MCPServer, code: str) -> None:
    # A code pasted with surrounding whitespace or in upper case must resolve
    # to the same measure, not silently report "not found" (the pre-fix
    # behaviour) — the boundary normalization ``list_measures``'s category_code
    # already had (strip + casefold), which ``get_measure`` lacked.
    measure = await _call_measure(local_server, "get_measure", {"code": code})

    check(measure["code"] == "org.1")


def _system_profile() -> dict[str, Any]:
    return {
        "profile_id": "portal",
        "system": "Portal de contratación",
        "scope": "Sede electrónica",
        "dimensions": {"confidencialidad": {"level": "bajo", "justification": "valoración base"}},
        "information_assets": [
            {
                "component_id": "contracts",
                "name": "Contratos",
                "dimensions": {
                    "confidencialidad": {
                        "level": "alto",
                        "justification": "contiene ofertas reservadas",
                    }
                },
            }
        ],
        "subsystems": [
            {
                "subsystem_id": "backoffice",
                "name": "Backoffice",
                "scope": "Gestión interna",
                "inheritance": False,
                "dimensions": {"integridad": {"level": "medio", "justification": "expedientes"}},
            }
        ],
        "compliance_profiles": [
            {
                "profile_id": "sector",
                "additional_measures": ["mp.if.3"],
                "excluded_measures": ["org.1"],
            }
        ],
        "active_compliance_profile": "sector",
    }


async def test_system_profile_aggregates_assets_subsystems_and_controls(
    local_server: MCPServer,
) -> None:
    result = await _call_result(
        local_server,
        "evaluate_system_profile",
        {"profile": _system_profile()},
    )
    payload = result.structured_content or {}

    check(payload["categoria_sistema"] == "alta")
    confidentiality = payload["dimensions"]["confidencialidad"]
    check(confidentiality["level"] == "alto")
    check(len(confidentiality["evidence"]) == 2)
    check("mp.if.3" in payload["applicable_measure_codes"])
    check("org.1" not in payload["applicable_measure_codes"])
    subsystem = payload["subsystems"][0]
    check(subsystem["scope_id"] == "backoffice")
    check(subsystem["categoria_sistema"] == "media")
    check(subsystem["dimensions"]["integridad"]["level"] == "medio")


async def test_system_profile_rejects_controls_that_conflict_after_normalization(
    local_server: MCPServer,
) -> None:
    profile = _system_profile()
    profile["compliance_profiles"] = [
        {
            "profile_id": "contradictory",
            "additional_measures": ["ORG.1"],
            "excluded_measures": ["org.1"],
        }
    ]
    profile["active_compliance_profile"] = "contradictory"

    with pytest.raises(ToolError, match="añadir y excluir"):
        await local_server.call_tool("evaluate_system_profile", {"profile": profile})


async def test_explain_applicability_reports_profile_decisions_and_evidence(
    local_server: MCPServer,
) -> None:
    added = await _call_result(
        local_server,
        "explain_applicability",
        {"code": "mp.if.3", "profile": _system_profile()},
    )
    check((added.structured_content or {})["reason"]["basis"] == "profile_addition")

    excluded = await _call_result(
        local_server,
        "explain_applicability",
        {"code": "org.1", "profile": _system_profile()},
    )
    check((excluded.structured_content or {})["reason"]["basis"] == "profile_exclusion")

    profile = _system_profile()
    profile["compliance_profiles"] = []
    profile["active_compliance_profile"] = None
    explained = await _call_result(
        local_server,
        "explain_applicability",
        {"code": "op.acc.5", "profile": profile},
    )
    reason = (explained.structured_content or {})["reason"]
    check(reason["basis"] == "dimension")
    check(reason["system_level"] == "alto")
    check(reason["evidence"][-1]["justification"] == "contiene ofertas reservadas")

    unvalued = await _call_result(
        local_server,
        "explain_applicability",
        {"code": "mp.if.3", "profile": profile},
    )
    check((unvalued.structured_content or {})["reason"]["basis"] == "unvalued_dimension")

    category = await _call_result(
        local_server,
        "explain_applicability",
        {"code": "org.1", "profile": profile},
    )
    check((category.structured_content or {})["reason"]["basis"] == "category")

    with pytest.raises(ToolError, match="subsistema desconocido"):
        await local_server.call_tool(
            "explain_applicability",
            {"code": "org.1", "profile": profile, "subsystem_id": "missing"},
        )

    duplicate = _system_profile()
    duplicate["subsystems"] = [duplicate["subsystems"][0], duplicate["subsystems"][0]]
    with pytest.raises(ToolError, match="subsystem_id deben ser únicos"):
        await local_server.call_tool("evaluate_system_profile", {"profile": duplicate})


@pytest.mark.parametrize("query", ["seguridad", " seguridad "])
async def test_search_measures_tool_finds_matches_ignoring_surrounding_whitespace(
    local_server: MCPServer, query: str
) -> None:
    matches = await _call_list(local_server, "search_measures", {"query": query})

    check(len(matches) > 0)


@pytest.mark.parametrize(
    # Escapados: escritos tal cual, los tres invisibles no se ven en el fuente.
    "query",
    ["   ", "\N{SOFT HYPHEN}", "\N{ZERO WIDTH SPACE}", " \N{SOFT HYPHEN} "],
)
async def test_search_measures_tool_returns_empty_for_a_query_with_no_content(
    local_server: MCPServer, query: str
) -> None:
    # An empty/whitespace query must not collapse into a substring that matches
    # every measure ("" in x is always True) and dump the whole dataset. Ni
    # tampoco una hecha sólo de caracteres invisibles, que es lo que se colaba:
    # ``"\xad".strip()`` no está vacío, así que la guarda de la tool lo dejaba
    # pasar y el pliegue lo convertía en ``""`` justo después.
    matches = await _call_list(local_server, "search_measures", {"query": query})

    check(matches == [], f"{query!r} devolvió {len(matches)} medidas")


@pytest.mark.parametrize("level", ["basico", "BÁSICO", " Básico "])
async def test_list_measures_tool_accepts_a_level_typed_with_accents(
    local_server: MCPServer, level: str
) -> None:
    # A Spanish speaker types "Básico". Before the boundary folded accents that
    # was an unknown enum member and a ToolError, not a filter.
    measures = await _call_list(local_server, "list_measures", {"level": level})

    check(len(measures) > 0)
    check(all("bajo" in measure["levels"] for measure in measures))


async def test_search_measures_tool_finds_the_same_with_and_without_accents(
    local_server: MCPServer,
) -> None:
    with_accent = await _call_list(local_server, "search_measures", {"query": "Política"})
    without = await _call_list(local_server, "search_measures", {"query": "politica"})

    check(len(with_accent) > 0)
    check(with_accent == without, "the accented and unaccented queries disagreed")


async def test_list_measures_tool_treats_whitespace_dimension_as_no_filter(
    local_server: MCPServer,
) -> None:
    # dimension="   " must behave like dimension=None (no filter → all measures),
    # not raise a ToolError — the same as dimension="" already does.
    measures = await _call_list(local_server, "list_measures", {"dimension": "   "})

    check(len(measures) > 0)


async def test_refresh_tool_reloads_the_live_page(local_server: MCPServer) -> None:
    # Warm the session, then call refresh and assert the tool reports ok.
    # dict[str, str] returns use a RootModel, unlike list/Optional returns
    # (_call_list/_call_measure), so structured_content is the plain
    # dict itself with no "result" wrapper key.
    await _call_list(local_server, "list_measures", {})
    result = await _call_result(local_server, "refresh_live_page", {})

    payload = result.structured_content or {}
    check(payload == {"status": "ok"})


async def test_list_measures_tool_surfaces_scraping_failures_as_tool_errors() -> None:
    # A page with no iframe at all makes the scrape fail. The tool has to
    # surface that as a ToolError rather than letting the raw scraping
    # exception escape the MCP boundary. A local fixture reproduces it
    # deterministically; this used to load python.org for the same shape.
    with local_site({OUTER_PAGE_FILENAME: "<p>no iframe here</p>"}) as base_url:
        async with local_session(base_url, timeout_ms=2000) as session:
            broken = build_server(NavegableRepository(session))

            with pytest.raises(ToolError):
                await broken.call_tool("list_measures", {})


@pytest.fixture
async def snapshot_server(tmp_path: Path) -> MCPServer:
    """A server over the real shipped corpus, which is what a DdA needs.

    The fixture site's five rows cannot answer "what does this system owe":
    that question is about the whole Anexo II, its n.a. cells and its
    reinforcement notes. No browser and no network — the snapshot is a file.
    """
    return build_server(
        SnapshotRepository.from_package_data(),
        guia=load_packaged_guide(),
        dda_store=FileDDAStore(tmp_path / "dda"),
        export_handler=export_dda,
        data_packs=tuple(load_data_pack(path) for path in sorted(Path("packs").glob("*.json"))),
        clock=lambda: datetime(2026, 9, 8, tzinfo=UTC),
    )


async def test_crosswalk_tools_filter_and_preserve_lifecycle(snapshot_server: MCPServer) -> None:
    active = await _call_list(snapshot_server, "list_data_packs", {})
    check({pack["pack_id"] for pack in active} == {"iso27001", "dora"})
    all_packs = await _call_list(snapshot_server, "list_data_packs", {"include_inactive": True})
    check({pack["pack_id"] for pack in all_packs} == {"iso27001", "nis2", "dora"})
    check(next(pack for pack in all_packs if pack["pack_id"] == "nis2")["status"] == "withdrawn")

    iso = await _call_result(
        snapshot_server,
        "query_crosswalk",
        {"pack_id": "ISO27001", "ens_code": "ORG.1"},
    )
    check((iso.structured_content or {})["mappings"][0]["external_reference"] == "5.1")

    dora = await _call_result(
        snapshot_server,
        "query_crosswalk",
        {
            "pack_id": "dora",
            "external_reference": "article 10",
            "limit": 1,
            "cursor": "0",
        },
    )
    check(
        (dora.structured_content or {})["mappings"]["items"][0]["external_reference"]
        == "Article 10"
    )

    nis2 = await _call_result(
        snapshot_server,
        "query_crosswalk",
        {"pack_id": "nis2", "include_inactive": True},
    )
    check((nis2.structured_content or {})["mappings"] == [])

    with pytest.raises(ToolError, match="desconocido"):
        await snapshot_server.call_tool("query_crosswalk", {"pack_id": "missing"})
    with pytest.raises(ToolError, match="no está activo"):
        await snapshot_server.call_tool("query_crosswalk", {"pack_id": "nis2"})
    with pytest.raises(ToolError, match="ninguna medida"):
        await snapshot_server.call_tool(
            "query_crosswalk", {"pack_id": "iso27001", "ens_code": "missing"}
        )


async def test_crosswalk_rejects_unknown_measure_codes_in_a_pack() -> None:
    pack = load_data_pack(Path("packs/dora.json"))
    invalid = replace(
        pack,
        mappings=(replace(pack.mappings[0], ens_measure_codes=("unknown.1",)),),
    )
    server = build_server(SnapshotRepository.from_package_data(), data_packs=(invalid,))
    with pytest.raises(ToolError, match="medidas ENS desconocidas"):
        await server.call_tool("query_crosswalk", {"pack_id": "dora"})


async def test_persistent_dda_workflow(snapshot_server: MCPServer) -> None:
    created = await _call_result(
        snapshot_server,
        "create_dda",
        {"record_id": "portal-2026", "profile": _system_profile()},
    )
    summary = created.structured_content or {}
    check(summary["record_id"] == "portal-2026")
    check(summary["measures"] == 73)

    listed = await _call_result(snapshot_server, "list_dda", {})
    check((listed.structured_content or {})["result"][0]["record_id"] == "portal-2026")

    record = await _call_result(snapshot_server, "get_dda", {"record_id": "portal-2026"})
    payload = record.structured_content or {}
    check(payload["category"] == "alta")
    check(payload["measures"][0]["decision_basis"]["basis"] == "profile_exclusion")

    updated = await _call_result(
        snapshot_server,
        "update_dda_measure_status",
        {
            "record_id": "portal-2026",
            "code": "ORG.1",
            "implementation_status": "compensated",
            "justification": "Riesgo aceptado",
            "owner": "CISO",
            "evidence_references": ["ev-42"],
            "compensatory_measures": ["segmentación"],
            "surveillance_measures": ["revisión mensual"],
            "target_date": "2026-10-01",
            "review_date": "2027-01-01",
        },
    )
    updated_measure = (updated.structured_content or {})["measures"][0]
    check(updated_measure["implementation_status"] == "compensated")
    check(updated_measure["owner"] == "CISO")

    document = await _call_result(
        snapshot_server,
        "export_dda",
        {"record_id": "portal-2026", "output_format": "json"},
    )
    exported = document.structured_content or {}
    check(exported["encoding"] == "base64")
    check(json.loads(b64decode(exported["content"]))["record_id"] == "portal-2026")

    with pytest.raises(ToolError, match="ya existe"):
        await snapshot_server.call_tool(
            "create_dda", {"record_id": "portal-2026", "profile": _system_profile()}
        )
    with pytest.raises(ToolError, match="exclusion_reason"):
        await snapshot_server.call_tool(
            "update_dda_measure_status",
            {
                "record_id": "portal-2026",
                "code": "org.1",
                "implementation_status": "excluded",
            },
        )
    with pytest.raises(ToolError, match="compensatory_measures"):
        await snapshot_server.call_tool(
            "update_dda_measure_status",
            {
                "record_id": "portal-2026",
                "code": "org.1",
                "implementation_status": "compensated",
            },
        )
    with pytest.raises(ToolError, match="no contiene"):
        await snapshot_server.call_tool(
            "update_dda_measure_status",
            {
                "record_id": "portal-2026",
                "code": "missing",
                "implementation_status": "implemented",
            },
        )
    with pytest.raises(ToolError, match="desconocida"):
        await snapshot_server.call_tool("get_dda", {"record_id": "missing"})


async def test_dda_store_can_be_wired_without_an_exporter(tmp_path: Path) -> None:
    server = build_server(SnapshotRepository.from_package_data(), dda_store=FileDDAStore(tmp_path))
    names = {tool.name for tool in await server.list_tools()}
    check("create_dda" in names)
    check("export_dda" not in names)


async def test_server_exposes_typed_resources_and_tool_annotations(
    snapshot_server: MCPServer,
) -> None:
    resources = await snapshot_server.list_resources()
    resource_uris = {str(resource.uri) for resource in resources}
    check(
        {
            "ens://anexo-ii",
            "ens://data/status",
            "ens://guide/808/articles",
            "ens://schemas/v1/tools",
        }
        <= resource_uris,
        f"recursos registrados: {resource_uris}",
    )

    contents = await snapshot_server.read_resource("ens://measures/org.1")
    measure = json.loads(_resource_text(contents))
    check(measure["code"] == "org.1")
    for uri in (
        "ens://anexo-ii",
        "ens://categories/org",
        "ens://data/status",
        "ens://guide/808/articles",
        "ens://guide/808/evidence/org.1",
    ):
        contents = await snapshot_server.read_resource(uri)
        check(bool(_resource_text(contents)), f"resource vacío: {uri}")
    for uri in (
        "ens://measures/unknown",
        "ens://categories/unknown",
        "ens://guide/808/evidence/unknown",
    ):
        with pytest.raises(ResourceError):
            await snapshot_server.read_resource(uri)

    tools = {tool.name: tool for tool in await snapshot_server.list_tools()}
    check(all(tool.output_schema for tool in tools.values()))
    contents = await snapshot_server.read_resource("ens://schemas/v1/tools")
    catalog = json.loads(_resource_text(contents))
    check(catalog["schema_version"] == "1.0.0")
    for name, tool in tools.items():
        check(catalog["tools"][name]["inputSchema"] == tool.input_schema)
        check(catalog["tools"][name]["outputSchema"] == tool.output_schema)
        contents = await snapshot_server.read_resource(f"ens://schemas/v1/tools/{name}")
        check(json.loads(_resource_text(contents)) == catalog["tools"][name])
    with pytest.raises(ResourceError):
        await snapshot_server.read_resource("ens://schemas/v1/tools/unknown")
    search_annotations = require(tools["search_measures"].annotations)
    check(search_annotations.read_only_hint is True)
    create_annotations = require(tools["create_dda"].annotations)
    check(create_annotations.read_only_hint is False)
    check(create_annotations.destructive_hint is False)

    async def refresh() -> None:
        return None

    live_server = build_server(
        SnapshotRepository.from_package_data(),
        refresh=refresh,
        status=lambda: {"source": "snapshot"},
    )
    live_tools = {tool.name: tool for tool in await live_server.list_tools()}
    check(set(catalog["tools"]) == set(tools) | set(live_tools))
    for name, tool in live_tools.items():
        check(catalog["tools"][name]["inputSchema"] == tool.input_schema)
        check(catalog["tools"][name]["outputSchema"] == tool.output_schema)
    refresh_annotations = require(live_tools["refresh_live_page"].annotations)
    check(refresh_annotations.open_world_hint is True)
    contents = await live_server.read_resource("ens://data/status")
    check(json.loads(_resource_text(contents)) == {"source": "snapshot"})


async def test_large_responses_support_paging_and_compact_fields(
    snapshot_server: MCPServer,
) -> None:
    result = await _call_result(
        snapshot_server,
        "list_measures",
        {"limit": 2, "compact": True, "include_norm_text": False},
    )
    page = (result.structured_content or {})["result"]
    check(len(page["items"]) == 2)
    check(page["next_cursor"] == "2")
    check("description" not in page["items"][0])
    check("norm_text" not in page["items"][0])

    without_norm = await _call_result(
        snapshot_server,
        "search_measures",
        {"query": "seguridad", "limit": 1, "include_norm_text": False},
    )
    check("norm_text" not in (without_norm.structured_content or {})["result"]["items"][0])

    next_page = await _call_result(
        snapshot_server, "search_measures", {"query": "seguridad", "cursor": "1", "limit": 1}
    )
    check(len((next_page.structured_content or {})["result"]["items"]) == 1)

    implicit_limit = await _call_result(
        snapshot_server, "search_measures", {"query": "seguridad", "cursor": "0"}
    )
    check(len((implicit_limit.structured_content or {})["result"]["items"]) <= 50)
    for arguments, message in (
        ({"limit": 0}, "limit debe estar entre"),
        ({"limit": 1, "cursor": "bad"}, "cursor debe ser"),
        ({"limit": 1, "cursor": "999"}, "cursor fuera"),
    ):
        with pytest.raises(ToolError, match=message):
            await snapshot_server.call_tool("list_measures", arguments)

    declaration = await _call_result(
        snapshot_server,
        "declaracion_aplicabilidad",
        {"confidencialidad": "alto", "measure_codes": ["org.1"], "compact": True},
    )
    declaration_page = (declaration.structured_content or {})["measures"]
    check([item["code"] for item in declaration_page] == ["org.1"])
    check("reinforcements" not in declaration_page[0])

    scope = await _call_result(
        snapshot_server,
        "alcance_auditoria",
        {
            "integridad": "medio",
            "measure_codes": ["org.1"],
            "limit": 1,
            "include_questions": False,
            "include_evidence": True,
        },
    )
    scope_page = (scope.structured_content or {})["measures"]
    check(len(scope_page["items"]) == 1)
    check("audit_requirements" not in scope_page["items"][0])
    check(scope_page["items"][0]["evidence"])

    requirements = await _call_result(
        snapshot_server,
        "requisitos_auditoria",
        {"essential_only": True, "limit": 1},
    )
    requirement_page = (requirements.structured_content or {})["result"]
    check(len(requirement_page["items"]) == 1)
    check(requirement_page["items"][0]["essential"] is True)


async def test_declaracion_tool_returns_the_category_and_what_it_demands(
    snapshot_server: MCPServer,
) -> None:
    result = await _call_result(
        snapshot_server,
        "declaracion_aplicabilidad",
        {
            "confidencialidad": "alto",
            "integridad": "medio",
            "disponibilidad": "basico",
            "autenticidad": "medio",
            "trazabilidad": "medio",
        },
    )
    payload = result.structured_content or {}

    check(payload["categoria_sistema"] == "alta", f"categoría fue {payload['categoria_sistema']}")
    measures = payload["measures"]
    check(len(measures) > 0)
    # Every line keeps the same measure shape the other tools return, plus the
    # two fields that make it a DdA line.
    first = measures[0]
    check({"code", "title", "reinforcements", "raw_levels"} <= set(first))
    check({"required_level", "required_reinforcements"} <= set(first))

    op_acc_5 = require(next((m for m in measures if m["code"] == "op.acc.5"), None))
    check(op_acc_5["required_level"] == "alto")
    chosen = {r["code"]: r["alternative"] for r in op_acc_5["required_reinforcements"]}
    check(chosen == {"R2": True, "R3": True, "R4": True, "R5": False}, f"refuerzos: {chosen}")
    check(all(r["text"] for r in op_acc_5["required_reinforcements"]), "un refuerzo sin redacción")


async def test_declaracion_tool_orders_a_reinforcement_named_twice_in_one_cell() -> None:
    # Una celda puede nombrar el mismo refuerzo dentro y fuera de los corchetes,
    # y entonces la medida lleva el par (R1 elección) y (R1 obligatorio) en el
    # mismo nivel. Salen de un frozenset, cuyo orden de iteración es arbitrario,
    # así que ordenar sólo por el código no los separa y el payload podía
    # alternarlos entre ejecuciones. ``_measure_to_dict`` y ``snapshot.codec``
    # ya cerraban la clave con ``alternative``; ésta era la que no.
    row = (
        '<tr class="cuerpo_tabla_izq"><td class="cuerpo_tabla_izq">mp.s.4</td>'
        "<td>Protección frente a denegación de servicio</td><td>D</td>"
        "<td>n.a.</td><td>n.a.</td><td>+ [R1 o R2] + R1</td></tr>"
    )
    files = site_files(table_html(row), MINIMAL_REQUISITOS_JS, MINIMAL_ENS_NORM_JS)
    async with local_repository(files) as repo:
        result = await _call_result(
            build_server(repo), "declaracion_aplicabilidad", {"disponibilidad": "alto"}
        )
        measures = (result.structured_content or {})["measures"]

        pairs = [(r["code"], r["alternative"]) for r in measures[0]["required_reinforcements"]]
        check(pairs == [("R1", False), ("R1", True), ("R2", True)], f"orden: {pairs}")


async def test_declaracion_tool_omits_what_a_lower_level_does_not_demand(
    snapshot_server: MCPServer,
) -> None:
    # mp.info.4 is n.a. below alto, so a system with trazabilidad at medio does
    # not owe it — the whole point of asking per dimension.
    async def codes(trazabilidad: str) -> set[str]:
        result = await _call_result(
            snapshot_server, "declaracion_aplicabilidad", {"trazabilidad": trazabilidad}
        )
        return {m["code"] for m in (result.structured_content or {})["measures"]}

    check("mp.info.4" not in await codes("medio"))
    check("mp.info.4" in await codes("alto"))


async def test_declaracion_tool_rejects_a_system_with_nothing_valued(
    snapshot_server: MCPServer,
) -> None:
    # No dimension given is a caller mistake; an empty list would read as a
    # valid DdA that demands nothing at all.
    with pytest.raises(ToolError, match="al menos una dimensión"):
        await snapshot_server.call_tool("declaracion_aplicabilidad", {})


async def test_declaracion_tool_normalizes_its_levels(snapshot_server: MCPServer) -> None:
    accented = await _call_result(
        snapshot_server, "declaracion_aplicabilidad", {"confidencialidad": " Básico "}
    )
    plain = await _call_result(
        snapshot_server, "declaracion_aplicabilidad", {"confidencialidad": "basico"}
    )

    check((accented.structured_content or {}) == (plain.structured_content or {}))


async def test_scope_tool_returns_the_audit_syllabus_of_a_system(
    snapshot_server: MCPServer,
) -> None:
    result = await _call_result(
        snapshot_server,
        "alcance_auditoria",
        {
            "confidencialidad": "alto",
            "integridad": "medio",
            "disponibilidad": "basico",
            "autenticidad": "medio",
            "trazabilidad": "medio",
        },
    )
    payload = result.structured_content or {}

    check(payload["categoria_sistema"] == "alta")
    # El nivel viaja con su nombre, como cualquier otro código de este payload:
    # un refuerzo lleva su `text`, un requisito su `question`. Un "L4" pelado
    # sólo le sirve a quien ya tiene la guía abierta.
    check(
        payload["nivel_madurez_requerido"] == {"code": "L4", "name": "Gestionado y medible"},
        f"CCN-STIC 808 §6: ALTA exige L4, llegó {payload['nivel_madurez_requerido']}",
    )
    measures = payload["measures"]
    check(len(measures) == 66, f"esperaba 66 medidas, hay {len(measures)}")
    questions = [q for m in measures for q in m["audit_requirements"]]
    check(len(questions) == 382, f"esperaba 382 preguntas, hay {len(questions)}")
    check(sum(1 for q in questions if q["essential"]) == 136)


async def test_scope_tool_accumulates_the_lower_tiers(snapshot_server: MCPServer) -> None:
    # Un sistema medio recibe las preguntas de básica Y las de media, que es la
    # regla del §5 de la 808 y la razón de ser de esta tool.
    result = await _call_result(snapshot_server, "alcance_auditoria", {"integridad": "medio"})
    measures = (result.structured_content or {})["measures"]

    op_pl_1 = require(next((m for m in measures if m["code"] == "op.pl.1"), None))
    check(op_pl_1["required_level"] == "medio")
    levels = {q["level"] for q in op_pl_1["audit_requirements"]}
    check(levels == {"basica", "media"}, f"niveles del temario: {levels}")
    check(len(op_pl_1["audit_requirements"]) == 12)


async def test_scope_tool_rejects_a_system_with_nothing_valued(
    snapshot_server: MCPServer,
) -> None:
    with pytest.raises(ToolError, match="al menos una dimensión"):
        await snapshot_server.call_tool("alcance_auditoria", {})


async def test_the_declaration_tool_did_not_grow_the_questionnaire(
    snapshot_server: MCPServer,
) -> None:
    # La DdA responde "qué implantar" y debe seguir siendo ligera: las 382
    # preguntas viven en alcance_auditoria, no arrastradas en cada línea.
    result = await _call_result(
        snapshot_server, "declaracion_aplicabilidad", {"confidencialidad": "alto"}
    )
    measures = (result.structured_content or {})["measures"]

    check(all("audit_requirements" not in m for m in measures))


async def test_audit_tool_returns_the_whole_questionnaire(snapshot_server: MCPServer) -> None:
    requirements = await _call_list(snapshot_server, "requisitos_auditoria", {})

    check(len(requirements) == 430, f"esperaba 430 preguntas, hay {len(requirements)}")
    check(sum(1 for r in requirements if r["essential"]) == 136)
    check(
        set(requirements[0])
        == {"measure_code", "position", "code", "level", "essential", "question", "note"},
        f"claves del payload: {sorted(requirements[0])}",
    )


async def test_audit_tool_narrows_to_one_measure(snapshot_server: MCPServer) -> None:
    requirements = await _call_list(snapshot_server, "requisitos_auditoria", {"code": "ORG.1 "})

    check(len(requirements) > 0)
    check(all(r["measure_code"] == "org.1" for r in requirements), "el filtro por medida falló")
    first = requirements[0]
    check(first["code"] == "1.1" and first["position"] == 0)
    check(first["essential"] is True)
    with_note = require(next((r for r in requirements if r["note"]), None))
    check(with_note["note"].startswith("NOTA: En algunos organismos"))


async def test_audit_tool_keeps_the_repeated_printed_codes(snapshot_server: MCPServer) -> None:
    # The wire must not deduplicate what the questionnaire repeats: op.acc.5
    # asks five different questions all printed as "1.1".
    requirements = await _call_list(snapshot_server, "requisitos_auditoria", {"code": "op.acc.5"})

    check(len(requirements) == 14, f"esperaba 14, hay {len(requirements)}")
    check(sum(1 for r in requirements if r["code"] == "1.1") == 5)
    check(len({r["position"] for r in requirements}) == 14)


async def test_audit_tool_filters_by_level(snapshot_server: MCPServer) -> None:
    alto = await _call_list(snapshot_server, "requisitos_auditoria", {"level": "alto"})
    everything = await _call_list(snapshot_server, "requisitos_auditoria", {})

    check(len(alto) > 0)
    check(all(r["level"] == "alta" for r in alto))
    check(len(alto) < len(everything), "el filtro por nivel no descartó nada")


async def test_list_measures_refuses_a_category_that_does_not_exist(
    snapshot_server: MCPServer,
) -> None:
    """Y aquí la lista vacía nunca fue una respuesta de verdad.

    Las 18 categorías del Anexo II tienen medidas, todas, así que un `[]` del
    filtro de categoría **a solas** sólo podía significar que el argumento no
    era una categoría. Se contestaba igual que si lo fuera y estuviera vacía.

    Combinado con otro filtro sí puede vaciarse de verdad —`op.cont` sólo
    protege disponibilidad— y eso tiene que seguir devolviendo `[]`, que es lo
    que separa validar el vocabulario de exigir resultados.
    """
    with pytest.raises(ToolError, match="no es ninguna categoría"):
        await snapshot_server.call_tool("list_measures", {"category_code": "op.seg"})

    real_but_empty = await _call_list(
        snapshot_server,
        "list_measures",
        {"category_code": "op.cont", "dimension": "confidencialidad"},
    )
    check(real_but_empty == [], f"op.cont + confidencialidad -> {len(real_but_empty)}")
    check(len(await _call_list(snapshot_server, "list_measures", {"category_code": "op.cont"})) > 0)


async def test_the_category_vocabulary_is_exactly_what_list_categories_serves(
    snapshot_server: MCPServer,
) -> None:
    # El vocabulario sale de las medidas, no de las filas de cabecera. Sobre el
    # corpus real tiene que coincidir con las 18 que sirve `list_categories`, y
    # cada una tiene que seguir aceptándose como filtro.
    categories = await _call_list(snapshot_server, "list_categories", {})

    for category in categories:
        found = await _call_list(
            snapshot_server, "list_measures", {"category_code": category["code"]}
        )
        check(len(found) > 0, f"la categoría {category['code']} quedó sin medidas")


async def test_audit_tool_refuses_a_code_that_is_not_a_measure(
    snapshot_server: MCPServer,
) -> None:
    """Una lista vacía no vale, porque también es una respuesta de verdad.

    Este test afirmaba lo contrario —que un código inexistente devuelve ``[]``—
    y eso hace indistinguibles dos cosas que no lo son: la 808 no escribe ni una
    pregunta para ``mp.com.2`` por debajo de nivel alto, así que
    ``requisitos_auditoria(code="mp.com.2", level="basico")`` está legítimamente
    vacío. Los mismos bytes en el cable para una errata y para un hecho.

    Y se lee como el hecho. ``op.acc`` llega hasta 6, así que a quien preguntaba
    por "op.acc.9" se le contestaba, en efecto, que el ENS no define requisitos
    de auditoría para esa medida: una afirmación falsa sobre un Real Decreto,
    dicha por una herramienta de cumplimiento.
    """
    with pytest.raises(ToolError, match="no es ninguna medida"):
        await snapshot_server.call_tool("requisitos_auditoria", {"code": "op.acc.9"})

    # Y la lista vacía sigue significando lo que significa: esta medida no tiene
    # nada que preguntar en ese tramo.
    empty = await _call_list(
        snapshot_server, "requisitos_auditoria", {"code": "mp.com.2", "level": "basico"}
    )
    check(empty == [], f"mp.com.2 en básico -> {empty}")


async def test_article_tool_returns_the_checks_over_the_rd_articles(
    snapshot_server: MCPServer,
) -> None:
    articles = await _call_list(snapshot_server, "requisitos_articulos", {})

    check(len(articles) == 6, f"esperaba 6 artículos, hay {len(articles)}")
    art_28 = require(next((a for a in articles if a["reference"] == "Art. 28"), None))
    check(art_28["title"] == "Declaración de aplicabilidad")
    check(len(art_28["questions"]) == 5)
    check("Declaración de Aplicabilidad." in art_28["evidence"])
    check(set(art_28["questions"][0]) == {"reference", "question"})


async def test_evidence_tool_says_what_documents_to_prepare(
    snapshot_server: MCPServer,
) -> None:
    everything = await _call_list(snapshot_server, "evidencias_auditoria", {})
    org_1 = await _call_list(snapshot_server, "evidencias_auditoria", {"code": "ORG.1 "})

    check(len(everything) == 73)
    check(sum(len(item["evidence"]) for item in everything) == 365)
    check(len(org_1) == 1 and org_1[0]["measure_code"] == "org.1")
    check(org_1[0]["evidence"][0].startswith("Documento formal conteniendo la política"))


async def test_evidence_tool_refuses_a_code_that_is_not_a_measure(
    snapshot_server: MCPServer,
) -> None:
    # La mitad simétrica: la guía cubre las 73 medidas, así que aquí un código
    # que no casa es siempre una errata, nunca "esta medida no tiene papeles".
    with pytest.raises(ToolError, match="no es ninguna medida"):
        await snapshot_server.call_tool("evidencias_auditoria", {"code": "op.acc.9"})


async def test_a_server_without_the_guide_omits_its_tools_and_still_works() -> None:
    # La guía es opcional: sin ella el servidor sigue respondiendo todo lo que
    # publica el ENS Navegable.
    server = build_server(SnapshotRepository.from_package_data())
    names = {tool.name for tool in await server.list_tools()}

    check("requisitos_articulos" not in names)
    check("evidencias_auditoria" not in names)
    check(len(await _call_list(server, "list_measures", {})) == 73)


async def test_snapshot_status_tool_reports_the_repositorys_own_freshness() -> None:
    # Both sides are the real shipped snapshot, so the comparison is real code
    # end to end and has to come out "unchanged" — no stand-in status handler.
    repository = RefreshingRepository(
        SnapshotRepository.from_package_data(), SnapshotRepository.from_package_data()
    )
    server = build_server(repository, refresh=repository.refresh, status=repository.status_payload)

    before = (await _call_result(server, "snapshot_status", {})).structured_content or {}
    check(before["live_check"] == "pending", f"before was {before}")
    check(before["measures"] == 73)

    await _call_result(server, "refresh_live_page", {})
    after = (await _call_result(server, "snapshot_status", {})).structured_content or {}

    check(after["live_check"] == "unchanged", f"after was {after}")
    check(after["captured_at"] == before["captured_at"])


async def test_build_server_without_refresh_exposes_no_refresh_tool() -> None:
    # No refresh handler wired and no tool invoked → no network, no browser.
    server = build_server(NavegableRepository(LiveSession()))
    tools = await server.list_tools()

    names = {tool.name for tool in tools}
    check("refresh" not in names)
    check(
        names
        == {
            "list_categories",
            "list_measures",
            "get_measure",
            "search_measures",
            "declaracion_aplicabilidad",
            "evaluate_system_profile",
            "explain_applicability",
            "alcance_auditoria",
            "requisitos_auditoria",
            "list_data_packs",
            "query_crosswalk",
        }
    )
    check("snapshot_status" not in names)


async def test_snapshot_status_names_the_edition_of_the_guide_it_is_serving() -> None:
    """La atribución de la CCN-STIC 808 tiene que salir del proceso.

    `parse_source` la lee de la portada porque "si la edición no viaja con el
    dato no viaja en ninguna parte — nadie puede saber contra qué versión de la
    guía está auditando", y `guia.codec` dedica veinte líneas a por qué eso
    decide si el dato vale: la serie 800 sigue circulando en ediciones escritas
    para el RD 3/2010, que el RD 311/2022 derogó, y descarta cuatro guías por
    eso.

    Y aun así se quedaba dentro: se extraía, se validaba, se empaquetaba y
    ninguna tool la ponía en el cable. `requisitos_articulos` y
    `evidencias_auditoria` dicen "Fuente: CCN-STIC 808" sin decir cuál.
    """
    repository = RefreshingRepository(
        SnapshotRepository.from_package_data(), SnapshotRepository.from_package_data()
    )
    guia = load_packaged_guide()
    server = build_server(repository, status=repository.status_payload, guia=guia)

    payload = (await _call_result(server, "snapshot_status", {})).structured_content or {}

    check(
        payload["guia_808"] == guia.source, f"la atribución servida fue {payload.get('guia_808')!r}"
    )
    check("CCN-STIC-808" in str(payload["guia_808"]), f"no nombra la guía: {payload['guia_808']!r}")
    # Lo que de verdad falta sin esto: la edición.
    check(
        re.search(r"edición de \w+ 20\d{2}", str(payload["guia_808"])) is not None,
        f"no nombra la edición: {payload['guia_808']!r}",
    )


async def test_snapshot_status_says_nothing_about_a_guide_that_is_not_loaded() -> None:
    # La clave es nueva y opcional, como la propia guía: un servidor sin ella
    # sigue contestando exactamente lo que contestaba.
    repository = RefreshingRepository(
        SnapshotRepository.from_package_data(), SnapshotRepository.from_package_data()
    )
    server = build_server(repository, status=repository.status_payload)

    payload = (await _call_result(server, "snapshot_status", {})).structured_content or {}

    check("guia_808" not in payload, f"inventó una atribución: {payload}")
