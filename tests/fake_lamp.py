"""A small lamp in the test's event loop: the parts of firmware-esp32/API.md the integration uses."""
from __future__ import annotations

import copy
import json

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer

UINT_MAX = 4294967295


def U(v, lo=0, hi=255):
    return {"val": v, "type": "uint32", "min": lo, "max": hi}


def F(v, lo=0.0, hi=1.0):
    return {"val": v, "type": "float", "min": lo, "max": hi}


EFFECTS = [
    ("SingleColorEffect", "Single Color", {"myColor": {"val": [255, 120, 30], "type": "Rgb"}}),
    ("AuroraEffect", "Aurora", {"Speed": F(1.0, 0.2, 3.0), "Palette": U(0, 0, 2)}),
]


def screen(off, length, eff):
    return {"Offset": U(off, 0, 0), "Length": U(length, 0, 0), "EffectIndex": U(eff, 0, 0), "Mirror": U(0, 0, 1),
            "Source": U(0, 0, 15), "On": U(1, 0, 1), "Brightness": U(255, 0, 255), "Label": {"val": "", "type": "string"},
            "Effect": {"name": {"val": "none", "type": "string", "internal": True}, "type": "pobj"},
            "Effects": {"type": "pobj"}, "Settings": {"type": "pobj"}, "type": "pobj"}


class FakeLamp:
    def __init__(self, password: str | None = None, supply: bool = True) -> None:
        self.password = password
        self.supply = supply
        self.tokens: set[str] = set()
        self.revision = 0
        self.uptime = 100
        self.info_id = "aa:bb:cc:dd:ee:ff"
        self.clients: set[web.WebSocketResponse] = set()
        self.writes: list[tuple[str, object]] = []
        fbm = {f"Screen {i}": screen(0, 0, UINT_MAX) for i in range(16)}
        fbm["Screen 0"] = screen(0, 100, 1)
        fbm["Screen 1"] = screen(100, 50, 0)
        fbm["Screen 1"]["Source"]["val"] = 1
        for i in range(16):
            fbm[f"Screen {i}"]["Source"]["val"] = i
        fbm.update({"Length": U(151, 0, 0), "LateFrames": U(0, 0, 0), "On": U(1, 0, 1), "Brightness": U(255, 0, 255), "type": "pobj"})
        self.tree = {"FramebufferManager": fbm, "EventMapper": {"type": "pobj"}}
        for i in range(16):
            self.bind(i)
        app = web.Application()
        app.router.add_get("/api/info", self.info)
        app.router.add_get("/api/effects", self.effects)
        app.router.add_get("/api/tree", self.get_tree)
        app.router.add_put("/api/property", self.put_property)
        app.router.add_post("/api/login", self.login)
        app.router.add_post("/api/screens/{i}/{action}", self.screen_action)
        app.router.add_post("/api/save", self.ok)
        app.router.add_post("/api/reboot", self.ok)
        app.router.add_get("/api/ws", self.ws)
        self.server = TestServer(app, host="127.0.0.1")

    async def start(self) -> None:
        await self.server.start_server()

    async def stop(self) -> None:
        for ws in list(self.clients):
            await ws.close()
        await self.server.close()

    @property
    def port(self) -> int:
        return self.server.port

    def scr(self, i: int) -> dict:
        return self.tree["FramebufferManager"][f"Screen {i}"]

    def bind(self, i: int) -> None:
        s = self.scr(i)
        eff = s["EffectIndex"]["val"]
        if not s["On"]["val"] or eff >= len(EFFECTS):
            s["Effects"] = {"type": "pobj"}
            s["Effect"] = {"name": {"val": "none", "type": "string", "internal": True}, "type": "pobj"}
        else:
            key, name, props = EFFECTS[eff]
            if key not in s["Effects"]:
                s["Effects"] = {key: dict(copy.deepcopy(props), name={"val": name, "type": "string", "internal": True}, type="pobj"), "type": "pobj"}
            s["Effect"] = s["Effects"][key]
        self.revision += 1

    def authorized(self, request: web.Request) -> bool:
        if not self.password:
            return True
        tok = request.headers.get("Authorization", "")[7:] or request.query.get("token", "")
        return tok in self.tokens

    def denied(self) -> web.Response:
        return web.json_response({"error": "auth", "scope": "control"}, status=401)

    def info_json(self) -> dict:
        d = {"name": "Testlampe", "id": self.info_id, "version": "1.2.3", "api": 1, "uptimeS": self.uptime,
             "revision": self.revision, "lateFrames": 0, "heapFree": 100000,
             "power": {"estimatedMa": 1234, "budgetMa": 3000, "scale": 1.0},
             "wifi": {"state": "connected", "rssi": -60}}
        if self.supply:
            d["supply"] = {"source": "pd", "contractMv": 20000, "contractMa": 3000, "inputMa": 900, "efuseFault": False, "railsGood": True}
        return d

    async def info(self, request):
        return web.json_response(self.info_json())

    async def effects(self, request):
        if not self.authorized(request):
            return self.denied()
        return web.json_response([{"index": i, "name": n, "key": k} for i, (k, n, _) in enumerate(EFFECTS)])

    async def get_tree(self, request):
        if not self.authorized(request):
            return self.denied()
        return web.json_response(self.tree)

    async def login(self, request):
        body = await request.json()
        if self.password and body.get("password") != self.password:
            return web.json_response({"error": "password", "scope": "control"}, status=401)
        tok = "t%d" % (len(self.tokens) + 1)
        self.tokens.add(tok)
        return web.json_response({"token": tok, "scope": "control"})

    async def set(self, path: str, value) -> dict:
        n = self.tree
        for p in path.split("/"):
            n = n.get(p) if isinstance(n, dict) else None
        if not isinstance(n, dict) or "val" not in n:
            raise web.HTTPNotFound(text=json.dumps({"error": "path"}))
        if n["type"] == "uint32" and n["max"]:
            value = max(n["min"], min(n["max"], int(value)))
        n["val"] = value
        self.writes.append((path, value))
        parts = path.split("/")
        if len(parts) == 3 and parts[2] in ("EffectIndex", "On"):
            self.bind(int(parts[1].split()[1]))
        await self.broadcast({"type": "changed", "path": path, "value": value})
        return {"path": path, "value": value}

    async def put_property(self, request):
        if not self.authorized(request):
            return self.denied()
        body = await request.json()
        return web.json_response(await self.set(body["path"], body["value"]))

    async def screen_action(self, request):
        if not self.authorized(request):
            return self.denied()
        i = int(request.match_info["i"])
        if request.match_info["action"] == "next-preset":
            pal = self.scr(i)["Effect"].get("Palette")
            if pal:
                pal["val"] = (pal["val"] + 1) % 3
        self.revision += 1
        return web.json_response({})

    async def ok(self, request):
        return web.json_response({})

    async def broadcast(self, msg: dict) -> None:
        for ws in list(self.clients):
            await ws.send_json(msg)

    async def status(self) -> None:
        await self.broadcast(dict(self.info_json(), type="status"))

    async def ws(self, request):
        if not self.authorized(request):
            return self.denied()
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.clients.add(ws)
        await ws.send_json(dict(self.info_json(), type="status"))
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    m = json.loads(msg.data)
                    if m.get("type") == "set":
                        await self.set(m["path"], m["value"])
        finally:
            self.clients.discard(ws)
        return ws
