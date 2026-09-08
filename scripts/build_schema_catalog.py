"""Generate the public MCP tool schema catalog from the server contracts."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from atomic_write import write_atomic

from ensmcp.guia.loader import load_packaged_guide
from ensmcp.mcp_server.server import build_server
from ensmcp.schema_catalog import SCHEMA_VERSION
from ensmcp.snapshot.repository import SnapshotRepository


async def build() -> dict[str, Any]:
    async def refresh() -> None:
        return None

    server = build_server(
        SnapshotRepository.from_package_data(),
        refresh=refresh,
        status=lambda: {"source": "snapshot"},
        guia=load_packaged_guide(),
    )
    tools = sorted(await server.list_tools(), key=lambda tool: tool.name)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "schema_version": SCHEMA_VERSION,
        "tools": {
            tool.name: {
                "inputSchema": tool.input_schema,
                "outputSchema": tool.output_schema,
            }
            for tool in tools
        },
    }


def main() -> None:
    output = Path(sys.argv[1]) if len(sys.argv) == 2 else Path("src/ensmcp/schemas/v1/tools.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(output, json.dumps(asyncio.run(build()), ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
