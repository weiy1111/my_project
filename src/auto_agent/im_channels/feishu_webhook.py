import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Mapping
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

import httpx

from auto_agent.exceptions import (
    ImChannelAuthError,
    ImChannelError,
    ImChannelProtocolError,
)
from auto_agent.im_channels.base import BaseWebhookImChannel
from auto_agent.models import AgentEvent, EventType, ImMessage

GroupSessionScope = Literal["chat", "sender", "thread"]
ReplyMode = Literal["reply_message", "send_to_chat"]


class FeishuWebhookChannel(BaseWebhookImChannel):
    """Feishu Open Platform event webhook and bot reply transport."""

    _TOKEN_ERROR_CODES = {99991661, 99991663, 99991664, 99991668}

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        verification_token: str,
        encrypt_key: str | None = None,
        channel_id: str = "feishu_webhook",
        webhook_path: str = "/v1/im/feishu/events",
        base_url: str = "https://open.feishu.cn",
        group_session_scope: GroupSessionScope = "sender",
        reply_mode: ReplyMode = "reply_message",
        respond_to_group_mentions_only: bool = True,
        stream_events: bool = False,
        max_message_chars: int = 8000,
        max_clock_skew_seconds: int = 300,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(channel_id, webhook_path)
        if not app_id or not app_secret or not verification_token:
            raise ValueError("Feishu app_id, app_secret and verification_token are required")
        if group_session_scope not in {"chat", "sender", "thread"}:
            raise ValueError(f"unsupported group_session_scope: {group_session_scope}")
        if reply_mode not in {"reply_message", "send_to_chat"}:
            raise ValueError(f"unsupported reply_mode: {reply_mode}")
        self.app_id = app_id
        self.app_secret = app_secret
        self.verification_token = verification_token
        self.encrypt_key = encrypt_key or ""
        self.base_url = base_url.rstrip("/")
        self.group_session_scope = group_session_scope
        self.reply_mode = reply_mode
        self.respond_to_group_mentions_only = respond_to_group_mentions_only
        self.stream_events = stream_events
        self.max_message_chars = max(1, max_message_chars)
        self.max_clock_skew_seconds = max(0, max_clock_skew_seconds)
        self._client = client
        self._owns_client = client is None
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()
        self._routes: OrderedDict[str, tuple[str, str]] = OrderedDict()
        self._route_cache_size = 4096
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

    async def handle_webhook(self, body: bytes, headers: Mapping[str, str]) -> dict:
        payload = self._decode_payload(body)
        event_type = self._event_type(payload)
        self._verify_token(payload)

        if event_type == "url_verification":
            challenge = payload.get("challenge")
            if not isinstance(challenge, str) or not challenge:
                raise ImChannelProtocolError("Feishu URL verification has no challenge")
            return {"challenge": challenge}

        self._verify_signature(body, headers)
        if event_type != "im.message.receive_v1":
            return {"code": 0}

        message = self._to_im_message(payload)
        if message is None:
            return {"code": 0}
        self._start_dispatch(message)
        return {"code": 0}

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
        content = self._format_event(event)
        if content:
            await self.send_reply(
                session_id,
                content,
                metadata={**(metadata or {}), "event_id": event.event_id},
            )

    def _decode_payload(self, body: bytes) -> dict[str, Any]:
        try:
            envelope = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ImChannelProtocolError("Feishu webhook body is not valid JSON") from exc
        if not isinstance(envelope, dict):
            raise ImChannelProtocolError("Feishu webhook body must be a JSON object")
        encrypted = envelope.get("encrypt")
        if not encrypted:
            return envelope
        if not self.encrypt_key:
            raise ImChannelAuthError("Feishu encrypted event received without encrypt_key")
        try:
            plaintext = self._decrypt(str(encrypted))
            payload = json.loads(plaintext)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ImChannelAuthError("Feishu event decryption failed") from exc
        if not isinstance(payload, dict):
            raise ImChannelProtocolError("Feishu decrypted event must be a JSON object")
        return payload

    def _decrypt(self, encrypted: str) -> bytes:
        try:
            from cryptography.hazmat.primitives import hashes, padding
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

            raw = base64.b64decode(encrypted, validate=True)
            if len(raw) <= 16 or len(raw) % 16:
                raise ValueError("invalid encrypted payload length")
            digest = hashes.Hash(hashes.SHA256())
            digest.update(self.encrypt_key.encode())
            key = digest.finalize()
            decryptor = Cipher(algorithms.AES(key), modes.CBC(raw[:16])).decryptor()
            padded = decryptor.update(raw[16:]) + decryptor.finalize()
            unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
            return unpadder.update(padded) + unpadder.finalize()
        except ImportError as exc:
            raise ImChannelError("cryptography is required for encrypted Feishu events") from exc

    def _verify_signature(self, body: bytes, headers: Mapping[str, str]) -> None:
        normalized = {key.lower(): value for key, value in headers.items()}
        timestamp = normalized.get("x-lark-request-timestamp", "")
        nonce = normalized.get("x-lark-request-nonce", "")
        signature = normalized.get("x-lark-signature", "")
        if not timestamp or not nonce or not signature:
            raise ImChannelAuthError("Feishu signature headers are missing")
        try:
            request_time = int(timestamp)
        except ValueError as exc:
            raise ImChannelAuthError("Feishu request timestamp is invalid") from exc
        if (
            self.max_clock_skew_seconds
            and abs(time.time() - request_time) > self.max_clock_skew_seconds
        ):
            raise ImChannelAuthError("Feishu request timestamp is outside the allowed window")
        expected = hashlib.sha256(
            (timestamp + nonce + self.encrypt_key).encode() + body
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ImChannelAuthError("Feishu request signature is invalid")

    def _verify_token(self, payload: dict[str, Any]) -> None:
        header = payload.get("header") or {}
        token = header.get("token") if isinstance(header, dict) else None
        token = token or payload.get("token")
        if not isinstance(token, str) or not hmac.compare_digest(token, self.verification_token):
            raise ImChannelAuthError("Feishu verification token is invalid")

    def _event_type(self, payload: dict[str, Any]) -> str:
        header = payload.get("header") or {}
        if isinstance(header, dict) and header.get("event_type"):
            return str(header["event_type"])
        return str(payload.get("type") or "")

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

    def _start_dispatch(self, message: ImMessage) -> None:
        if self._closed:
            raise ImChannelError("Feishu webhook channel is not connected")
        task = asyncio.create_task(self.dispatch_message(message))
        self._dispatch_tasks.add(task)
        task.add_done_callback(self._dispatch_done)

    def _dispatch_done(self, task: asyncio.Task[None]) -> None:
        self._dispatch_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            self._logger.exception("Feishu message dispatch failed")

    async def _send_text(self, *, message_id: str, chat_id: str, content: str) -> None:
        token = await self._get_token()
        response = await self._send_text_request(
            token=token,
            message_id=message_id,
            chat_id=chat_id,
            content=content,
        )
        data = self._response_json(response)
        if response.status_code == 401 or data.get("code") in self._TOKEN_ERROR_CODES:
            token = await self._get_token(force_refresh=True)
            response = await self._send_text_request(
                token=token,
                message_id=message_id,
                chat_id=chat_id,
                content=content,
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

    async def _send_text_request(
        self,
        *,
        token: str,
        message_id: str,
        chat_id: str,
        content: str,
    ) -> httpx.Response:
        client = self._require_client()
        body = {
            "msg_type": "text",
            "content": json.dumps({"text": content}, ensure_ascii=False),
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

    def _response_json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise ImChannelProtocolError(
                "Feishu API returned a non-JSON response",
                context={"status_code": response.status_code},
            ) from exc
        if not isinstance(data, dict):
            raise ImChannelProtocolError("Feishu API returned an invalid JSON response")
        return data

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None or self._closed:
            raise ImChannelError("Feishu webhook channel is not connected")
        return self._client

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
