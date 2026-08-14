"""La regla de dependencias, ejecutable.

Hasta ahora las capas sólo estaban descritas en los docstrings: "el dominio no
depende de infraestructura", "la capa MCP sólo referencia el scraping a través
del puerto". Un docstring no impide nada — un `from ensmcp.scraping...` en
`domain/queries.py` pasa black, ruff, mypy y los 196 tests sin una queja, y la
arquitectura se erosiona sin que salte nada.

Esto lee el AST de cada módulo de ``src/ensmcp`` y comprueba las cuatro reglas
que sostienen el diseño. Es un test de regresión sobre la estructura, no sobre
el comportamiento: falla en el momento en que alguien cruza una frontera, que
es cuando la corrección cuesta una línea en vez de un refactor.

Sin mocks y sin importar nada del paquete: parsea el código fuente tal como
está en disco, así que no puede verse afectado por lo que un import ejecute.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

from tests.support import check

_SOURCE_ROOT = Path(__file__).resolve().parent.parent / "src" / "ensmcp"

# El paquete de infraestructura del navegador y el framework MCP. Ninguno de
# los dos puede aparecer en el dominio: son exactamente las dependencias que la
# regla de dependencias manda apuntar hacia dentro, nunca hacia fuera.
_FRAMEWORKS = frozenset({"patchright", "mcp"})

# Los módulos que sus propios docstrings declaran puros ("no Patchright, no
# network, no filesystem"). Se comprueba lo que esa promesa significa: que no
# pueden alcanzar ni el navegador, ni el disco, ni el reloj, ni el sistema.
_PURE_MODULES = (
    "snapshot/codec.py",
    "guia/codec.py",
    "scraping/parsers.py",
    "scraping/requisitos.py",
    "scraping/norm_texts.py",
)
_IMPURE_ROOTS = frozenset(
    {
        "patchright",
        "pathlib",
        "importlib",
        "asyncio",
        "os",
        "socket",
        "subprocess",
        "urllib",
        "time",
    }
)

# El composition root: el único sitio donde se conocen a la vez las dos fuentes
# de datos, porque es el que decide que una respalda a la otra.
_COMPOSITION_ROOT = "ensmcp.__main__"


def _module_name(path: Path) -> str:
    """El nombre de módulo punteado de un fichero bajo ``src/``."""
    relative = path.relative_to(_SOURCE_ROOT.parent).with_suffix("")
    parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
    return ".".join(parts)


def _imports(path: Path) -> Iterator[str]:
    """Cada módulo que ``path`` importa, por su nombre absoluto.

    De un ``from X import y`` salen tanto ``X`` como ``X.y``: la segunda forma
    es la que hace visible un ``from ensmcp import package_data``, donde el
    módulo importado sólo aparece como nombre importado. Que alguno de esos
    ``X.y`` sea un símbolo y no un módulo da igual — las reglas comparan
    prefijos, y un símbolo vive bajo el mismo paquete que lo declara.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # El proyecto no usa imports relativos; comprobarlo aquí es lo que
            # mantiene esa regla en pie, y además es lo que deja resolver todo
            # nombre por prefijo absoluto sin reconstruir el paquete padre.
            check(node.level == 0, f"import relativo en {path}")
            module = node.module
            check(module is not None, f"import sin módulo en {path}")
            yield str(module)
            yield from (f"{module}.{alias.name}" for alias in node.names)


def _package_imports(path: Path) -> set[str]:
    """Sólo lo que ``path`` importa del propio ``ensmcp``."""
    return {name for name in _imports(path) if name == "ensmcp" or name.startswith("ensmcp.")}


def _modules_in(*relative_parts: str) -> list[Path]:
    root = _SOURCE_ROOT.joinpath(*relative_parts)
    found = sorted(root.rglob("*.py"))
    check(len(found) > 0, f"no hay módulos bajo {root}")
    return found


