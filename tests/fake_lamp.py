"""A small lamp in the test's event loop: the parts of firmware/API.md (version 2) the integration uses."""
from __future__ import annotations

import copy
import json

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer


def number(pid, name, kind, lo, hi, default, group="", **more):
    return dict({"id": pid, "name": name, "group": group, "type": kind, "min": lo, "max": hi, "default": default}, **more)


EFFECTS = [
    {"id": "single_color", "name": "Single Color", "preset": True, "midi": False,
     "params": [{"id": "color", "name": "myColor", "group": "", "type": "color", "default": [255, 120, 30]}]},
    {"id": "aurora", "name": "Aurora", "preset": True, "midi": False,
     "params": [number("speed", "Speed", "float", 0.2, 3.0, 1.0), number("palette", "Palette", "uint", 0, 2, 0)]},
    {"id": "nova_missile", "name": "Nova Missile", "preset": False, "midi": False,
     "params": [number("period_length", "Period Length", "float", 0.1, 40.0, 10.0, "Flying Emitters"),
                {"id": "spawn_collide_particle", "name": "Spawn CollideParticle", "group": "Flying Emitters", "type": "float",
                 "min": 0.0, "max": 1.0, "trigger": True}]},
]
BY_ID = {e["id"]: e for e in EFFECTS}


def error(status: int, code: str, message: str = "") -> web.Response:
    return web.json_response({"error": code, "message": message or code}, status=status)


class Refused(Exception):
    pass


