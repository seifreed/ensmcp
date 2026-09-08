"""Atomic filesystem persistence for DdA records."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from ensmcp.dda_store import DATA_DIR_ENV_VAR, FileDDAStore
from tests.domain.test_dda import sample_dda
from tests.support import check


def test_environment_location_and_empty_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DATA_DIR_ENV_VAR, str(tmp_path / "configured"))
    configured = FileDDAStore.from_environment()
    check(configured.root == tmp_path / "configured" / "dda")
    check(configured.list_ids() == ())

    monkeypatch.delenv(DATA_DIR_ENV_VAR)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    check(FileDDAStore.from_environment().root == tmp_path / ".ensmcp" / "dda")


def test_create_load_update_list_and_refuse_overwrite(tmp_path: Path) -> None:
    store = FileDDAStore(tmp_path)
    record = sample_dda()
    store.create(record)

    check(store.list_ids() == (record.record_id,))
    check(store.load(record.record_id) == record)
    with pytest.raises(ValueError, match="ya existe"):
        store.create(record)

    updated = replace(record, system="Portal actualizado")
    store.save(updated)
    check(store.load(record.record_id).system == "Portal actualizado")


def test_store_rejects_bad_ids_missing_corrupt_and_unknown_versions(tmp_path: Path) -> None:
    store = FileDDAStore(tmp_path)
    with pytest.raises(ValueError, match="record_id"):
        store.load("../escape")
    with pytest.raises(ValueError, match="desconocida"):
        store.load("missing")

    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "broken.json").write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupta"):
        store.load("broken")

    store.create(sample_dda())
    payload = json.loads((tmp_path / "portal-2026.json").read_text(encoding="utf-8"))
    payload["schema_version"] = 999
    (tmp_path / "portal-2026.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="incompatible"):
        store.load("portal-2026")
