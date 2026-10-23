"""Client for the HTTP and WebSocket API of a Lichtschnitzel lamp (firmware-esp32/API.md).

Independent of Home Assistant apart from aiohttp, so it can be tested against the mock
(firmware-esp32/tools/mock_api.py) or the Effektlabor behind tools/lab_bridge.py.
"""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

TIMEOUT = aiohttp.ClientTimeout(total=10)


class LichtschnitzelError(Exception):
    """The lamp answered with an error, or did not answer."""

    def __init__(self, message: str, code: str = "", status: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class CannotConnect(LichtschnitzelError):
    """The lamp is not reachable."""


class AuthFailed(LichtschnitzelError):
    """The lamp wants a password for "control", and the one given is missing or wrong."""


class LichtschnitzelClient:
    """One lamp. Logs in on demand: only when the lamp answers 401, and again after its reboot."""

    def __init__(self, session: aiohttp.ClientSession, host: str, port: int = 80, password: str | None = None) -> None:
        self.session = session
        self.host = host
        self.port = port
        self.password = password
        self.token: str | None = None

    @property
    def base(self) -> str:
        return f"http://{self.host}:{self.port}"

    async def login(self) -> None:
        # A scope without password takes any password, so an empty one works there too.
        ans = await self._call("POST", "/api/login", {"scope": "control", "password": self.password or ""}, auth=False)
        self.token = ans.get("token")

    async def request(self, method: str, path: str, body: Any = None) -> Any:
        for attempt in range(3):
            try:
                return await self._call(method, path, body)
            except LichtschnitzelError as err:
                if err.status == 401 and err.code == "auth" and attempt == 0:
                    await self.login()  # tokens are gone after a reboot of the lamp
                elif err.status == 503 and err.code == "busy" and attempt < 2:
                    await asyncio.sleep(0.2)  # the render task did not answer within 200 ms
                else:
                    raise
        raise LichtschnitzelError("no answer", "busy", 503)

    async def _call(self, method: str, path: str, body: Any = None, auth: bool = True) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if auth and self.token else {}
        try:
            async with self.session.request(method, self.base + path, json=body, headers=headers, timeout=TIMEOUT) as resp:
                try:
                    data = await resp.json(content_type=None)
                except ValueError:
                    data = None
                if resp.status >= 400:
                    err = data if isinstance(data, dict) else {}
                    if path == "/api/login" and resp.status == 401:
                        raise AuthFailed(err.get("message") or "wrong password", "password", 401)
                    raise LichtschnitzelError(err.get("message") or f"HTTP {resp.status}", err.get("error", ""), resp.status)
                return data
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            raise CannotConnect(str(err) or type(err).__name__) from err

    # ---- endpoints
    async def info(self) -> dict:
        return await self.request("GET", "/api/info")

    async def tree(self) -> dict:
        return await self.request("GET", "/api/tree")

    async def effects(self) -> list[dict]:
        return await self.request("GET", "/api/effects")

    async def set_property(self, path: str, value: Any) -> dict:
        return await self.request("PUT", "/api/property", {"path": path, "value": value})

    async def next_preset(self, screen: int) -> None:
        await self.request("POST", f"/api/screens/{screen}/next-preset")

    async def reset_effect(self, screen: int) -> None:
        await self.request("POST", f"/api/screens/{screen}/reset-effect")

    async def mark(self, leds: list[list[int]], ms: int = 1500) -> None:
        await self.request("POST", "/api/mark", {"leds": leds, "ms": ms})

    async def save(self) -> None:
        await self.request("POST", "/api/save")

    async def reboot(self) -> None:
        await self.request("POST", "/api/reboot")

    async def check_access(self) -> dict:
        """The lamp's info, after making sure the tree can be read (logs in if it has to)."""
        info = await self.info()
        try:
            await self._call("GET", "/api/effects")
        except LichtschnitzelError as err:
            if err.status != 401:
                raise
            if not self.password:
                raise AuthFailed("password needed", "auth", 401) from err
            await self.login()
        return info

    async def ws_connect(self) -> aiohttp.ClientWebSocketResponse:
        """The lamp's WebSocket (status every second, "changed" after writes)."""
        for attempt in range(2):
            url = f"{self.base}/api/ws" + (f"?token={self.token}" if self.token else "")
            try:
                ws = await self.session.ws_connect(url, heartbeat=30)
            except aiohttp.WSServerHandshakeError as err:
                if err.status == 401 and attempt == 0:
                    await self.login()
                    continue
                raise CannotConnect(str(err)) from err
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
                raise CannotConnect(str(err) or type(err).__name__) from err
            return ws
        raise AuthFailed("WebSocket refused", "auth", 401)