class FakeLamp:
    def __init__(self, password: str | None = None, supply: bool = True) -> None:
        self.password = password
        self.supply = supply
        self.api = 2
        self.tokens: set[str] = set()
        self.revision = 1
        self.uptime = 100
        self.info_id = "aa:bb:cc:dd:ee:ff"
        self.clients: set[web.WebSocketResponse] = set()
        self.writes: list[tuple[object, dict]] = []  # ("lamp" or the screen, the patch as it came)
        self.state_reads = 0  # how often GET /api/state was asked
        self.notifications: list[tuple[str, dict]] = []  # (path, body) of POST /api/notify and /api/indicators
        self.indicator_count = 0  # "indicators" of /api/info
        self.can_notify = True  # False: the request fails with 404
        self.ha_enabled = True  # False: Home Assistant is switched off in the lamp's settings
        self.scenes: dict[int, tuple[str, list, list]] = {}  # index -> (name, screens, what they remember)
        self.scene_active: int | None = None
        self.scene_modified = False
        self.scene_list = 0
        self.lamp = {"on": True, "brightness": 255}
        self.screens = [
            {"index": i, "begin": 0, "length": 0, "mirror": False, "on": True, "brightness": 255, "source": i, "shows": i,
             "label": "", "effect": None, "params": {}, "starved": False}
            for i in range(16)
        ]
        self.memory: list[dict[str, dict]] = [{} for _ in range(16)]  # per screen: effect id -> its values
        self.screens[0].update(length=100)
        self.screens[1].update(begin=100, length=50)
        self.show(0, "aurora")
        self.show(1, "single_color")
        app = web.Application(middlewares=[self.ha_switch])
        app.router.add_get("/api/info", self.get_info)
        app.router.add_get("/api/effects", self.guarded(lambda r: web.json_response(EFFECTS)))
        app.router.add_get("/api/state", self.guarded(self.get_state))
        app.router.add_patch("/api/lamp", self.guarded(self.http_patch_lamp))
        app.router.add_patch("/api/screens/{i}", self.guarded(self.http_patch_screen))
        app.router.add_post("/api/login", self.login)
        app.router.add_post("/api/screens/{i}/{action}", self.guarded(self.screen_action))
        app.router.add_post("/api/save", self.guarded(self.ok))
        app.router.add_post("/api/reboot", self.guarded(self.ok))
        app.router.add_get("/api/scenes", self.guarded(lambda r: web.json_response(self.scenes_json())))
        app.router.add_post("/api/scenes", self.guarded(self.post_scene))
        app.router.add_post("/api/scenes/activate", self.guarded(self.post_activate))
        app.router.add_post("/api/notify", self.guarded(self.post_notify))
        app.router.add_post("/api/indicators", self.guarded(self.post_notify))
        app.router.add_post("/api/indicators/clear", self.guarded(self.post_notify))
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

    # ---- the model
    def show(self, i: int, effect: str | None) -> None:
        """Screen i gets this effect, with the values it remembers for it."""
        s = self.screens[i]
        s["effect"] = effect
        if effect is None:
            s["params"] = {}
            return
        values = self.memory[i].setdefault(effect, {})
        s["params"] = {p["id"]: values.get(p["id"], p["default"]) for p in BY_ID[effect]["params"] if not p.get("trigger")}

    def follow_sources(self) -> None:
        for s in self.screens:
            at, seen = s["index"], set()
            while self.screens[at]["source"] != at and at not in seen:
                seen.add(at)
                at = self.screens[at]["source"]
            s["shows"] = at if self.screens[at]["source"] == at else s["index"]

    def state_json(self) -> dict:
        return {"revision": self.revision, "frameLength": 150, "lamp": dict(self.lamp), "screens": copy.deepcopy(self.screens)}

    def apply_screen(self, i: int, patch: dict) -> None:
        s = self.screens[i]
        if "effect" in patch and patch["effect"] is not None and patch["effect"] not in BY_ID:
            raise Refused("effect: the id of an effect, or null")
        effect = patch.get("effect", s["effect"])
        for pid in patch.get("params", {}):
            if effect is None or not any(p["id"] == pid for p in BY_ID[effect]["params"]):
                raise Refused(f"no such parameter: {pid}")
        for k in ("begin", "length", "mirror", "on", "brightness", "source", "label"):
            if k in patch:
                s[k] = patch[k]
        if "effect" in patch:
            self.show(i, effect)
        for pid, value in patch.get("params", {}).items():
            p = next(p for p in BY_ID[effect]["params"] if p["id"] == pid)
            if p["type"] != "color":
                value = max(p["min"], min(p["max"], value))
                value = int(round(value)) if p["type"] == "uint" else float(value)
            if not p.get("trigger"):
                s["params"][pid] = value
                self.memory[i].setdefault(effect, {})[pid] = value
        self.follow_sources()

    async def patch_screen(self, i: int, patch: dict, origin: web.WebSocketResponse | None = None) -> dict:
        """A change to a screen: by a request, by another client, or (called from a test) at the lamp itself."""
        self.apply_screen(i, patch)
        self.writes.append((i, patch))
        self.revision += 1
        self.scene_modified = True
        await self.broadcast({"type": "screen", "revision": self.revision, "screen": copy.deepcopy(self.screens[i])}, origin)
        return self.screens[i]

    async def patch_lamp(self, patch: dict, origin: web.WebSocketResponse | None = None) -> dict:
        self.lamp.update({k: patch[k] for k in ("on", "brightness") if k in patch})
        self.writes.append(("lamp", patch))
        self.revision += 1
        await self.broadcast(dict(self.lamp, type="lamp", revision=self.revision), origin)
        return dict(self.lamp, revision=self.revision)

    # ---- access
    @web.middleware
    async def ha_switch(self, request, handler):
        """While Home Assistant is switched off, the lamp refuses it (here: every client)."""
        if self.ha_enabled or not request.path.startswith("/api/"):
            return await handler(request)
        if request.path == "/api/ws":
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_json({"type": "error", "error": "homeassistant", "message": "Home Assistant is switched off on this lamp"})
            await ws.close()
            return ws
        return error(403, "homeassistant", "Home Assistant is switched off on this lamp")

    async def switch_ha(self, on: bool) -> None:
        """As PUT /api/homeassistant in the lamp's web UI: switching off closes the WebSockets."""
        self.ha_enabled = on
        if not on:
            for ws in list(self.clients):
                await ws.send_json({"type": "error", "error": "homeassistant", "message": "Home Assistant is switched off on this lamp"})
                await ws.close()

    def authorized(self, request: web.Request) -> bool:
        if not self.password:
            return True
        tok = request.headers.get("Authorization", "")[7:] or request.query.get("token", "")
        return tok in self.tokens

    def guarded(self, handler):
        async def call(request: web.Request) -> web.StreamResponse:
            if not self.authorized(request):
                return web.json_response({"error": "auth", "scope": "control"}, status=401)
            answer = handler(request)
            return answer if isinstance(answer, web.StreamResponse) else await answer

        return call

    async def login(self, request):
        body = await request.json()
        if self.password and body.get("password") != self.password:
            return web.json_response({"error": "password", "scope": "control"}, status=401)
        tok = "t%d" % (len(self.tokens) + 1)
        self.tokens.add(tok)
        return web.json_response({"token": tok, "scope": "control"})

    # ---- requests
    def info_json(self) -> dict:
        d = {"name": "Testlampe", "id": self.info_id, "version": "2.0.0", "api": self.api, "uptimeS": self.uptime,
             "revision": self.revision, "lateFrames": 0, "heapFree": 100000, "indicators": self.indicator_count,
             "frameLength": 150, "power": {"estimatedMa": 1234, "budgetMa": 3000, "scale": 1.0},
             "wifi": {"state": "connected", "rssi": -60}}
        name = self.scenes[self.scene_active][0] if self.scene_active in self.scenes else None
        d["scene"] = {"index": self.scene_active if name else None, "name": name,
                      "modified": bool(name) and self.scene_modified, "list": self.scene_list}
        if self.supply:
            d["supply"] = {"source": "pd", "contractMv": 20000, "contractMa": 3000, "inputMa": 900, "efuseFault": False, "railsGood": True}
        return d

    async def get_state(self, request):
        self.state_reads += 1
        return web.json_response(self.state_json())

    async def get_info(self, request):
        return web.json_response(self.info_json())

    async def http_patch_lamp(self, request):
        return web.json_response(await self.patch_lamp(await request.json()))

    async def http_patch_screen(self, request):
        try:
            return web.json_response(await self.patch_screen(int(request.match_info["i"]), await request.json()))
        except Refused as err:
            return error(400, "request", str(err))

    async def screen_action(self, request):
        i = int(request.match_info["i"])
        s = self.screens[self.screens[i]["shows"]]
        if s["effect"] is None:
            return error(400, "request", "the screen has no effect")
        if request.match_info["action"] == "next-preset":
            if "palette" not in s["params"]:
                return error(400, "request", "the effect has no presets")
            await self.patch_screen(s["index"], {"params": {"palette": (s["params"]["palette"] + 1) % 3}})
        else:
            self.memory[s["index"]].pop(s["effect"], None)
            await self.patch_screen(s["index"], {"effect": s["effect"]})
        return web.json_response({})

    # ---- scenes
    def scenes_json(self) -> dict:
        return {"scenes": [{"index": i, "name": n} for i, (n, _, _) in sorted(self.scenes.items())],
                "active": self.scene_active, "modified": self.scene_modified, "max": 16}

    async def store_scene(self, index: int, name: str) -> None:
        """POST /api/scenes, as the web UI would do it."""
        self.scenes[index] = (name, copy.deepcopy(self.screens), copy.deepcopy(self.memory))
        self.scene_active, self.scene_modified = index, False
        self.scene_list += 1
        await self.broadcast(dict(self.scenes_json(), type="scenes"))

    async def post_scene(self, request):
        """Overwriting a scene by index (the name stays)."""
        i = (await request.json()).get("index")
        if i not in self.scenes:
            return error(404, "notfound")
        await self.store_scene(i, self.scenes[i][0])
        return web.json_response({"index": i, "name": self.scenes[i][0]})

    async def post_activate(self, request):
        body = await request.json()
        i = body.get("index")
        if i not in self.scenes:
            match = [k for k, (n, _, _) in self.scenes.items() if n.lower() == str(body.get("name", "")).lower()]
            i = match[0] if match else None
        if i is None:
            return error(404, "notfound")
        self.screens, self.memory = copy.deepcopy(self.scenes[i][1]), copy.deepcopy(self.scenes[i][2])
        self.scene_active, self.scene_modified = i, False
        self.revision += 1
        await self.broadcast(dict(self.state_json(), type="state"))
        await self.broadcast(dict(self.scenes_json(), type="scenes"))
        return web.json_response({"index": i, "name": self.scenes[i][0]})

    async def post_notify(self, request):
        if not self.can_notify:
            return error(404, "notfound")
        self.notifications.append((request.path, await request.json()))
        return web.json_response({})

    async def ok(self, request):
        return web.json_response({})

    # ---- WebSocket
    async def broadcast(self, msg: dict, origin: web.WebSocketResponse | None = None) -> None:
        """To every client but the one whose own change this is: that one knows what it said."""
        for ws in list(self.clients):
            if ws is not origin:
                await ws.send_json(msg)

    async def status(self) -> None:
        await self.broadcast(dict(self.info_json(), type="status"))

    async def ws(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        if not self.authorized(request):
            await ws.send_json({"type": "error", "error": "auth", "scope": "control"})
            await ws.close()
            return ws
        self.clients.add(ws)
        await ws.send_json(dict(self.info_json(), type="status"))
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                m = json.loads(msg.data)
                kind = m.pop("type", None)
                try:
                    if kind == "lamp":
                        await self.patch_lamp(m, ws)
                    elif kind == "screen":
                        await self.patch_screen(m.pop("index"), m, ws)
                except Refused as err:
                    await ws.send_json({"type": "error", "error": "request", "message": str(err)})
        finally:
            self.clients.discard(ws)
        return ws
