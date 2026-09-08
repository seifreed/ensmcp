"""Authenticated Streamable HTTP with strict local binding and Origin checks."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from hmac import compare_digest
from ipaddress import ip_address
from urllib.parse import urlsplit

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.types import ASGIApp, Message, Receive, Scope, Send


@dataclass(frozen=True, slots=True)
class HTTPSettings:
    token: str
    host: str = "127.0.0.1"
    port: int = 8000
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.token) < 32:
            raise ValueError("el token HTTP debe tener al menos 32 caracteres")
        try:
            loopback = ip_address(self.host).is_loopback
        except ValueError:
            loopback = self.host == "localhost"
        if not loopback:
            raise ValueError("el transporte HTTP sólo puede enlazarse a una dirección local")
        if not 1 <= self.port <= 65535:
            raise ValueError("el puerto HTTP debe estar entre 1 y 65535")
        for host in self.allowed_hosts:
            if not host or any(character.isspace() for character in host) or "/" in host:
                raise ValueError(f"Host permitido inválido: {host!r}")
        for origin in self.allowed_origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(f"Origin permitido inválido: {origin!r}")


def _matches(value: str, allowed: Sequence[str]) -> bool:
    value = value.casefold()
    return any(
        value == item or (item.endswith(":*") and value.startswith(f"{item[:-2]}:"))
        for item in map(str.casefold, allowed)
    )


class SecureBearerMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        token: str,
        allowed_hosts: tuple[str, ...],
        allowed_origins: tuple[str, ...],
    ) -> None:
        self.app = app
        self.token = token
        self.allowed_hosts = allowed_hosts
        self.allowed_origins = allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", ())
        }
        host = headers.get("host", "")
        if not _matches(host, self.allowed_hosts):
            await _response(send, 421, "invalid_host", "Invalid Host header")
            return
        origin = headers.get("origin")
        if origin and not _matches(origin, self.allowed_origins):
            await _response(send, 403, "invalid_origin", "Invalid Origin header")
            return
        scheme, separator, credentials = headers.get("authorization", "").partition(" ")
        if not (
            separator and scheme.casefold() == "bearer" and compare_digest(credentials, self.token)
        ):
            await _response(
                send,
                401,
                "invalid_token",
                "Authentication required",
                authenticate=True,
            )
            return
        await self.app(scope, receive, send)


async def _response(
    send: Send,
    status: int,
    error: str,
    description: str,
    *,
    authenticate: bool = False,
) -> None:
    body = json.dumps({"error": error, "error_description": description}).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    if authenticate:
        headers.append((b"www-authenticate", b'Bearer realm="ensmcp"'))
    start: Message = {"type": "http.response.start", "status": status, "headers": headers}
    response: Message = {"type": "http.response.body", "body": body}
    await send(start)
    await send(response)


def build_http_app(server: MCPServer, settings: HTTPSettings) -> Starlette:
    host_header = f"[{settings.host}]" if ":" in settings.host else settings.host
    allowed_hosts = (
        host_header,
        f"{host_header}:*",
        *settings.allowed_hosts,
    )
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(allowed_hosts),
        allowed_origins=list(settings.allowed_origins),
    )
    app = server.streamable_http_app(
        host=settings.host,
        stateless_http=True,
        json_response=True,
        transport_security=security,
    )
    app.add_middleware(
        SecureBearerMiddleware,
        token=settings.token,
        allowed_hosts=allowed_hosts,
        allowed_origins=settings.allowed_origins,
    )
    return app


async def run_http_server(server: MCPServer, settings: HTTPSettings) -> None:
    config = uvicorn.Config(
        build_http_app(server, settings),
        host=settings.host,
        port=settings.port,
        log_level="info",
    )
    await uvicorn.Server(config).serve()
