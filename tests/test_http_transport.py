from __future__ import annotations

import json

import pytest
import uvicorn
from starlette.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from ensmcp.__main__ import build_wiring
from ensmcp.http_transport import (
    HTTPSettings,
    SecureBearerMiddleware,
    _matches,
    build_http_app,
    run_http_server,
)
from tests.support import check

TOKEN = "x" * 32


async def _request(
    authorization: str | None = f"Bearer {TOKEN}",
    *,
    host: str = "127.0.0.1",
    origin: str | None = None,
    scope_type: str = "http",
) -> tuple[list[Message], bool]:
    reached = False

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal reached
        reached = True

    middleware = SecureBearerMiddleware(
        downstream,
        token=TOKEN,
        allowed_hosts=("127.0.0.1", "127.0.0.1:*"),
        allowed_origins=("https://client.example",),
    )
    headers = [(b"host", host.encode())]
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    messages: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        messages.append(message)

    await middleware({"type": scope_type, "headers": headers}, receive, send)
    return messages, reached


def test_http_settings_reject_unsafe_configuration() -> None:
    for kwargs, message in (
        ({"token": "".join(("sho", "rt"))}, "al menos 32"),
        (
            {"token": TOKEN, "host": ".".join(("0", "0", "0", "0"))},
            "dirección local",
        ),
        ({"token": TOKEN, "host": "example.com"}, "dirección local"),
        ({"token": TOKEN, "port": 0}, "entre 1 y 65535"),
        ({"token": TOKEN, "port": 65536}, "entre 1 y 65535"),
        ({"token": TOKEN, "allowed_hosts": ("",)}, "Host permitido"),
        ({"token": TOKEN, "allowed_hosts": ("bad host",)}, "Host permitido"),
        ({"token": TOKEN, "allowed_hosts": ("bad/host",)}, "Host permitido"),
        ({"token": TOKEN, "allowed_origins": ("ftp://client.example",)}, "Origin"),
        ({"token": TOKEN, "allowed_origins": ("https:",)}, "Origin"),
        ({"token": TOKEN, "allowed_origins": ("https://client.example/path",)}, "Origin"),
        ({"token": TOKEN, "allowed_origins": ("https://client.example?a=1",)}, "Origin"),
        ({"token": TOKEN, "allowed_origins": ("https://client.example#x",)}, "Origin"),
    ):
        with pytest.raises(ValueError, match=message):
            HTTPSettings(**kwargs)

    check(HTTPSettings(token=TOKEN, host="localhost").host == "localhost")
    check(HTTPSettings(token=TOKEN, host="::1").host == "::1")
    check(
        HTTPSettings(
            token=TOKEN,
            allowed_hosts=("proxy.example",),
            allowed_origins=("https://client.example/",),
        ).port
        == 8000
    )


def test_host_match_is_exact_or_explicit_port_wildcard() -> None:
    check(_matches("localhost", ("localhost",)))
    check(_matches("localhost:8123", ("localhost:*",)))
    check(_matches("LOCALHOST:8123", ("localhost:*",)))
    check(_matches("HTTPS://CLIENT.EXAMPLE", ("https://client.example",)))
    check(not _matches("localhost", ("example.com", "example.com:*")))


async def test_bearer_middleware_rejects_untrusted_requests() -> None:
    for authorization, host, origin, status in (
        (None, "evil.example", None, 421),
        (None, "127.0.0.1", "https://evil.example", 403),
        (None, "127.0.0.1", None, 401),
        ("Basic abc", "127.0.0.1", None, 401),
        ("Bearer wrong", "127.0.0.1", None, 401),
    ):
        messages, reached = await _request(authorization, host=host, origin=origin)
        check(messages[0]["status"] == status)
        check(not reached)
        check(bool(json.loads(messages[1]["body"])["error"]))
        challenge = (b"www-authenticate", b'Bearer realm="ensmcp"')
        check(
            (challenge in messages[0]["headers"]) == (status == 401),
            f"unexpected authentication challenge for {status}",
        )

    messages, reached = await _request(origin="https://client.example")
    check(messages == [])
    check(reached)
    messages, reached = await _request(scope_type="lifespan")
    check(messages == [])
    check(reached)


def test_streamable_http_initializes_with_authentication() -> None:
    server, _ = build_wiring(None)
    app = build_http_app(server, HTTPSettings(token=TOKEN))
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2026-07-28",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }

    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            headers={
                "host": "127.0.0.1",
                "authorization": f"Bearer {TOKEN}",
                "accept": "application/json, text/event-stream",
            },
            json=request,
        )

    check(response.status_code == 200)
    check(response.json()["result"]["serverInfo"]["name"] == "ensmcp")
    ipv6_server, _ = build_wiring(None)
    build_http_app(ipv6_server, HTTPSettings(token=TOKEN, host="::1"))


async def test_uvicorn_runs_the_secured_app(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[uvicorn.Config] = []
    served: list[bool] = []

    class FakeServer:
        def __init__(self, config: uvicorn.Config) -> None:
            seen.append(config)

        async def serve(self) -> None:
            served.append(True)

    monkeypatch.setattr("ensmcp.http_transport.uvicorn.Server", FakeServer)
    server, _ = build_wiring(None)
    await run_http_server(server, HTTPSettings(token=TOKEN, port=8123))

    check(seen[0].host == "127.0.0.1")
    check(seen[0].port == 8123)
    check(served == [True])
