import asyncio

from auto_agent.exceptions import DuplicateTaskError
from auto_agent.http import create_app
from auto_agent.models import MulticaTaskRequest, TaskStatus
from auto_agent.task_manager import TaskManager
from auto_agent.workers import FakeAgentWorker


def test_task_manager_runs_fake_worker() -> None:
    async def run() -> None:
        manager = TaskManager(FakeAgentWorker(), default_timeout=1)
        context = await manager.submit_multica(
            MulticaTaskRequest(workspace_id="ws", prompt="hello")
        )
        await asyncio.sleep(0.01)
        result = manager.get(context.task_id)
        assert result.status == TaskStatus.COMPLETED
        assert result.result == {"text": "completed"}

    asyncio.run(run())


def test_duplicate_task_is_rejected() -> None:
    async def run() -> None:
        manager = TaskManager(FakeAgentWorker())
        request = MulticaTaskRequest(task_id="same", workspace_id="ws", prompt="hello")
        await manager.submit_multica(request)
        try:
            await manager.submit_multica(request)
        except DuplicateTaskError:
            return
        raise AssertionError("duplicate task was accepted")

    asyncio.run(run())


def test_app_has_health_endpoint() -> None:
    app = create_app(TaskManager(FakeAgentWorker()))
    paths = {route.path for route in app.routes}
    assert "/healthz" in paths
    assert "/v1/agents" in paths
    assert "/v1/agents/{agent_name}" in paths
    assert "/v1/skills" in paths
    assert "/v1/tasks" in paths
    assert "/v1/tasks/{task_id}/events" in paths


def _feishu_payload(*, chat_id: str = "oc_chat", open_id: str = "ou_user") -> dict:
    return {
        "header": {"event_type": "im.message.receive_v1", "token": "token"},
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


def _webhook_channel(**kwargs):
    from auto_agent.im_channels import FeishuWebhookChannel

    return FeishuWebhookChannel(
        app_id="cli_a",
        app_secret="secret",
        verification_token="token",
        **kwargs,
    )


def test_feishu_whitelist_empty_allows_all() -> None:
    channel = _webhook_channel()
    message = channel._to_im_message(_feishu_payload())
    assert message is not None
    assert message.content == "hello"
    assert message.metadata["chat_id"] == "oc_chat"


def test_feishu_allowed_chats_filter() -> None:
    channel = _webhook_channel(allowed_chats=["oc_ok"])
    assert channel._to_im_message(_feishu_payload(chat_id="oc_ok")) is not None
    assert channel._to_im_message(_feishu_payload(chat_id="oc_other")) is None


def test_feishu_allowed_senders_filter() -> None:
    channel = _webhook_channel(allowed_senders=["ou_ok"])
    assert channel._to_im_message(_feishu_payload(open_id="ou_ok")) is not None
    assert channel._to_im_message(_feishu_payload(open_id="ou_denied")) is None
