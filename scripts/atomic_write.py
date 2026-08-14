"""Publish generated text without exposing a partial destination file."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def write_atomic(output_path: Path, content: str) -> None:
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output_path)
    except BaseException as error:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as cleanup_error:
                error.add_note(f"también falló la limpieza de {temporary}: {cleanup_error}")
        raise
