"""Shared pytest setup.

Enables coverage measurement inside the real subprocess that
tests/test_main.py spawns (``python -m ensmcp``), using coverage.py's
documented subprocess hook: a .pth file that calls
``coverage.process_startup()`` at interpreter start. It only activates
when COVERAGE_PROCESS_START is set (see test_main.py), so it never affects
a plain `python -m ensmcp` run.
"""

from __future__ import annotations

import sysconfig
from pathlib import Path

_HOOK_CONTENT = "import coverage; coverage.process_startup()\n"


def _site_packages() -> Path:
    """El directorio donde un ``.pth`` de verdad se ejecuta, en cualquier sistema.

    ``sysconfig.get_path("purelib")`` y no ``site.getsitepackages()[0]``, que era
    lo que había: ese índice sólo acierta en POSIX. En Windows —uno de los tres
    sistemas que CLAUDE.md exige soportar— ``getsitepackages()`` devuelve
    ``[sys.prefix, sys.prefix/"Lib"/"site-packages"]`` (ver el ``else`` de
    ``os.sep == '/'`` en el ``site.py`` de CPython), así que ``[0]`` es la raíz
    del entorno, donde Python **no** procesa ficheros ``.pth``.

    El hook se escribía ahí y no se cargaba nunca, con lo que el subproceso que
    lanza ``test_main.py`` no aportaba cobertura y la suite fallaba por
    ``--cov-fail-under=100`` con un error que no menciona ni el hook ni el
    sistema: sólo unas líneas sin cubrir en ``__main__.py``. Y devuelve un único
    valor, así que además no hay índice que elegir.
    """
    return Path(sysconfig.get_path("purelib"))


def _ensure_subprocess_coverage_hook() -> None:
    # A .pth file is executed by every interpreter that starts in this
    # environment, so this is deliberately the smallest possible one, written
    # only when its content would actually change. Explicit UTF-8 on both ends
    # keeps the comparison from depending on the ambient locale, which would
    # otherwise make this rewrite the file on some machines and not others.
    hook_path = _site_packages() / "ensmcp_coverage_subprocess.pth"
    if hook_path.exists() and hook_path.read_text(encoding="utf-8") == _HOOK_CONTENT:
        return
    hook_path.write_text(_HOOK_CONTENT, encoding="utf-8")


_ensure_subprocess_coverage_hook()
