"""Independent crosswalk data-pack loading and validation."""

import json
import os
from pathlib import Path

import pytest

from ensmcp.data_packs import DATA_PACKS_ENV_VAR, load_configured_data_packs, load_data_pack
from ensmcp.domain.crosswalk import DataPackStatus
from tests.support import check

_ROOT = Path(__file__).resolve().parent.parent
_PACKS = _ROOT / "packs"


def test_repository_packs_are_versioned_sourced_and_reference_known_measures() -> None:
    packs = {path.stem: load_data_pack(path) for path in _PACKS.glob("*.json")}
    check(set(packs) == {"iso27001", "nis2", "dora"})
    check(packs["iso27001"].status is DataPackStatus.ACTIVE)
    check(packs["nis2"].status is DataPackStatus.WITHDRAWN)
    check(packs["nis2"].mappings == ())
    check(all(pack.source_url.startswith("https://") for pack in packs.values()))

    corpus = json.loads((_ROOT / "src/ensmcp/data/anexo_ii.json").read_text(encoding="utf-8"))
    known_codes = {measure["code"] for measure in corpus["measures"]}
    referenced = {
        code
        for pack in packs.values()
        for mapping in pack.mappings
        for code in mapping.ens_measure_codes
    }
    check(referenced <= known_codes)


def test_configured_loader_accepts_direct_files_and_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DATA_PACKS_ENV_VAR, raising=False)
    check(load_configured_data_packs() == ())
    monkeypatch.setenv(DATA_PACKS_ENV_VAR, str(_PACKS))
    check([pack.pack_id for pack in load_configured_data_packs()] == ["dora", "iso27001", "nis2"])

    direct = os.pathsep.join((str(_PACKS / "iso27001.json"), str(_PACKS / "dora.json")))
    monkeypatch.setenv(DATA_PACKS_ENV_VAR, direct)
    check([pack.pack_id for pack in load_configured_data_packs()] == ["iso27001", "dora"])

    monkeypatch.setenv(
        DATA_PACKS_ENV_VAR,
        os.pathsep.join((str(_PACKS / "iso27001.json"), str(_PACKS / "iso27001.json"))),
    )
    with pytest.raises(ValueError, match="pack_id configurados"):
        load_configured_data_packs()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(DATA_PACKS_ENV_VAR, f"{_PACKS}{os.pathsep}")
    with pytest.raises(ValueError, match="rutas vacías"):
        load_configured_data_packs()


def test_loader_rejects_missing_malformed_incompatible_and_ambiguous_packs(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="data pack inválido"):
        load_data_pack(tmp_path / "missing.json")

    path = tmp_path / "pack.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="data pack inválido"):
        load_data_pack(path)

    payload = json.loads((_PACKS / "iso27001.json").read_text(encoding="utf-8"))
    payload["schema_version"] = 999
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        load_data_pack(path)

    payload["schema_version"] = 1
    payload["pack_id"] = "INVALID ID"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="pack_id inválido"):
        load_data_pack(path)

    payload["pack_id"] = "duplicate"
    payload["mappings"] = [payload["mappings"][0], payload["mappings"][0]]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="referencias duplicadas"):
        load_data_pack(path)
