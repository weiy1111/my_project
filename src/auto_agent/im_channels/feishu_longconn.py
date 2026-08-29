"""Feishu long-connection (WebSocket) channel using the official lark-oapi SDK.

Events arrive over an outbound WebSocket connection, so no public domain or
tunnel is required. The SDK client runs on a dedicated daemon thread; inbound
payloads are marshalled back onto the asyncio loop that owns this channel.

Known limitation: current lark-oapi releases drop card action callbacks in
long-connection mode (``MessageType.CARD`` frames are ignored), so this
channel sends status cards without the cancel button by default. The parsing
path is kept in place so buttons start working once the SDK supports it.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Callable

from auto_agent.im_channels.feishu_common import FeishuApiChannel

EventHandler = Callable[[Any], Any]
WsClientFactory = Callable[[EventHandler], Any]


class _RawEventDispatcher:
    """Duck-typed stand-in for the SDK's EventDispatcherHandler.

    ``lark.ws.Client`` only calls ``_do_without_validation(raw_payload_bytes)``
    for EVENT frames, so we accept the raw JSON directly and route it onto the
    channel's asyncio loop without depending on SDK model classes.
    """

    def __init__(self, channel: FeishuLongConnectionChannel) -> None:
        self._channel = channel

    def _do_without_validation(self, raw_payload: bytes) -> None:
        try:
            payload = json.loads(raw_payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._channel._logger.warning("Feishu ws event payload is not valid JSON")
            return
        if isinstance(payload, dict):
            self._channel._submit_payload(payload)


class FeishuLongConnectionChannel(FeishuApiChannel):
    """Receive Feishu events via the official SDK's WebSocket long connection."""

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        channel_id: str = "feishu_ws",
        log_level: str = "WARNING",
        allow_cancel_button: bool = False,
        client_factory: WsClientFactory | None = None,
        **api_kwargs: Any,
    ) -> None:
        super().__init__(
            app_id=app_id,
            app_secret=app_secret,
            channel_id=channel_id,
            allow_cancel_button=allow_cancel_button,
            **api_kwargs,
        )
        self._log_level = log_level
        self._client_factory = client_factory or self._default_client_factory
        self._ws_thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _default_client_factory(self, event_handler: EventHandler) -> Any:
        try:
            import lark_oapi as lark
        except ImportError as exc:  # pragma: no cover - declared dependency
            raise RuntimeError(
                "lark-oapi is required for the Feishu long-connection channel"
            ) from exc
        level = getattr(lark.LogLevel, self._log_level, lark.LogLevel.WARNING)
        return lark.ws.Client(
            self.app_id,
            self.app_secret,
            event_handler=event_handler,
            log_level=level,
        )

    async def connect(self) -> None:
        await super().connect()
        self._loop = asyncio.get_running_loop()
        if self._ws_thread is not None and self._ws_thread.is_alive():
            return
        self._closed = False
        self._ws_thread = threading.Thread(
            target=self._run_ws, name=f"{self.channel_id}-ws", daemon=True
        )
        self._ws_thread.start()

    async def close(self) -> None:
        self._closed = True
        # The SDK client has no public stop API; its thread is a daemon and
        # exits with the process. Dropping the reference lets a later
        # connect() build a fresh client.
        self._ws_thread = None
        await super().close()

    # ------------------------------------------------------------ ws thread

    def _run_ws(self) -> None:
        try:
            client = self._client_factory(_RawEventDispatcher(self))
            client.start()  # blocks; auto-reconnects inside the SDK
        except Exception:
            if not self._closed:
                self._logger.exception("Feishu long-connection client terminated")

    def _submit_payload(self, payload: dict[str, Any]) -> None:
        """Called on the SDK thread; hops processing onto the asyncio loop."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self._process_payload(payload), loop)

    async def _process_payload(self, payload: dict[str, Any]) -> None:
        header = payload.get("header") or {}
        event_type = str(header.get("event_type") or payload.get("type") or "")
        if event_type == "im.message.receive_v1":
            message = self._to_im_message(payload)
            if message is not None:
                self._start_dispatch(message)
            return
        if (
            event_type == "card.action.trigger"
            or payload.get("type") == "card_action_trigger"
        ):
            action = self._to_im_action(payload)
            if action is not None:
                self._start_dispatch_action(action)
