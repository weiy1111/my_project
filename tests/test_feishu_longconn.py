import asyncio
import json
import threading

from auto_agent.im_channels import FeishuLongConnectionChannel
from auto_agent.im_channels.cards import build_status_card
from auto_agent.models import ImMessage


class FakeWsClient:
    """Mimics lark.ws.Client: blocks inside start() until released."""

    def __init__(self, event_handler, stop_event: threading.Event) -> None:
        self.event_handler = event_handler
        self._stop = stop_event
        self.started = False

    def start(self) -> None:
        self.started = True
        self._stop.wait(timeout=10)

    def emit(self, payload: dict) -> None:
        self.event_handler._do_without_validation(json.dumps(payload).encode())


def _make_channel(**kwargs):
    stop = threading.Event()
    clients: list[FakeWsClient] = []

    def factory(event_handler):
        client = FakeWsClient(event_handler, stop)
        clients.append(client)
        return client

    channel = FeishuLongConnectionChannel(
        app_id="cli_a",
        app_secret="secret",
        client_factory=factory,
        **kwargs,
    )
    return channel, clients, stop


def _message_payload(chat_id: str = "oc_chat", open_id: str = "ou_user") -> dict:
    return {
        "header": {"event_type": "im.message.receive_v1", "event_id": "ev-1"},
        "event": {
            "sender": {
                "sender_type": "user",
                "sender_id": {"open_id": open_id},
                "tenant_key": "tk",
            },
            "message": {
                "chat_id": chat_id,
                "message_id": "om_1",
                "message_type": "text",
                "chat_type": "p2p",
                "content": "{\"text\":\"hello\"}",
            },
        },
    }


def _card_action_payload(task_id: str = "task-9") -> dict:
    return {
        "header": {"event_type": "card.action.trigger", "event_id": "ev-2"},
        "event": {
            "operator": {"open_id": "ou_user"},
            "action": {"value": {"task_id": task_id, "action": "cancel"}},
        },
    }


def test_ws_message_dispatched_with_session_routing() -> None:
    async def run() -> None:
        received: list[ImMessage] = []
        channel, clients, stop = _make_channel()
        channel.on_message(received.append)
        await channel.connect()
        try:
            for _ in range(100):
                if clients:
                    break
                await asyncio.sleep(0.02)
            assert clients and clients[0].started
            clients[0].emit(_message_payload())
            await asyncio.sleep(0.05)
        finally:
            await channel.close()
            stop.set()
        assert len(received) == 1
        message = received[0]
        assert message.content == "hello"
        assert message.session_id == "oc_chat"
        assert message.metadata["chat_id"] == "oc_chat"

    asyncio.run(run())


def test_ws_respects_chat_whitelist() -> None:
    async def run() -> None:
        received: list[ImMessage] = []
        channel, clients, stop = _make_channel(allowed_chats=["oc_ok"])
        channel.on_message(received.append)
        await channel.connect()
        try:
            for _ in range(100):
                if clients:
                    break
                await asyncio.sleep(0.02)
            clients[0].emit(_message_payload(chat_id="oc_ok"))
            clients[0].emit(_message_payload(chat_id="oc_denied"))
            await asyncio.sleep(0.05)
        finally:
            await channel.close()
            stop.set()
        assert len(received) == 1
        assert received[0].metadata["chat_id"] == "oc_ok"

    asyncio.run(run())


def test_ws_card_action_parses_to_im_action() -> None:
    async def run() -> None:
        actions = []
        channel, clients, stop = _make_channel()
        channel.on_action(actions.append)
        await channel.connect()
        try:
            for _ in range(100):
                if clients:
                    break
                await asyncio.sleep(0.02)
            clients[0].emit(_card_action_payload("task-42"))
            await asyncio.sleep(0.05)
        finally:
            await channel.close()
            stop.set()
        assert len(actions) == 1
        action = actions[0]
        assert action.task_id == "task-42"
        assert action.action == "cancel"
        assert action.sender == "ou_user"

    asyncio.run(run())


def test_long_connection_disables_cancel_button_by_default() -> None:
    async def run() -> None:
        channel, _clients, stop = _make_channel()
        await channel.connect()
        try:
            assert channel.allow_cancel_button is False
        finally:
            await channel.close()
            stop.set()

    asyncio.run(run())


def test_status_card_button_gated_by_allow_cancel_button() -> None:
    card = build_status_card(task_id="t1", status="running", allow_cancel_button=False)
    assert all(element["tag"] != "action" for element in card["elements"])
    enabled = build_status_card(task_id="t1", status="running", allow_cancel_button=True)
    assert any(element["tag"] == "action" for element in enabled["elements"])
