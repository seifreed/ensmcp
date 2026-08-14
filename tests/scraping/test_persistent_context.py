"""Tests for PersistentBrowserContext's own failure-cleanup path.

The happy path (successful open()/close()) is already exercised indirectly
by every LiveSession test; what has no other coverage is open() cleaning up
after itself when the launch fails partway through.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from patchright.async_api import Error as PlaywrightError

from ensmcp.scraping.persistent_context import PersistentBrowserContext
from tests.support import check, leftover_profiles, require


async def test_open_cleans_up_after_a_failed_launch() -> None:
    # An unknown channel makes launch_persistent_context fail fast (no
    # network, no WAF involved) after async_playwright().start() and
    # tempfile.mkdtemp() have already run — exactly the partial-failure
    # window open() must clean up instead of leaking the driver process
    # and the temp profile dir.
    #
    # Lo que se comprueba es el directorio en disco, no que los campos internos
    # queden a None. Esa era la versión anterior de este test, y dejaba pasar
    # justo el fallo que importa: quitándole el `shutil.rmtree` a `close()` y
    # dejando el `self._user_data_dir = None`, seguía en verde mientras el
    # perfil se quedaba en el disco. La contabilidad interna no es la garantía;
    # "no se filtra el perfil" sí.
    before = leftover_profiles()
    browser = PersistentBrowserContext(headless=True, channel="not-a-real-channel-xyz")

    with pytest.raises(PlaywrightError):
        await browser.open()

    leaked = leftover_profiles() - before
    check(not leaked, f"open() dejó perfiles temporales sin borrar: {sorted(leaked)}")

    # Safe to close again even though open() never fully succeeded — y sigue sin
    # dejar nada detrás, que es lo que el `finally` de __main__ da por hecho.
    await browser.close()

    leaked = leftover_profiles() - before
    check(not leaked, f"un segundo close() dejó perfiles temporales: {sorted(leaked)}")


async def test_close_finishes_the_teardown_even_when_the_context_throws() -> None:
    """El `try/finally` de cada paso de `close()`, que no tenía test propio.

    ``context.close()` sí puede reventar: es justo el caso del que se recupera
    ``LiveSession`` —el navegador muerto por su cuenta (crash, OOM, alguien que
    cierra la ventana)—. Aquí se provoca de verdad, parando el driver por debajo
    del contexto, sin doblar nada.

    Lo que garantiza el `finally` no es que `close()` no lance —lanza, y debe
    hacerlo— sino que deja el campo a None, de modo que **un segundo intento
    termina el trabajo**. Sin él, `self._context` se quedaría puesto y cada
    reintento moriría en el mismo paso para siempre, con el perfil temporal en
    el disco a perpetuidad. Eso es exactamente lo que dice el docstring de
    `close()` y lo que ninguna prueba comprobaba: quitarle los dos `finally`
    dejaba la suite entera en verde.
    """
    before = leftover_profiles()
    browser = PersistentBrowserContext(headless=True, channel=None)
    await browser.open()
    # El driver se para por debajo, así que el contexto ya no puede cerrarse.
    await require(browser._playwright, "open() no dejó el driver puesto").stop()

    # ``TargetClosedError``, que patchright no exporta: es una subclase de
    # ``Error`` y con ella basta para afirmar que el paso reventó.
    with pytest.raises(PlaywrightError):
        await browser.close()

    # Aquí sí se mira el campo interno, y es lo único que se puede mirar: lo que
    # el `finally` hace *es* dejarlo a None. Comprobar sólo el disco no vale,
    # porque el segundo `context.close()` de patchright resulta ser idempotente
    # —no vuelve a lanzar— así que el reintento acaba limpiando el perfil
    # incluso sin el `finally`, y la fuga no se ve. Justo por eso hace falta
    # afirmarlo: el contrato es que un reintento termina **sin depender** de que
    # la llamada que falló sea idempotente, que no es algo que patchright
    # prometa. Con un fallo repetible —un driver colgado, un timeout— sin el
    # `finally` el contexto se quedaría puesto y cada `close()` moriría en el
    # mismo paso, sin llegar nunca al `rmtree`.
    check(browser._context is None, "el paso que falló no dejó su campo a None")

    # Y con eso, el reintento termina el trabajo.
    await browser.close()

    leaked = leftover_profiles() - before
    check(not leaked, f"el perfil temporal sobrevivió a los dos close(): {sorted(leaked)}")


async def test_one_close_finishes_the_teardown_even_when_a_step_throws() -> None:
    """Un solo ``close()`` tiene que dejarlo todo cerrado, no sólo el reintento.

    El test de arriba afirma que un **segundo** intento termina el trabajo, y
    eso es lo que daban los ``finally`` por separado: reponen el campo, pero la
    llamada que falla sigue saltándose los pasos siguientes. El problema es que
    en producción nadie reintenta — ``LiveSession.close`` llama a esto una sola
    vez, desde el ``finally`` de ``serve()``—, así que un ``context.close()``
    que revienta dejaba vivo el proceso del driver y el perfil temporal en
    disco: justo la fuga que esta clase existe para impedir, y en el escenario
    del que ``LiveSession`` dice recuperarse (el navegador muerto por su
    cuenta).

    Lo que se afirma es el estado tras **una** llamada, no tras dos. Y que siga
    lanzando: el fallo no se traga, sólo deja de llevarse por delante el resto
    del desmontaje.
    """
    before = leftover_profiles()
    browser = PersistentBrowserContext(headless=True, channel=None)
    await browser.open()
    await require(browser._playwright, "open() no dejó el driver puesto").stop()

    with pytest.raises(PlaywrightError):
        await browser.close()

    check(browser._playwright is None, "el driver se quedó sin parar tras el primer close()")
    leaked = leftover_profiles() - before
    check(not leaked, f"el perfil temporal sobrevivió a un close(): {sorted(leaked)}")


async def test_close_survives_a_profile_directory_that_is_already_gone() -> None:
    """Que el perfil ya no exista sigue siendo idempotente.

    El directorio temporal puede no estar cuando ``close()`` llega a borrarlo:
    un limpiador de ``/tmp``, un borrado a mano, o un ``close()`` anterior que
    ya pasó por ahí. Sin ``ignore_errors`` eso es un ``FileNotFoundError`` que
    sale de ``close()`` — y ``close()`` se llama desde el ``finally`` de
    ``serve()``, o sea que un fallo ahí es lo último que se ejecuta al apagar.

    Nada lo comprobaba: no tolerar ``FileNotFoundError`` dejaba la suite en verde.
    """
    browser = PersistentBrowserContext(headless=True, channel=None)
    await browser.open()
    profile = Path(require(browser._user_data_dir, "open() no dejó el perfil puesto"))
    await require(browser._context, "open() no dejó el contexto puesto").close()
    shutil.rmtree(profile)

    await browser.close()

    check(not profile.exists(), f"el perfil reapareció: {profile}")
    check(browser._user_data_dir is None, "close() no repuso el campo del perfil")


async def test_close_preserves_a_profile_path_when_deletion_fails(tmp_path: Path) -> None:
    browser = PersistentBrowserContext(headless=True, channel=None)
    profile = tmp_path / "profile"
    profile.write_text("not a directory")
    browser._user_data_dir = str(profile)

    with pytest.raises(NotADirectoryError):
        await browser.close()

    check(browser._user_data_dir == str(profile), "close() perdió la ruta para reintentar")

    profile.unlink()
    profile.mkdir()
    await browser.close()

    check(not profile.exists(), f"el reintento no borró el perfil: {profile}")
    check(browser._user_data_dir is None, "el reintento no repuso el campo del perfil")
