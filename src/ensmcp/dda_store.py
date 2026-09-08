"""Atomic filesystem storage for versioned DdA records."""

from __future__ import annotations

import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import TypeAdapter, ValidationError

from ensmcp.domain.dda import DDA_SCHEMA_VERSION, DDARecord

DATA_DIR_ENV_VAR = "ENSMCP_DATA_DIR"
_RECORD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_ADAPTER = TypeAdapter(DDARecord)


class FileDDAStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def from_environment(cls) -> FileDDAStore:
        configured = os.environ.get(DATA_DIR_ENV_VAR)
        root = Path(configured) if configured else Path.home() / ".ensmcp"
        return cls(root / "dda")

    def _path(self, record_id: str) -> Path:
        if _RECORD_ID.fullmatch(record_id) is None:
            raise ValueError("record_id debe usar sólo letras, números, punto, guion o guion bajo")
        return self.root / f"{record_id}.json"

    def _write(self, record: DDARecord, *, create: bool) -> None:
        path = self._path(record.record_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = _ADAPTER.dump_json(record, indent=2) + b"\n"
        with NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            if create:
                try:
                    os.link(temporary, path)
                except FileExistsError as exc:
                    raise ValueError(f"la DdA {record.record_id!r} ya existe") from exc
            else:
                os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def create(self, record: DDARecord) -> None:
        self._write(record, create=True)

    def save(self, record: DDARecord) -> None:
        self._write(record, create=False)

    def load(self, record_id: str) -> DDARecord:
        path = self._path(record_id)
        try:
            payload = path.read_bytes()
        except FileNotFoundError as exc:
            raise ValueError(f"DdA desconocida: {record_id!r}") from exc
        try:
            record = _ADAPTER.validate_json(payload)
        except ValidationError as exc:
            raise ValueError(f"DdA corrupta o incompatible: {record_id!r}") from exc
        if record.schema_version != DDA_SCHEMA_VERSION:
            raise ValueError(f"DdA corrupta o incompatible: {record_id!r}")
        return record

    def list_ids(self) -> tuple[str, ...]:
        if not self.root.is_dir():
            return ()
        return tuple(sorted(path.stem for path in self.root.glob("*.json") if path.is_file()))
