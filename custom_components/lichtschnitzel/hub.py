"""Keeps one lamp's state: info, effect list and property tree, live through the WebSocket."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from typing import Any

import aiohttp

from .api import AuthFailed, LichtschnitzelClient, LichtschnitzelError
from .const import FBM, NO_EFFECT, NUM_SCREENS, PARAM_TYPES

_LOGGER = logging.getLogger(__name__)

# Writes after which "Effect" of a screen is another object, so the tree is read again.
RELOAD_AFTER = re.compile(r"^FramebufferManager/Screen \d+/(EffectIndex|On|Source|Length)$")


class LichtschnitzelHub:
    """State of one lamp. Entities read from it and register a listener for changes."""

    def __init__(self, client: LichtschnitzelClient) -> None:
        self.client = client
        self.info: dict = {}
        self.effects: list[dict] = []
        self.tree: dict = {}
        self.connected = False
        self._listeners: list[Callable[[], None]] = []
        self._tree_listeners: list[Callable[[], None]] = []
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._task: asyncio.Task | None = None
        self._reload: asyncio.TimerHandle | None = None
        self._revision: int | None = None

    # ---- lifecycle
    async def async_load(self) -> None:
        """First contact: info, effects and tree. Raises if the lamp cannot be read."""
        self.info = await self.client.check_access()
        self.effects = await self.client.effects()
        self.tree = await self.client.tree()
        self._revision = self.info.get("revision")

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
                    # scene with other screens, names and effects. Entities follow the new tree.
                    await self.async_load()
                    self._tree_changed()
                first = False
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
            elif kind == "changed":
                self._on_changed(data.get("path", ""), data.get("value"))
            elif kind == "error" and data.get("error") == "auth":
                # A lamp without a valid token for "control" says so and closes; log in for the next try.
                self.client.token = None
                await self.client.login()
                break
            elif kind == "error":
                _LOGGER.warning("Lichtschnitzel %s: %s", self.client.host, data.get("message") or data.get("error"))

    def _on_status(self, data: dict) -> None:
        data = {k: v for k, v in data.items() if k != "type"}
        rebooted = self.info and data.get("uptimeS", 0) + 3 < self.info.get("uptimeS", 0)
        self.info.update(data)
        rev = data.get("revision")
        if rebooted or (rev is not None and rev != self._revision):
            self._revision = rev
            self.reload_tree_soon()
        self._notify()

    def _on_changed(self, path: str, value: Any) -> None:
        node = self.node(path)
        if isinstance(node, dict) and "val" in node:
            node["val"] = value
        if RELOAD_AFTER.match(path):
            self.reload_tree_soon()
        self._notify()

    def _set_connected(self, on: bool) -> None:
        if self.connected != on:
            self.connected = on
            self._notify()

    # ---- tree
    def reload_tree_soon(self, delay: float = 0.3) -> None:
        if self._reload:
            self._reload.cancel()
        loop = asyncio.get_running_loop()
        self._reload = loop.call_later(delay, lambda: loop.create_task(self._reload_tree()))

    async def _reload_tree(self) -> None:
        try:
            self.tree = await self.client.tree()
        except LichtschnitzelError as err:
            _LOGGER.debug("Lichtschnitzel %s: tree: %s", self.client.host, err)
            return
        self._tree_changed()

    def _tree_changed(self) -> None:
        for cb in list(self._tree_listeners):
            cb()
        self._notify()

    def node(self, path: str) -> Any:
        n: Any = self.tree
        for part in path.split("/"):
            if not isinstance(n, dict):
                return None
            n = n.get(part)
        return n

    def value(self, path: str, default: Any = None) -> Any:
        n = self.node(path)
        return n.get("val", default) if isinstance(n, dict) else default

    def has(self, path: str) -> bool:
        n = self.node(path)
        return isinstance(n, dict) and "val" in n

    # ---- screens
    @staticmethod
    def screen_path(i: int) -> str:
        return f"{FBM}/Screen {i}"

    def screen_used(self, i: int) -> bool:
        return (self.value(f"{self.screen_path(i)}/Length", 0) or 0) > 0

    def screen_label(self, i: int) -> str:
        return self.value(f"{self.screen_path(i)}/Label", "") or f"Screen {i}"

    def source_of(self, i: int) -> int:
        # FramebufferManager::sourceScreen: follow Source; a loop ends at the screen itself.
        s = i
        for _ in range(NUM_SCREENS):
            n = self.value(f"{self.screen_path(s)}/Source", s)
            if n == s or not isinstance(n, int) or n >= NUM_SCREENS:
                return s
            s = n
        return i

    def effect_of(self, i: int) -> dict | None:
        """The effect screen i shows (its own, or that of its source), from /api/effects."""
        idx = self.value(f"{self.screen_path(self.source_of(i))}/EffectIndex", NO_EFFECT)
        return next((e for e in self.effects if e.get("index") == idx), None)

    def effect_base(self, i: int) -> str | None:
        """Path of the instance screen i shows (in its source screen), None without an effect."""
        e = self.effect_of(i)
        if e is None:
            return None
        path = f"{self.screen_path(self.source_of(i))}/Effects/{e['key']}"
        return path if isinstance(self.node(path), dict) else None

    def parameters(self, i: int) -> list[dict]:
        """The parameters of the effect screen i shows: key (path inside the instance), type, value,
        and min/max for numbers. Sub-objects (e.g. "Flying Emitters") give keys like "a/b"."""
        base = self.effect_base(i)
        out: list[dict] = []
        if base is None:
            return out

        def walk(node: dict, prefix: str) -> None:
            for key, p in node.items():
                if key == "name" or not isinstance(p, dict) or p.get("internal"):
                    continue
                kind = p.get("type")
                if kind == "pobj":
                    walk(p, prefix + key + "/")
                elif kind in PARAM_TYPES:
                    item = {"key": prefix + key, "type": kind, "value": p.get("val")}
                    if kind != "Rgb":
                        item["min"] = p.get("min", 0)
                        item["max"] = p.get("max", 255)
                    if kind == "float":  # the lamp's floats are 32 bit: 0.1 arrives as 0.10000000149
                        for k in ("value", "min", "max"):
                            if isinstance(item[k], (int, float)):
                                item[k] = float(f"{item[k]:.7g}")
                    out.append(item)

        walk(self.node(base), "")
        return out

    # ---- writes
    async def async_set(self, path: str, value: Any) -> None:
        node = self.node(path)
        if isinstance(node, dict) and "val" in node:
            node["val"] = value  # optimistic; the lamp's "changed" brings the stored value
        if self._ws is not None and not self._ws.closed:
            await self._ws.send_json({"type": "set", "path": path, "value": value})
        else:
            ans = await self.client.set_property(path, value)
            self._on_changed(ans.get("path", path), ans.get("value", value))
        self._notify()

    async def async_set_parameter(self, i: int, key: str, value: Any) -> None:
        """Sets a parameter of the effect screen i shows, clamped to its limits. Raises ValueError
        for a parameter the effect does not have, or a value that does not fit its type."""
        param = next((p for p in self.parameters(i) if p["key"] == key), None)
        if param is None:
            raise ValueError(f"no parameter {key!r} in the current effect")
        if param["type"] == "Rgb":
            if isinstance(value, str):
                value = value.lstrip("#")
                value = [int(value[n : n + 2], 16) for n in (0, 2, 4)] if len(value) == 6 else None
            if not isinstance(value, (list, tuple)) or len(value) != 3:
                raise ValueError("a colour is [r, g, b] or #rrggbb")
            value = [max(0, min(255, int(c))) for c in value]
        else:
            value = max(param["min"], min(param["max"], float(value)))
            value = int(round(value)) if param["type"] == "uint32" else value
        await self.async_set(f"{self.effect_base(i)}/{key}", value)

    # ---- listeners
    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    def add_tree_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Called after the tree was read again: screens or effect parameters may be new."""
        self._tree_listeners.append(cb)
        return lambda: self._tree_listeners.remove(cb)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()
