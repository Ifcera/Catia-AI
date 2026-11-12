"""CATIA araçlarını uygulamanın içinde bir MCP sunucusu (streamable HTTP) olarak sunar.

- Yalnızca 127.0.0.1'de dinler; her açılışta üretilen rastgele bir belirteç (Authorization: Bearer) ister,
  böylece bilgisayardaki başka bir program araçları çağıramaz.
- Araçlar aynı CatiaTools oturumunu (COM iş parçacığı, geri alma günlüğü, çalışma klasörü kısıtı) kullanır;
  arayüzün "son mesajı geri al" ve durdurma işlevleri bu yüzden Claude Code ile de çalışır.
- Girdiler araç şemasıyla doğrulanır; ekran görüntüleri MCP ImageContent olarak döner.
- on_event ile arayüze araç çağrısı/sonucu (görüntülerle) bildirilir.

Kullanım:
    srv = CatiaMcpServer(tools); srv.start(); cfg = srv.claude_mcp_config(); ...; srv.stop()
"""
from __future__ import annotations

import asyncio
import base64
import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable

import jsonschema
import uvicorn
from mcp import types as T
from mcp.server.lowlevel import Server

from .agent.prompts import AGENT_TOOLS
from .tools import CatiaTools

SERVER_NAME = "catia"


class _BearerAuth:
    """Belirteci olmayan istekleri 401 ile reddeden ASGI ara katmanı."""

    def __init__(self, app, token: str):
        self.app, self.expected = app, f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            if not secrets.compare_digest(headers.get(b"authorization", b""), self.expected):
                await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self.app(scope, receive, send)


class CatiaMcpServer:
    def __init__(self, tools: CatiaTools, host: str = "127.0.0.1", port: int = 0,
                 on_event: Callable[[str, dict], None] | None = None, image_max_size: int = 768):
        self.tools = tools
        self.host, self.port = host, port
        self.on_event = on_event or (lambda kind, data: None)
        self.image_max_size = image_max_size
        self.token = secrets.token_urlsafe(32)
        self.last_plan: dict | None = None
        self.calls = 0
        specs = tools.specs() + list(AGENT_TOOLS.values())
        self._specs = {s["name"]: s for s in specs}
        self._uv: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ MCP
    def _build(self) -> Server:
        async def list_tools(ctx, params) -> T.ListToolsResult:
            return T.ListToolsResult(tools=[
                T.Tool(name=s["name"], description=s["description"], input_schema=s["input_schema"])
                for s in self._specs.values()])

        async def call_tool(ctx, params: T.CallToolRequestParams) -> T.CallToolResult:
            name, args = params.name, dict(params.arguments or {})
            return await asyncio.to_thread(self.handle_call, name, args)

        return Server(SERVER_NAME, version="0.3", on_list_tools=list_tools, on_call_tool=call_tool,
                      instructions="CATIA V5 R20 Part Design araçları. Birimler mm/derece.")

    def handle_call(self, name: str, args: dict) -> T.CallToolResult:
        """Tek bir araç çağrısı (eşzamanlı; test için doğrudan da çağrılabilir)."""
        self.calls += 1
        call_id = f"mcp_{self.calls}"
        self.on_event("tool_call", {"id": call_id, "name": name, "input": args})
        t0 = time.monotonic()
        images: list[Path] = []
        spec = self._specs.get(name)
        if spec is None:
            data = {"ok": False, "error": f"Bilinmeyen araç: {name}"}
        else:
            try:
                jsonschema.validate(args, spec["input_schema"])
                err = None
            except jsonschema.ValidationError as exc:
                path = "/".join(str(p) for p in exc.absolute_path) or "(kök)"
                err = f"Girdi şemaya uymuyor ({path}): {exc.message}"
            if err:
                data = {"ok": False, "error": err, "hint": "Girdiyi araç şemasına göre düzeltin."}
            elif name == "present_plan":
                self.last_plan = args
                self.on_event("plan", {"plan": args})
                data = {"ok": True, "status": "Plan kullanıcıya gösterildi. Turu burada bitir ve kullanıcının onayını bekle; başka araç çağırma."}
            else:
                if name == "screenshot":
                    args = {**args, "max_size": min(int(args.get("max_size", self.image_max_size)), self.image_max_size)}
                try:
                    r = self.tools.call(name, args)
                    data, images = r.data, list(r.images)
                except Exception as exc:  # noqa: BLE001
                    data = {"ok": False, "error": f"Araç çalıştırılamadı: {exc.__class__.__name__}: {exc}"}
        self.on_event("tool_result", {"id": call_id, "name": name, "ok": bool(data.get("ok")), "data": data,
                                      "images": [str(p) for p in images], "seconds": round(time.monotonic() - t0, 2)})
        content: list[Any] = [T.TextContent(type="text", text=json.dumps(data, ensure_ascii=False, default=str))]
        for p in images:
            try:
                b64 = base64.standard_b64encode(Path(p).read_bytes()).decode("ascii")
                content.append(T.ImageContent(type="image", data=b64, mime_type="image/jpeg"))
            except OSError:
                pass
        return T.CallToolResult(content=content, is_error=not data.get("ok", True))

    # ---------------------------------------------------------------- HTTP
    def start(self, timeout: float = 15.0) -> None:
        app = _BearerAuth(self._build().streamable_http_app(host=self.host), self.token)
        # log_config=None: uvicorn'un kendi günlük yapılandırması sys.stdout.isatty() çağırır; konsolsuz (pythonw)
        # çalışırken sys.stdout None olduğu için "Unable to configure formatter 'default'" ile çöküyordu.
        cfg = uvicorn.Config(app, host=self.host, port=self.port, log_level="warning", lifespan="on",
                             log_config=None, access_log=False)
        self._uv = uvicorn.Server(cfg)
        self._thread = threading.Thread(target=self._uv.run, daemon=True, name="catia-mcp")
        self._thread.start()
        t0 = time.monotonic()
        while not self._uv.started:
            if not self._thread.is_alive() or time.monotonic() - t0 > timeout:
                raise RuntimeError("MCP sunucusu başlatılamadı.")
            time.sleep(0.05)
        self.port = self._uv.servers[0].sockets[0].getsockname()[1]

    def stop(self) -> None:
        if self._uv is not None:
            self._uv.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/mcp"

    def claude_mcp_config(self) -> dict:
        return {"mcpServers": {SERVER_NAME: {"type": "http", "url": self.url,
                                             "headers": {"Authorization": f"Bearer {self.token}"}}}}
