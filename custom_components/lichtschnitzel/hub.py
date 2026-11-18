"""Keeps one lamp's state: info, the effects it offers, lamp and screens, live through the WebSocket."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import aiohttp

from .api import AuthFailed, LichtschnitzelClient, LichtschnitzelError
from .const import NUM_SCREENS

_LOGGER = logging.getLogger(__name__)

# Fields of a screen that decide which effect runs and with which values: they go by HTTP, whose
# answer is the whole screen (the parameters the screen remembers for that effect included).
STRUCTURAL = ("effect", "source")


def param_path(param: dict) -> str:
    """What a parameter was called before API version 2: "Speed", or "Flying Emitters/Period Length"."""
    return f"{param['group']}/{param['name']}" if param.get("group") else param["name"]


class LichtschnitzelHub:
    """State of one lamp. Entities read from it and register a listener for changes."""

    def __init__(self, client: LichtschnitzelClient) -> None:
        self.client = client
        self.ident: str | None = None  # the config entry's unique id
        self.info: dict = {}
        self.effects: list[dict] = []  # GET /api/effects
        self.state: dict = {}  # GET /api/state
        self.scenes: dict = {}  # GET /api/scenes
        self.connected = False
        self._listeners: list[Callable[[], None]] = []
        self._screen_listeners: list[Callable[[], None]] = []
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._task: asyncio.Task | None = None
        self._reload: asyncio.TimerHandle | None = None
        self._settled = False  # the first status after connecting confirmed the revision
        self._batches: dict[str, dict] = {}

    # ---- lifecycle
    async def async_load(self) -> None:
        """First contact: info, effects, state and scenes. Raises if the lamp cannot be read."""
        self.info = await self.client.check_access()
        self.effects = await self.client.effects()
        self.state = await self.client.state()
        self.scenes = await self.client.scenes()

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._reload:
            self._reload.cancel()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()

    async def _run(self) -> None:
        delay = 1
        first = True
        while True:
            try:
                if not first:
                    # After a reboot the lamp may be another one: the Effektlabor switches to a 3D
                    # scene with other screens, names and effects. Entities follow what it says now.
                    await self.async_load()
                    self._screens_changed()
                first = False
                self._settled = False
                self._ws = await self.client.ws_connect()
                self._set_connected(True)
                delay = 1
                await self._listen(self._ws)
            except asyncio.CancelledError:
                raise
            except AuthFailed as err:
                _LOGGER.warning("Lichtschnitzel %s: login failed: %s", self.client.host, err)
            except LichtschnitzelError as err:
                _LOGGER.debug("Lichtschnitzel %s: %s", self.client.host, err)
            except Exception:  # keep the connection loop alive whatever the lamp sends
                _LOGGER.exception("Lichtschnitzel %s: unexpected error", self.client.host)
            self._ws = None
            self._set_connected(False)
            first = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def _listen(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                if msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                    break
                continue
            try:
                data = json.loads(msg.data)
            except ValueError:
                continue
            kind = data.get("type")
            if kind == "status":
                self._on_status(data)
            elif kind == "lamp":
                self.state["lamp"] = {"on": bool(data.get("on")), "brightness": int(data.get("brightness", 255))}
                self._seen(data)
                self._notify()
            elif kind == "screen" and isinstance(data.get("screen"), dict):
                self._adopt_screen(data["screen"])
                self._seen(data)
                self._screens_changed()
            elif kind == "state":  # many screens at once (a scene, a revert), or other outputs
                self.state = {k: v for k, v in data.items() if k != "type"}
                self._screens_changed()
            elif kind == "scenes":
                self.scenes = {k: v for k, v in data.items() if k != "type"}
                self._notify()
            elif kind == "error" and data.get("error") == "auth":
                # A lamp without a valid token for "control" says so and closes; log in for the next try.
                self.client.token = None
                await self.client.login()
                break
            elif kind == "error":
                # Something this side sent was refused: what is shown may be wrong now.
                _LOGGER.warning("Lichtschnitzel %s: %s", self.client.host, data.get("message") or data.get("error"))
                self.reload_state_soon()

    def _seen(self, data: dict) -> None:
        if data.get("revision") is not None:
            self.state["revision"] = data["revision"]

    def _on_status(self, data: dict) -> None:
        data = {k: v for k, v in data.items() if k != "type"}
        self._on_scene_status(self.info.get("scene"), data.get("scene"))
        self.info.update(data)
        rev = data.get("revision")
        if rev is not None and rev != self.state.get("revision"):
            # The revision counts every change. Over the open WebSocket the events brought them all
            # (a change of this side over the WebSocket is not echoed, it only moves the count), so
            # the state is read again only when something may have been missed while connecting.
            if self._settled:
                self.state["revision"] = rev
            else:
                self.reload_state_soon(0)
        self._settled = True
        self._notify()

    def _on_scene_status(self, before: dict | None, now: dict | None) -> None:
        """The scene part of the status: the active scene, and a counter that moves when the list changed."""
        if not isinstance(now, dict):
            return
        if isinstance(before, dict) and before.get("list") != now.get("list"):
            asyncio.get_running_loop().create_task(self._reload_scenes())
            return
        self.scenes["active"] = now.get("index")
        self.scenes["modified"] = bool(now.get("modified"))

    async def _reload_scenes(self) -> None:
        try:
            self.scenes = await self.client.scenes()
        except LichtschnitzelError as err:
            _LOGGER.debug("Lichtschnitzel %s: scenes: %s", self.client.host, err)
            return
        self._notify()

    # ---- scenes
    def scene_names(self) -> list[str]:
        return [s["name"] for s in self.scenes.get("scenes", [])]

    def find_scene(self, index: int | None = None, name: str | None = None) -> dict | None:
        """The scene with this index, or this name (exact, else ignoring case)."""
        items = self.scenes.get("scenes", [])
        if index is not None:
            return next((s for s in items if s["index"] == index), None)
        name = (name or "").strip()
        return next((s for s in items if s["name"] == name), None) or next(
            (s for s in items if s["name"].lower() == name.lower()), None
        )

    def active_scene(self) -> dict | None:
        active = self.scenes.get("active")
        return None if active is None else self.find_scene(index=active)

    async def async_activate_scene(self, index: int | None = None, name: str | None = None) -> None:
        """Switches to a scene. Raises ValueError for one the lamp does not have."""
        scene = self.find_scene(index, name)
        if scene is None:
            raise ValueError(f"no scene {index if index is not None else name!r}")
        await self.client.activate_scene(index=scene["index"])  # the lamp tells the screens as events
        self.scenes["active"] = scene["index"]
        self.scenes["modified"] = False
        self._notify()

    async def async_overwrite_scene(self) -> None:
        """Stores the screens as they are now in the active scene, under its name (POST /api/scenes with
        its index; needs the settings scope). Raises ValueError without an active scene."""
        scene = self.active_scene()
        if scene is None:
            raise ValueError("no active scene")
        await self.client.post("/api/scenes", {"index": scene["index"]})
        self.scenes["modified"] = False
        self._notify()

    # ---- notifications and indicators
    async def async_notify(self, screen: int | None, body: dict) -> None:
        """Shows a notification on screen (None: every screen with LEDs)."""
        await self._batched("/api/notify", screen, body)

    async def async_set_indicator(self, screen: int | None, body: dict) -> None:
        await self._batched("/api/indicators", screen, body)

    async def async_clear_indicator(self, screen: int | None, body: dict) -> None:
        await self._batched("/api/indicators/clear", screen, body)

    async def _batched(self, path: str, screen: int | None, body: dict) -> None:
        """POSTs body to path for screen. Calls with the same path and body that come together (one
        action on several screens of this lamp) go out as one request with "screens", so the screens
        start in step; a call for the whole lamp (screen None) leaves "screens" out."""
        key = json.dumps([path, body], sort_keys=True)
        batch = self._batches.get(key)
        if batch is None:
            loop = asyncio.get_running_loop()
            batch = {"screens": set(), "all": False, "done": loop.create_future()}
            self._batches[key] = batch
            loop.call_later(0.02, lambda: loop.create_task(self._send_batch(key)))
        if screen is None:
            batch["all"] = True
        else:
            batch["screens"].add(screen)
        await asyncio.shield(batch["done"])

    async def _send_batch(self, key: str) -> None:
        batch = self._batches.pop(key)
        path, body = json.loads(key)
        if not batch["all"]:
            body["screens"] = sorted(batch["screens"])
        try:
            await self.client.post(path, body)
        except Exception as err:  # every caller gets it
            batch["done"].set_exception(err)
        else:
            batch["done"].set_result(None)

    def _set_connected(self, on: bool) -> None:
        if self.connected != on:
            self.connected = on
            self._notify()

    # ---- state
    def reload_state_soon(self, delay: float = 0.3) -> None:
        if self._reload:
            self._reload.cancel()
        loop = asyncio.get_running_loop()
        self._reload = loop.call_later(delay, lambda: loop.create_task(self._reload_state()))

    async def _reload_state(self) -> None:
        try:
            self.state = await self.client.state()
        except LichtschnitzelError as err:
            _LOGGER.debug("Lichtschnitzel %s: state: %s", self.client.host, err)
            return
        self._screens_changed()

    def _adopt_screen(self, screen: dict) -> None:
        screens = self.state.setdefault("screens", [])
        i = screen.get("index")
        if isinstance(i, int) and 0 <= i < len(screens):
            screens[i] = screen

    def _screens_changed(self) -> None:
        for cb in list(self._screen_listeners):
            cb()
        self._notify()

    @property
    def lamp(self) -> dict:
        return self.state.get("lamp") or {}

    def screen(self, i: int) -> dict:
        screens = self.state.get("screens") or []
        return screens[i] if 0 <= i < len(screens) else {}

    def screen_used(self, i: int) -> bool:
        return (self.screen(i).get("length") or 0) > 0

    def screen_label(self, i: int) -> str:
        return self.screen(i).get("label") or f"Screen {i}"

    def shows(self, i: int) -> int:
        """The screen whose effect is seen on screen i: itself, or its source followed to the end."""
        s = self.screen(i).get("shows", i)
        return s if isinstance(s, int) and 0 <= s < NUM_SCREENS else i

    def effect(self, effect_id: str | None) -> dict | None:
        return next((e for e in self.effects if e.get("id") == effect_id), None)

    def effect_of(self, i: int) -> dict | None:
        """The effect screen i shows (its own, or that of its source), from /api/effects."""
        return self.effect(self.screen(self.shows(i)).get("effect"))

    def parameters(self, i: int) -> list[dict]:
        """The parameters of the effect screen i shows, for the Lichtschnitzel card: key (the id),
        name and group, type ("float", "uint" or "color"), the value, min and max for numbers, and
        "trigger" for something that happens once when it is set."""
        e = self.effect_of(i)
        if e is None:
            return []
        values = self.screen(self.shows(i)).get("params") or {}
        out: list[dict] = []
        for p in e.get("params", []):
            item = {"key": p["id"], "name": p.get("name", p["id"]), "group": p.get("group", ""), "type": p["type"],
                    "value": values.get(p["id"], p.get("default"))}
            if p["type"] != "color":
                item["min"] = p.get("min", 0)
                item["max"] = p.get("max", 255)
            if p["type"] == "float":  # the lamp's floats are 32 bit: 0.1 may arrive as 0.10000000149
                for k in ("value", "min", "max"):
                    if isinstance(item[k], (int, float)):
                        item[k] = float(f"{item[k]:.7g}")
            if p.get("trigger"):
                item["trigger"] = True
            out.append(item)
        return out

    # ---- writes
    async def async_patch_lamp(self, patch: dict) -> None:
        """Changes "on" and "brightness" of the lamp."""
        lamp = self.state.setdefault("lamp", {})
        if self._ws_open():
            lamp.update(patch)  # this side's change over the WebSocket is not echoed
            await self._ws.send_json({"type": "lamp", **patch})
        else:
            ans = await self.client.patch_lamp(patch)
            lamp.update({k: ans[k] for k in ("on", "brightness") if k in ans})
            self._seen(ans)
        self._notify()

    async def async_patch_screen(self, i: int, patch: dict) -> None:
        """Changes fields of screen i in one step. Values go over the WebSocket, which is quick enough
        for a slider being dragged; another effect or source goes by HTTP, because only the answer
        says which parameters the screen has then."""
        if self._ws_open() and not any(k in patch for k in STRUCTURAL):
            screen = self.screen(i)
            for k, v in patch.items():
                if k == "params":
                    screen.setdefault("params", {}).update(v)
                else:
                    screen[k] = v
            await self._ws.send_json({"type": "screen", "index": i, **patch})
            if "length" in patch:
                self._screens_changed()
            else:
                self._notify()
        else:
            self._adopt_screen(await self.client.patch_screen(i, patch))
            self._screens_changed()

    def _ws_open(self) -> bool:
        return self._ws is not None and not self._ws.closed

    async def async_set_parameter(self, i: int, key: str, value: Any) -> None:
        """Sets a parameter of the effect screen i shows, limited to its range. key is the parameter's
        id ("period_length"); what it was called before API version 2 ("Flying Emitters/Period Length")
        is understood too. Raises ValueError for a parameter the effect does not have, or a value
        that does not fit its type."""
        e = self.effect_of(i)
        params = e.get("params", []) if e else []
        param = next((p for p in params if p["id"] == key), None) or next((p for p in params if param_path(p) == key), None)
        if param is None:
            raise ValueError(f"no parameter {key!r} in the current effect")
        if param["type"] == "color":
            if isinstance(value, str):
                value = value.lstrip("#")
                value = [int(value[n : n + 2], 16) for n in (0, 2, 4)] if len(value) == 6 else None
            if not isinstance(value, (list, tuple)) or len(value) != 3:
                raise ValueError("a colour is [r, g, b] or #rrggbb")
            value = [max(0, min(255, int(c))) for c in value]
        else:
            if isinstance(value, (list, tuple)):
                raise ValueError("a number is expected")
            value = max(param.get("min", 0), min(param.get("max", 255), float(value)))
            value = int(round(value)) if param["type"] == "uint" else value
        await self.async_patch_screen(self.shows(i), {"params": {param["id"]: value}})

    # ---- listeners
    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    def add_screen_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Called when screens changed: one may have LEDs now, or show another effect."""
        self._screen_listeners.append(cb)
        return lambda: self._screen_listeners.remove(cb)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()
