"""Shared Feishu transport logic used by both webhook and long-connection channels."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

import httpx

from auto_agent.exceptions import (
    ImChannelAuthError,
    ImChannelError,
    ImChannelProtocolError,
)
from auto_agent.im_channels.base import BaseImChannel
from auto_agent.im_channels.cards import build_final_output_card, build_status_card
from auto_agent.models import AgentEvent, EventType, ImMessage

GroupSessionScope = Literal["chat", "sender", "thread"]
ReplyMode = Literal["reply_message", "send_to_chat"]

_RUNNING_STATUSES = {"running"}
_TERMINAL_STATUSES = {"completed", "cancelled", "failed", "timed_out"}

_TOKEN_ERROR_CODES = {99991661, 99991663, 99991664, 99991668}


class FeishuApiChannel(BaseImChannel):
    """Feishu Open Platform outbound API plus inbound message parsing.

    Holds tenant token management, message/card sending, session routing and
    the shared ``im.message.receive_v1`` -> :class:`ImMessage` conversion.
    Inbound transports (HTTP webhook or WebSocket long connection) subclass
    this and feed parsed payloads into :meth:`_to_im_message`.
    """

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        channel_id: str = "feishu",
        base_url: str = "https://open.feishu.cn",
        group_session_scope: GroupSessionScope = "sender",
        reply_mode: ReplyMode = "reply_message",
        respond_to_group_mentions_only: bool = True,
        allowed_chats: list[str] | None = None,
        allowed_senders: list[str] | None = None,
        stream_events: bool = False,
        max_message_chars: int = 8000,
        allow_cancel_button: bool = True,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not app_id or not app_secret:
            raise ValueError("Feishu app_id and app_secret are required")
        if group_session_scope not in {"chat", "sender", "thread"}:
            raise ValueError(f"unsupported group_session_scope: {group_session_scope}")
        if reply_mode not in {"reply_message", "send_to_chat"}:
            raise ValueError(f"unsupported reply_mode: {reply_mode}")
        BaseImChannel.__init__(self, channel_id)
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = base_url.rstrip("/")
        self.group_session_scope = group_session_scope
        self.reply_mode = reply_mode
        self.respond_to_group_mentions_only = respond_to_group_mentions_only
        self.allowed_chats = tuple(allowed_chats or ())
        self.allowed_senders = tuple(allowed_senders or ())
        self.stream_events = stream_events
        self.max_message_chars = max(1, max_message_chars)
        self.allow_cancel_button = allow_cancel_button
        self._client = client
        self._owns_client = client is None
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()
        self._routes: OrderedDict[str, tuple[str, str]] = OrderedDict()
        self._route_cache_size = 4096
        self._status_cards: OrderedDict[str, str] = OrderedDict()
        self._dispatch_tasks: set[asyncio.Task[None]] = set()
        self._closed = True
        self._logger = logging.getLogger(__name__)

    async def connect(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10)
        self._closed = False

    async def close(self) -> None:
        self._closed = True
        tasks = list(self._dispatch_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._dispatch_tasks.clear()
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ send

    async def send_reply(
        self,
        session_id: str,
        content: str,
        *,
        metadata: dict | None = None,
    ) -> None:
        if not content:
            return
        metadata = metadata or {}
        route = self._routes.get(session_id)
        message_id = str(
            metadata.get("reply_callback")
            or metadata.get("message_id")
            or (route[0] if route else "")
        )
        chat_id = str(metadata.get("chat_id") or (route[1] if route else ""))
        if self.reply_mode == "reply_message" and not message_id:
            raise ImChannelError(
                "Feishu reply target is unavailable",
                context={"channel_id": self.channel_id, "session_id": session_id},
            )
        if self.reply_mode == "send_to_chat" and not chat_id:
            raise ImChannelError(
                "Feishu chat target is unavailable",
                context={"channel_id": self.channel_id, "session_id": session_id},
            )

        for chunk in self._split_content(content):
            await self._send_text(message_id=message_id, chat_id=chat_id, content=chunk)

    async def send_event(
        self,
        session_id: str,
        event: AgentEvent,
        *,
        metadata: dict | None = None,
    ) -> None:
        metadata = {**(metadata or {}), "event_id": event.event_id}
        if event.event_type == EventType.FINAL_OUTPUT:
            text = str(event.payload.get("text", event.payload.get("output", "")))
            card = build_final_output_card(
                task_id=event.task_id,
                text=text,
                agent_name=event.payload.get("agent_name"),
                max_chars=self.max_message_chars,
            )
            await self._send_card(session_id, card, metadata)
            self._status_cards.pop(session_id, None)
            return
        if event.event_type == EventType.STATUS_CHANGED:
            status = str(event.payload.get("status") or "")
            if status in _RUNNING_STATUSES or status in _TERMINAL_STATUSES:
                await self._upsert_status_card(session_id, event, status, metadata)
                return
        content = self._format_event(event)
        if content:
            await self.send_reply(
                session_id,
                content,
                metadata={**metadata, "event_id": event.event_id},
            )

    async def _upsert_status_card(
        self,
        session_id: str,
        event: AgentEvent,
        status: str,
        metadata: dict,
    ) -> None:
        """Create or update the per-session task status card.

        Running cards may carry a cancel button; terminal cards drop it. When
        an in-place update fails we fall back to sending a fresh card message
        so the status is never silently lost.
        """
        card = build_status_card(
            task_id=event.task_id,
            status=status,
            agent_name=event.payload.get("agent_name"),
            allow_cancel_button=self.allow_cancel_button,
        )
        card_message_id = self._status_cards.get(session_id)
        if card_message_id is not None:
            updated = await self._update_card(card_message_id, card)
            if updated:
                if status in _TERMINAL_STATUSES:
                    self._status_cards.pop(session_id, None)
                return
        new_message_id = await self._send_card(session_id, card, metadata)
        if new_message_id and status in _RUNNING_STATUSES:
            self._status_cards[session_id] = new_message_id
            self._status_cards.move_to_end(session_id)
            while len(self._status_cards) > self._route_cache_size:
                self._status_cards.popitem(last=False)
        elif status in _TERMINAL_STATUSES:
            self._status_cards.pop(session_id, None)

    async def _send_card(self, session_id: str, card: dict, metadata: dict) -> str | None:
        route = self._routes.get(session_id)
        message_id = str(
            metadata.get("reply_callback")
            or metadata.get("message_id")
            or (route[0] if route else "")
        )
        chat_id = str(metadata.get("chat_id") or (route[1] if route else ""))
        if self.reply_mode == "reply_message" and not message_id:
            raise ImChannelError(
                "Feishu reply target is unavailable",
                context={"channel_id": self.channel_id, "session_id": session_id},
            )
        if self.reply_mode == "send_to_chat" and not chat_id:
            raise ImChannelError(
                "Feishu chat target is unavailable",
                context={"channel_id": self.channel_id, "session_id": session_id},
            )
        data = await self._post_message(
            msg_type="interactive",
            content_obj=card,
            message_id=message_id,
            chat_id=chat_id,
        )
        payload = (data or {}).get("data") or {}
        return str(payload.get("message_id") or "") or None

    async def _send_text(self, *, message_id: str, chat_id: str, content: str) -> None:
        await self._post_message(
            msg_type="text",
            content_obj={"text": content},
            message_id=message_id,
            chat_id=chat_id,
        )

    async def _post_message(
        self,
        *,
        msg_type: str,
        content_obj: dict,
        message_id: str,
        chat_id: str,
    ) -> dict:
        token = await self._get_token()
        response = await self._message_request(
            token=token,
            msg_type=msg_type,
            content_obj=content_obj,
            message_id=message_id,
            chat_id=chat_id,
        )
        data = self._response_json(response)
        if response.status_code == 401 or data.get("code") in _TOKEN_ERROR_CODES:
            token = await self._get_token(force_refresh=True)
            response = await self._message_request(
                token=token,
                msg_type=msg_type,
                content_obj=content_obj,
                message_id=message_id,
                chat_id=chat_id,
            )
            data = self._response_json(response)
        if response.is_error or data.get("code", 0) != 0:
            raise ImChannelError(
                "Feishu message API rejected the reply",
                context={
                    "status_code": response.status_code,
                    "code": data.get("code"),
                    "message": data.get("msg"),
                },
            )
        return data

    async def _update_card(self, message_id: str, card: dict) -> bool:
        """Update a previously sent interactive card in place.

        Returns False when the update fails (e.g. the card was deleted or
        expired); callers are expected to fall back to sending a new message.
        """
        try:
            client = self._require_client()
            token = await self._get_token()
            target = quote(message_id, safe="")
            response = await client.patch(
                f"{self.base_url}/open-apis/im/v1/messages/{target}",
                headers={"Authorization": f"Bearer {token}"},
                json={"content": json.dumps(card, ensure_ascii=False)},
            )
            data = self._response_json(response)
            if response.is_error or data.get("code", 0) != 0:
                raise ImChannelError(
                    "Feishu card update failed",
                    context={
                        "status_code": response.status_code,
                        "code": data.get("code"),
                        "message": data.get("msg"),
                    },
                )
            return True
        except ImChannelError:
            self._logger.warning(
                "Feishu status card update failed; falling back to a new message",
                extra={"channel_id": self.channel_id},
            )
            return False

    async def _message_request(
        self,
        *,
        token: str,
        msg_type: str,
        content_obj: dict,
        message_id: str,
        chat_id: str,
    ) -> httpx.Response:
        client = self._require_client()
        body = {
            "msg_type": msg_type,
            "content": json.dumps(content_obj, ensure_ascii=False),
            "uuid": str(uuid4()),
        }
        headers = {"Authorization": f"Bearer {token}"}
        if self.reply_mode == "reply_message":
            target = quote(message_id, safe="")
            return await client.post(
                f"{self.base_url}/open-apis/im/v1/messages/{target}/reply",
                headers=headers,
                json=body,
            )
        body["receive_id"] = chat_id
        return await client.post(
            f"{self.base_url}/open-apis/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers=headers,
            json=body,
        )

    async def _get_token(self, *, force_refresh: bool = False) -> str:
        now = time.monotonic()
        if not force_refresh and self._token and now < self._token_expires_at:
            return self._token
        async with self._token_lock:
            now = time.monotonic()
            if not force_refresh and self._token and now < self._token_expires_at:
                return self._token
            response = await self._require_client().post(
                f"{self.base_url}/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
            )
            data = self._response_json(response)
            token = data.get("tenant_access_token")
            if response.is_error or data.get("code", 0) != 0 or not token:
                raise ImChannelAuthError(
                    "Failed to obtain Feishu tenant_access_token",
                    context={
                        "status_code": response.status_code,
                        "code": data.get("code"),
                        "message": data.get("msg"),
                    },
                )
            expires_in = max(60, int(data.get("expire") or 7200) - 60)
            self._token = str(token)
            self._token_expires_at = now + expires_in
            return self._token

    # -------------------------------------------------------------- inbound

    def _to_im_message(self, payload: dict[str, Any]) -> ImMessage | None:
        header = payload.get("header")
        event = payload.get("event")
        if not isinstance(header, dict) or not isinstance(event, dict):
            raise ImChannelProtocolError("Feishu message event is missing header or event")
        sender = event.get("sender")
        message = event.get("message")
        if not isinstance(sender, dict) or not isinstance(message, dict):
            raise ImChannelProtocolError("Feishu message event has invalid sender or message")
        if sender.get("sender_type") in {"app", "bot"}:
            return None
        if message.get("message_type") != "text":
            return None

        chat_id = str(message.get("chat_id") or "")
        message_id = str(message.get("message_id") or "")
        sender_id = sender.get("sender_id") or {}
        sender_open_id = str(sender_id.get("open_id") or sender_id.get("user_id") or "")
        if not chat_id or not message_id or not sender_open_id:
            raise ImChannelProtocolError("Feishu message event has incomplete identifiers")
        if self.allowed_chats and chat_id not in self.allowed_chats:
            return None
        if self.allowed_senders and sender_open_id not in self.allowed_senders:
            return None

        mentions = message.get("mentions") or []
        if not isinstance(mentions, list):
            raise ImChannelProtocolError("Feishu message mentions must be a list")
        if (
            message.get("chat_type") == "group"
            and self.respond_to_group_mentions_only
            and not mentions
        ):
            return None
        try:
            content_payload = json.loads(message.get("content") or "{}")
        except json.JSONDecodeError as exc:
            raise ImChannelProtocolError("Feishu text content is not valid JSON") from exc
        if not isinstance(content_payload, dict):
            raise ImChannelProtocolError("Feishu text content must be a JSON object")
        content = str(content_payload.get("text") or "")
        for mention in mentions:
            if isinstance(mention, dict) and mention.get("key"):
                content = content.replace(str(mention["key"]), "")
        content = content.strip()
        if not content:
            return None

        session_id = self._session_id(message, chat_id, sender_open_id, message_id)
        self._routes[session_id] = (message_id, chat_id)
        self._routes.move_to_end(session_id)
        while len(self._routes) > self._route_cache_size:
            self._routes.popitem(last=False)
        event_id = str(header.get("event_id") or message_id)
        return ImMessage(
            channel_id=self.channel_id,
            session_id=session_id,
            sender=sender_open_id,
            content=content,
            msg_type="text",
            reply_callback=message_id,
            metadata={
                "event_id": event_id,
                "message_id": message_id,
                "chat_id": chat_id,
                "chat_type": message.get("chat_type"),
                "root_id": message.get("root_id"),
                "parent_id": message.get("parent_id"),
                "tenant_key": sender.get("tenant_key"),
            },
        )

    def _session_id(
        self,
        message: dict[str, Any],
        chat_id: str,
        sender_open_id: str,
        message_id: str,
    ) -> str:
        if message.get("chat_type") != "group" or self.group_session_scope == "chat":
            return chat_id
        if self.group_session_scope == "sender":
            return f"{chat_id}:{sender_open_id}"
        return f"{chat_id}:{message.get('root_id') or message_id}"

    def _to_im_action(self, payload: dict[str, Any]) -> Any | None:
        """Parse card action callbacks across Feishu schema 1.0 and 2.0."""
        header = payload.get("header") or {}
        event = payload.get("event") or {}
        if not isinstance(event, dict):
            event = {}
        operator = event.get("operator") or payload.get("operator") or {}
        if not isinstance(operator, dict):
            operator = {}
        action = event.get("action") or payload.get("action") or {}
        value = action.get("value") if isinstance(action, dict) else None
        if not isinstance(value, dict):
            return None
        task_id = str(value.get("task_id") or "")
        action_name = str(value.get("action") or "cancel")
        if not task_id:
            return None
        from auto_agent.models import ImAction

        sender_open_id = str(operator.get("open_id") or "")
        context = event.get("context") or {}
        chat_id = ""
        if isinstance(context, dict):
            chat_id = str(context.get("open_chat_id") or "")
        session_id = chat_id or sender_open_id or task_id
        return ImAction(
            channel_id=self.channel_id,
            session_id=session_id,
            sender=sender_open_id,
            action=action_name,
            task_id=task_id,
            message_id=str(action.get("open_message_id") or "") or None,
            chat_id=chat_id or None,
            metadata={"raw_event_id": str(header.get("event_id") or "")},
        )

    def _format_event(self, event: AgentEvent) -> str:
        if event.event_type == EventType.FINAL_OUTPUT:
            return str(event.payload.get("text", event.payload.get("output", "")))
        if event.event_type == EventType.ERROR:
            return f"执行失败：{event.payload.get('message', 'unknown error')}"
        if event.event_type == EventType.STATUS_CHANGED and event.payload.get("status") in {
            "cancelled",
            "timed_out",
        }:
            return f"任务状态：{event.payload.get('status')}"
        if self.stream_events and event.event_type in {EventType.STDOUT, EventType.STDERR}:
            return str(event.payload.get("text", ""))
        return ""

    def _split_content(self, content: str) -> list[str]:
        return [
            content[index : index + self.max_message_chars]
            for index in range(0, len(content), self.max_message_chars)
        ]

    # ------------------------------------------------------- response helpers

    @staticmethod
    def _response_json(response: httpx.Response) -> dict[str, Any]:
        try:
            return response.json()
        except Exception:
            return {"code": response.status_code, "msg": "non-JSON response"}

    # ------------------------------------------------------- dispatch infra

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None or self._closed:
            raise ImChannelError("Feishu channel is not connected")
        return self._client

    def _start_dispatch(self, message: ImMessage) -> None:
        self._require_started()
        task = asyncio.create_task(self.dispatch_message(message))
        self._dispatch_tasks.add(task)
        task.add_done_callback(self._dispatch_done)

    def _start_dispatch_action(self, action) -> None:
        self._require_started()
        task = asyncio.create_task(self.dispatch_action(action))
        self._dispatch_tasks.add(task)
        task.add_done_callback(self._dispatch_done)

    def _require_started(self) -> None:
        if self._closed:
            raise ImChannelError("Feishu channel is not connected")

    def _dispatch_done(self, task: asyncio.Task[None]) -> None:
        self._dispatch_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            self._logger.exception("Feishu message dispatch failed")
