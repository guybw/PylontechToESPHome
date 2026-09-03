"""Runtime link to the Pylontech console via an ESPHome ``serial_proxy`` port.

Owns a persistent, auto-reconnecting :class:`aioesphomeapi.APIClient`, keeps the
serial stream subscribed, and turns "send a console command, read its reply"
into a single awaitable.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime

from aioesphomeapi import APIClient, DeviceInfo, ReconnectLogic
from aioesphomeapi.core import APIConnectionError

from . import protocol
from .const import CONNECT_TIMEOUT, CONSOLE_BAUD, LOGGER, LOGIN_COMMAND, WAKE_BAUD


class PylontechConnectionError(Exception):
    """Raised when the bridge cannot be reached or the port is missing."""


class PylontechBridge:
    """Talk to one Pylontech stack through one ESPHome serial_proxy port."""

    def __init__(
        self,
        host: str,
        port: int,
        encryption_key: str,
        proxy_name: str,
        sync_time: bool = False,
    ) -> None:
        self._host = host
        self._proxy_name = proxy_name
        self._sync_time = sync_time
        self._client = APIClient(host, port, None, noise_psk=encryption_key)
        self._reconnect: ReconnectLogic | None = None
        self._instance: int | None = None
        self._buf = bytearray()
        self._lock = asyncio.Lock()
        self._connected = asyncio.Event()
        self._unsub_data: Callable[[], None] | None = None
        self.esphome_device_info: DeviceInfo | None = None

    @property
    def host(self) -> str:
        return self._host

    @property
    def available(self) -> bool:
        return self._connected.is_set() and self._instance is not None

    async def async_start(self) -> None:
        """Connect (with retry) and wait for the first successful handshake."""
        self._reconnect = ReconnectLogic(
            client=self._client,
            on_connect=self._on_connect,
            on_disconnect=self._on_disconnect,
            on_connect_error=self._on_connect_error,
            name=self._host,
        )
        await self._reconnect.start()
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                await self._connected.wait()
        except TimeoutError as err:
            await self.async_stop()
            raise PylontechConnectionError(
                f"Timed out connecting to {self._host} or serial port "
                f"{self._proxy_name!r} not found"
            ) from err

    async def async_stop(self) -> None:
        if self._reconnect is not None:
            await self._reconnect.stop()
            self._reconnect = None
        self._clear_data_sub()
        try:
            await self._client.disconnect()
        except APIConnectionError:
            pass

    # --- connection lifecycle ------------------------------------------

    async def _on_connect(self) -> None:
        info = await self._client.device_info()
        self.esphome_device_info = info
        names = [p.name for p in (info.serial_proxies or [])]
        if self._proxy_name not in names:
            LOGGER.error(
                "serial_proxy port %r not found on %s (available: %s)",
                self._proxy_name,
                self._host,
                names or "none",
            )
            return  # leave disconnected -> async_start() will time out

        self._instance = names.index(self._proxy_name)
        self._buf.clear()
        self._unsub_data = self._client.subscribe_serial_proxy_data(self._on_data)
        self._client.serial_proxy_subscribe(self._instance)
        await asyncio.sleep(0.3)

        try:
            async with self._lock:
                await self._raw_command(LOGIN_COMMAND, timeout=4.0)
                if self._sync_time:
                    await self._raw_command(
                        protocol.format_set_time(datetime.now()), timeout=4.0
                    )
        except Exception as err:  # noqa: BLE001 - best effort
            LOGGER.debug("%s: login/time-sync failed: %s", self._host, err)

        self._connected.set()
        LOGGER.debug("%s: bridge ready (serial_proxy instance %d)", self._host, self._instance)

    async def _on_disconnect(self, expected_disconnect: bool) -> None:
        self._connected.clear()
        self._instance = None
        self._clear_data_sub()
        LOGGER.debug("%s: bridge disconnected (expected=%s)", self._host, expected_disconnect)

    def _clear_data_sub(self) -> None:
        if self._unsub_data is not None:
            try:
                self._unsub_data()
            except Exception:  # noqa: BLE001
                pass
            self._unsub_data = None

    async def _on_connect_error(self, err: Exception) -> None:
        LOGGER.debug("%s: connect error: %s", self._host, err)

    def _on_data(self, msg) -> None:
        if msg.instance == self._instance:
            self._buf.extend(msg.data)

    # --- command / response ------------------------------------------

    async def async_command(self, command: str, timeout: float = 6.0) -> str:
        """Send ``command`` and return the raw console reply text."""
        if not self.available:
            raise PylontechConnectionError("bridge not connected")
        async with self._lock:
            return await self._raw_command(command, timeout)

    async def async_commands(
        self, commands: list[str], timeout: float = 6.0
    ) -> list[str]:
        """Run several commands back-to-back under one lock (no poll in between)."""
        if not self.available:
            raise PylontechConnectionError("bridge not connected")
        async with self._lock:
            return [await self._raw_command(cmd, timeout) for cmd in commands]

    async def async_set_time(self, when: datetime) -> str:
        """Set the battery RTC. Returns the console's reply text."""
        reply = await self.async_command(protocol.format_set_time(when), timeout=6.0)
        if not protocol.command_ok(reply):
            raise PylontechConnectionError(f"battery rejected time set: {reply.strip()}")
        return reply

    async def async_wake(self) -> None:
        """Nudge a silent battery: drop the console to 1200 baud, send the
        wake frame, switch back to 115200, then re-enter debug mode. Needs
        serial_proxy runtime reconfig (ESPHome >= 2026.3)."""
        if self._instance is None:
            raise PylontechConnectionError("bridge not connected")
        async with self._lock:
            LOGGER.info("%s: sending 1200-baud wake frame", self._host)
            self._client.serial_proxy_configure(self._instance, WAKE_BAUD)
            await asyncio.sleep(0.5)
            self._client.serial_proxy_write(self._instance, protocol.WAKE_FRAME)
            await asyncio.sleep(1.0)  # let the 22-byte frame clock out at 1200 baud
            self._client.serial_proxy_configure(self._instance, CONSOLE_BAUD)
            await asyncio.sleep(0.5)
            self._buf.clear()
            # A battery that had powered off comes back in its default,
            # non-debug mode; prod it with a CR and log back in so that the
            # next pwrsys/pwr poll returns data.
            try:
                await self._raw_command("", timeout=1.5)
                await self._raw_command(LOGIN_COMMAND, timeout=4.0)
            except Exception as err:  # noqa: BLE001 - best effort
                LOGGER.debug("%s: post-wake login failed: %s", self._host, err)

    async def _raw_command(self, command: str, timeout: float) -> str:
        assert self._instance is not None
        loop = asyncio.get_running_loop()
        self._buf.clear()
        self._client.serial_proxy_write(
            self._instance, (command + "\r\n").encode("ascii")
        )
        deadline = loop.time() + timeout
        seen = 0
        idle_since = loop.time()
        text = ""
        while loop.time() < deadline:
            await asyncio.sleep(0.15)
            text = bytes(self._buf).decode("ascii", "replace")
            if protocol.response_complete(text):
                break
            if len(self._buf) != seen:
                seen = len(self._buf)
                idle_since = loop.time()
            elif seen and loop.time() - idle_since > 0.6:
                break  # stream went quiet -> assume complete
        else:
            text = bytes(self._buf).decode("ascii", "replace")
        LOGGER.debug(
            "%s: %r -> %d bytes: %r", self._host, command, len(text), text[:300]
        )
        return text
