"""Lo que se publica, no lo que se ejecuta desde el árbol de fuentes.

Todos los demás tests corren contra el repositorio —CI instala con
``pip install -e .``— así que nada de lo que distingue a un paquete *instalado*
se estaba comprobando: ni que los dos corpus viajen dentro del wheel, ni que el
rango de dependencias declarado sea instalable.

Ese segundo hueco escondía un fallo real. ``pyproject`` pedía ``mcp>=1.9.0`` y
``mcp.server.mcpserver`` —el módulo que importa ``mcp_server/server.py``— no
existe en ninguna versión de la serie 1.x: se comprobaron 1.9.0, 1.25.0, 1.28.0
y 1.29.0, la última, y en las cuatro el import falla. O sea que 21 versiones
satisfacían el requisito y ninguna arrancaba. CI instala desde ``pylock.toml``,
que fija 2.0.0, así que el extremo bajo del rango no se ejercitaba nunca.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

from tests.support import check, require

_ROOT = Path(__file__).resolve().parent.parent
_PACKAGE = _ROOT / "src" / "ensmcp"


def _runtime_requirements() -> list[Requirement]:
    with (_ROOT / "pyproject.toml").open("rb") as handle:
        return [Requirement(raw) for raw in tomllib.load(handle)["project"]["dependencies"]]


def _locked_versions() -> dict[str, Version]:
    with (_ROOT / "pylock.toml").open("rb") as handle:
        locked = tomllib.load(handle)["packages"]
    return {
        package["name"]: Version(package["version"]) for package in locked if "version" in package
    }


def _declared_floor(requirement: Requirement) -> Version:
    floors = [
        Version(spec.version) for spec in requirement.specifier if spec.operator in (">=", "==")
    ]
    check(len(floors) == 1, f"{requirement.name} no declara un mínimo único: {requirement}")
    return floors[0]


def test_no_dependency_claims_a_version_that_is_never_installed() -> None:
    """El mínimo declarado no puede ser menor que el que se bloquea.

    Es la regla que convierte el fallo de arriba en imposible: lo único que se
    instala y se prueba es lo que fija ``pylock.toml``, así que declarar un
    mínimo por debajo es prometer soporte para versiones que nadie ejecuta —y en
    el caso de ``mcp`` era, además, falso.

    Si un día el lock sube, este test cae. Eso es lo que se busca: volver a
    declarar hasta dónde se soporta es una decisión, no un efecto secundario de
    regenerar el lock.
    """
    locked = _locked_versions()

    for requirement in _runtime_requirements():
        pinned = require(locked.get(requirement.name), f"{requirement.name} no está en pylock.toml")
        floor = _declared_floor(requirement)
        check(
            floor >= pinned,
            f"{requirement.name} declara >={floor} pero sólo se instala y prueba {pinned}: "
            "o se sube el mínimo, o se comprueba a mano que esa versión arranca",
        )


def test_the_locked_version_satisfies_what_pyproject_declares() -> None:
    # La otra mitad, por si el mínimo se subiera por encima de lo que hay
    # bloqueado: el lock tiene que seguir siendo instalable según el rango.
    locked = _locked_versions()

    for requirement in _runtime_requirements():
        pinned = locked[requirement.name]
        check(
            requirement.specifier.contains(pinned),
            f"pylock fija {requirement.name}=={pinned}, que no cumple {requirement}",
        )


def test_both_corpora_live_inside_the_package_so_any_build_ships_them() -> None:
    # `package_data.read` los busca con `importlib.resources` bajo `ensmcp/data`,
    # así que tienen que estar dentro del propio paquete: el wheel declara
    # `packages = ["src/ensmcp"]` y se lleva lo que haya ahí dentro. Un fichero
    # de datos fuera de ese árbol carga en el repo y falta en el wheel, que es
    # justo el fallo que ningún otro test puede ver.
    data = _PACKAGE / "data"

    check(data.is_dir(), f"no existe {data}")
    shipped = {path.name for path in data.glob("*.json")}
    check(shipped == {"anexo_ii.json", "guia_808.json"}, f"datos empaquetados: {sorted(shipped)}")


# El probe viaja como argv de `-c`, y en la locale C de Linux CPython aborta en
# el arranque si no puede decodificar el propio argv ("Unable to decode the
# command from the command line"): un solo carácter no-ASCII aquí y el test
# muere antes de probar nada. Por eso la "í" va como escape `\\u00ed`, que el
# probe decodifica él mismo una vez arrancado.
_UTF8_PROBE = """
import sys
from ensmcp.guia.loader import load_packaged_guide
from ensmcp.snapshot.repository import SnapshotRepository

measures = SnapshotRepository.from_package_data().measures
guia = load_packaged_guide()
titles = " ".join(measure.title for measure in measures)
print(len(measures), len(guia.measure_evidence), "Pol\\u00edtica de seguridad" in titles, sep="|")
"""


async def test_both_corpora_are_read_as_utf8_whatever_the_locale_says() -> None:
    """Los dos ficheros del paquete se leen igual en un sistema no-UTF-8.

    ``package_data.read`` pasa ``encoding="utf-8"`` explícito, y esa palabra es
    lo único que separa el corpus de un montón de mojibake: sin ella
    ``read_text`` usa la codificación de la *locale*, y el modo UTF-8 no es el
    predeterminado hasta Python 3.15 (PEP 686). En un Windows recién instalado
    eso es cp1252 — y CLAUDE.md exige que la librería funcione en Windows.

    Lo peor es que **no falla**: leído como Latin-1, ``anexo_ii.json`` da 416985
    caracteres en vez de 410170, ``json.loads`` lo acepta tan contento y el
    servidor arranca contestando cada "í", "ó" y "ñ" del ENS en dos caracteres
    de basura. Un corpus en castellano servido por una herramienta de
    cumplimiento.

    Es el mismo fallo que ``build_guia_808`` ya tenía en su ``stdin`` y se
    comprueba igual: forzando la codificación desde el entorno. ``LC_ALL=C`` con
    ``PYTHONUTF8=0`` da US-ASCII en cualquier sistema —la locale C existe en
    todos— así que sin el ``encoding`` esto revienta con ``UnicodeDecodeError``.
    Verificado que distingue. ``create_subprocess_exec`` y no el módulo
    ``subprocess``, por lo mismo que ``test_main``: lista de argumentos, sin
    shell.
    """
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _UTF8_PROBE,
        env={**os.environ, "PYTHONUTF8": "0", "LC_ALL": "C"},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    out, errors = await process.communicate()

    check(
        process.returncode == 0,
        f"leer los corpus con la locale en US-ASCII falló: "
        f"{errors.decode(errors='replace')[-400:]}",
    )
    served = out.decode("utf-8").strip()
    check(
        served == "73|73|True", f"los corpus salieron distintos en una locale no-UTF-8: {served!r}"
    )
