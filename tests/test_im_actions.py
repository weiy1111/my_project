import asyncio
import hashlib
import json
import time

import httpx

from auto_agent.exceptions import AutoAgentError
from auto_agent.im_channels import FeishuWebhookChannel, ImTaskBridge
from auto_agent.im_channels.base import BaseImChannel
from auto_agent.models import AgentEvent, EventType, ImAction


def _token_ok() -> dict:
    return {"code": 0, "tenant_access_token": "tk", "expire": 7200}


def _message_created(message_id: str) -> dict:
    return {"code": 0, "data": {"message_id": message_id}}


class RecordingTransport:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/auth/v3/tenant_access_token/internal"):
            return httpx.Response(200, json=_token_ok())
        if "/messages/" in path and request.method == "PATCH":
            return httpx.Response(200, json={"code": 0})
        return httpx.Response(200, json=_message_created("om_card_1"))


def _channel(transport: RecordingTransport) -> FeishuWebhookChannel:
    return FeishuWebhookChannel(
        app_id="cli_a",
        app_secret="secret",
        verification_token="token",
        client=httpx.AsyncClient(transport=httpx.MockTransport(transport.handler)),
    )


def _event(event_type: EventType, status: str | None = None, text: str = "") -> AgentEvent:
    payload: dict = {}
    if status is not None:
        payload["status"] = status
    if text:
        payload["text"] = text
    return AgentEvent(task_id="task-1", event_type=event_type, sequence=0, payload=payload)


async def _send(channel: FeishuWebhookChannel, event: AgentEvent) -> None:
    await channel.send_event(
        "session-1",
        event,
        metadata={"message_id": "om_origin", "chat_id": "oc_chat"},
    )


def test_final_output_sends_interactive_card() -> None:
    async def run() -> None:
        transport = RecordingTransport()
        channel = _channel(transport)
        await channel.connect()
        try:
            await _send(channel, _event(EventType.FINAL_OUTPUT, text="hello world"))
        finally:
            await channel.close()
        sends = [r for r in transport.requests if r.method == "POST" and "/reply" in r.url.path]
        assert len(sends) == 1
        body = json.loads(sends[0].content)
        assert body["msg_type"] == "interactive"
        card = json.loads(body["content"])
        assert any("hello world" in str(e.get("content", "")) for e in card["elements"])

    asyncio.run(run())


def test_status_card_created_then_patched_and_cleared() -> None:
    async def run() -> None:
        transport = RecordingTransport()
        channel = _channel(transport)
        await channel.connect()
        try:
            await _send(channel, _event(EventType.STATUS_CHANGED, status="running"))
            assert channel._status_cards["session-1"] == "om_card_1"
            await _send(channel, _event(EventType.STATUS_CHANGED, status="running"))
            await _send(channel, _event(EventType.STATUS_CHANGED, status="cancelled"))
            assert "session-1" not in channel._status_cards
        finally:
            await channel.close()

        patches = [r for r in transport.requests if r.method == "PATCH"]
        # first running creates (no PATCH); second running updates; cancelled updates then clears
        assert len(patches) == 2
        final_card = json.loads(json.loads(patches[-1].content)["content"])
        has_button = any(
            element.get("tag") == "action"
            for element in final_card.get("elements", [])
        )
        assert not has_button

    asyncio.run(run())


def test_patch_failure_falls_back_to_new_message() -> None:
    async def run() -> None:
        class FailingPatchTransport(RecordingTransport):
            def handler(self, request: httpx.Request) -> httpx.Response:
                if request.method == "PATCH":
                    self.requests.append(request)
                    return httpx.Response(200, json={"code": 230001})
                return super().handler(request)

        transport = FailingPatchTransport()
        channel = _channel(transport)
        await channel.connect()
        try:
            await _send(channel, _event(EventType.STATUS_CHANGED, status="running"))
            await _send(channel, _event(EventType.STATUS_CHANGED, status="cancelled"))
        finally:
            await channel.close()
        replies = [r for r in transport.requests if r.method == "POST" and "/reply" in r.url.path]
        assert len(replies) == 2  # initial create + fallback after failed patch

    asyncio.run(run())


def _signed_headers(body: bytes, key: str = "") -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = "nonce"
    signature = hashlib.sha256((timestamp + nonce + key).encode() + body).hexdigest()
    return {
        "X-Lark-Request-Timestamp": timestamp,
        "X-Lark-Request-Nonce": nonce,
        "X-Lark-Signature": signature,
    }


def test_card_action_callback_dispatches_im_action() -> None:
    async def run() -> None:
        received: list = []
        channel = _channel(RecordingTransport())
        await channel.connect()

        async def handler(action) -> None:
            received.append(action)

        channel.on_action(handler)
        payload = {
            "header": {
                "event_type": "card.action.trigger",
                "token": "token",
                "event_id": "ev-1",
            },
            "event": {
                "operator": {"open_id": "ou_user"},
                "action": {
                    "value": {"task_id": "task-9", "action": "cancel"},
                    "open_message_id": "om_card_1",
                },
            },
        }
        body = json.dumps(payload).encode()
        try:
            result = await channel.handle_webhook(body, _signed_headers(body))
            await asyncio.sleep(0.01)
        finally:
            await channel.close()
        assert result == {"code": 0}
        assert len(received) == 1
        action = received[0]
        assert action.task_id == "task-9"
        assert action.action == "cancel"
        assert action.sender == "ou_user"

    asyncio.run(run())


class _StubManager:
    def __init__(self, fail: bool = False) -> None:
        self.cancelled: list[str] = []
        self.fail = fail

    async def cancel(self, task_id: str):
        if self.fail:
            raise AutoAgentError("gone")
        self.cancelled.append(task_id)


class _ReplyOnlyChannel(BaseImChannel):
    def __init__(self) -> None:
        super().__init__("stub")
        self.replies: list[tuple[str, str]] = []

    async def connect(self) -> None:
        return None

    async def send_reply(self, session_id: str, content: str, *, metadata=None) -> None:
        self.replies.append((session_id, content))

    async def send_event(self, session_id, event, *, metadata=None) -> None:
        return None

    async def close(self) -> None:
        return None


def test_bridge_handle_action_cancels_and_replies() -> None:
    async def run() -> None:
        channel = _ReplyOnlyChannel()
        manager = _StubManager()
        bridge = ImTaskBridge(channel, manager, workspace_id="ws")

        action = ImAction(
            channel_id="stub",
            session_id="s1",
            sender="ou_u",
            action="cancel",
            task_id="t1",
        )
        await bridge.handle_action(action)
        assert manager.cancelled == ["t1"]
        assert channel.replies[-1] == ("s1", "任务已取消。")

        manager.fail = True
        await bridge.handle_action(action)
        assert channel.replies[-1] == ("s1", "任务已结束或不存在，无法取消。")

    asyncio.run(run())