def test_the_domain_depends_on_nothing_but_itself() -> None:
    # La regla de dependencias: el dominio es el centro, así que no puede
    # nombrar ninguna otra capa ni ninguno de los dos frameworks.
    for path in _modules_in("domain"):
        for imported in _package_imports(path):
            check(
                imported.startswith("ensmcp.domain"),
                f"{_module_name(path)} importa {imported}: el dominio no depende de otra capa",
            )
        for imported in _imports(path):
            check(
                imported.split(".")[0] not in _FRAMEWORKS,
                f"{_module_name(path)} importa {imported}: el dominio no conoce frameworks",
            )


def test_the_mcp_layer_reaches_the_data_only_through_the_domain() -> None:
    # La capa de interfaz habla con el puerto MeasureRepository y con los
    # objetos de dominio, nunca con quien los produce: ni el scraping, ni el
    # snapshot, ni el cargador de la guía.
    for path in _modules_in("mcp_server"):
        for imported in _package_imports(path):
            check(
                imported.startswith("ensmcp.domain"),
                f"{_module_name(path)} importa {imported}: la capa MCP sólo depende del dominio",
            )


def test_the_modules_that_promise_purity_keep_it() -> None:
    for relative in _PURE_MODULES:
        path = _SOURCE_ROOT / relative
        check(path.is_file(), f"{relative} ya no existe: actualiza _PURE_MODULES")
        for imported in _imports(path):
            check(
                imported.split(".")[0] not in _IMPURE_ROOTS,
                f"{relative} importa {imported}, y su docstring promete ser puro",
            )


def test_only_the_composition_root_wires_the_live_site_to_the_snapshot() -> None:
    # Que una sola pieza conozca las dos fuentes es lo que deja servir el
    # snapshot sin navegador y comprobarlo contra la web sin que ninguna de las
    # dos sepa de la otra. Si otro módulo empieza a importar ambas, esa decisión
    # se habrá filtrado fuera del sitio que la toma.
    for path in _modules_in():
        imported = _package_imports(path)
        knows_live = any(name.startswith("ensmcp.scraping") for name in imported)
        knows_stored = any(name.startswith(("ensmcp.snapshot", "ensmcp.guia")) for name in imported)

        check(
            not (knows_live and knows_stored) or _module_name(path) == _COMPOSITION_ROOT,
            f"{_module_name(path)} conoce la web y el snapshot: eso es {_COMPOSITION_ROOT}",
        )


def test_the_browser_never_launches_with_chromes_sandbox_disabled() -> None:
    """``chromium_sandbox=True`` es una medida de seguridad, no un ajuste.

    Patchright/Playwright pasan ``--no-sandbox`` salvo que se les diga lo
    contrario, y este navegador visita páginas externas y vivas: sin el sandbox
    del renderer, un fallo de Chrome deja de estar contenido. El comentario que
    acompaña al argumento ya lo dice, pero nada lo comprobaba — ponerlo a
    ``False`` dejaba la suite entera en verde.

    Se lee el AST y no se lanza un navegador porque no hay forma de observar el
    sandbox desde dentro de la página, y el proyecto prohíbe los mocks: la
    afirmación estática es la que queda, y es del mismo género que las cuatro
    reglas de dependencias de este fichero. Vale además para el caso que más
    importa — que nadie lo desactive "temporalmente" para depurar y lo deje.
    """
    path = _SOURCE_ROOT / "scraping" / "persistent_context.py"
    launches = [
        node
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "launch_persistent_context"
    ]

    check(len(launches) == 1, f"esperaba un solo lanzamiento, hay {len(launches)}")
    sandbox = [kw for kw in launches[0].keywords if kw.arg == "chromium_sandbox"]
    check(len(sandbox) == 1, "el lanzamiento no pasa chromium_sandbox")
    check(
        isinstance(sandbox[0].value, ast.Constant) and sandbox[0].value.value is True,
        "el navegador se lanza con el sandbox de Chrome desactivado",
    )
