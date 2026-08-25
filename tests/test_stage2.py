import asyncio
import sys

import httpx

from auto_agent.exceptions import MulticaAuthError
from auto_agent.gateways.multica import HttpMulticaEventSink
from auto_agent.im_channels.base import BaseImChannel
from auto_agent.im_channels.bridge import ImTaskBridge
from auto_agent.models import AgentEvent, EventType, ImMessage, TaskContext, TaskSource, TaskStatus
from auto_agent.task_manager import TaskManager
from auto_agent.workers import FakeAgentWorker, HermesAgentWorker


class RecordingChannel(BaseImChannel):
    def __init__(self) -> None:
        super().__init__("test")
        self.replies: list[tuple[str, str]] = []
        self.reply_metadata: list[dict] = []
        self.events: list[AgentEvent] = []

    async def connect(self) -> None:
        return None

    async def send_reply(
        self, session_id: str, content: str, *, metadata: dict | None = None
    ) -> None:
        self.replies.append((session_id, content))
        self.reply_metadata.append(metadata or {})

    async def send_event(
        self,
        session_id: str,
        event: AgentEvent,
        *,
        metadata: dict | None = None,
    ) -> None:
        self.events.append(event)

    async def close(self) -> None:
        return None


def test_multica_http_sink_sends_structured_event() -> None:
    async def run() -> None:
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(204)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sink = HttpMulticaEventSink(token="secret", client=client)
        context = TaskContext(
            task_id="task-1",
            workspace_id="ws",
            source=TaskSource.MULTICA,
            status=TaskStatus.RUNNING,
            callback_url="https://multica.invalid/callback",
        )
        event = AgentEvent(
            task_id="task-1", event_type=EventType.STDOUT, sequence=0, payload={"text": "ok"}
        )
        await sink.publish_event(event, context)
        assert len(requests) == 1
        assert requests[0].headers["authorization"] == "Bearer secret"
        body = requests[0].content.decode()
        assert '"kind":"agent_event"' in body
        assert '"status":"in_progress"' in body
        await client.aclose()

    asyncio.run(run())


def test_multica_http_sink_surfaces_401() -> None:
    async def run() -> None:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(401)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sink = HttpMulticaEventSink(client=client, retries=0)
        context = TaskContext(
            task_id="task-1",
            workspace_id="ws",
            source=TaskSource.MULTICA,
            callback_url="https://multica.invalid/callback",
        )
        try:
            await sink.publish_status(context)
        except MulticaAuthError:
            pass
        else:
            raise AssertionError("401 was not surfaced")
        await client.aclose()

    asyncio.run(run())


def test_im_bridge_deduplicates_and_replies() -> None:
    async def run() -> None:
        channel = RecordingChannel()
        manager = TaskManager(FakeAgentWorker())
        bridge = ImTaskBridge(channel, manager, workspace_id="ws")
        message = ImMessage(
            channel_id="test",
            session_id="session-1",
            sender="user-1",
            content="hello",
            metadata={"message_id": "message-1"},
        )
        await bridge.handle_message(message)
        await bridge.handle_message(message)
        await asyncio.sleep(0.02)
        assert channel.replies == [("session-1", "已收到，任务开始执行。")]
        assert channel.reply_metadata == [{"message_id": "message-1"}]
        assert [event.event_type for event in channel.events][-1] == EventType.STATUS_CHANGED
        assert manager.get(next(iter(manager._contexts))).status == TaskStatus.COMPLETED

    asyncio.run(run())


def test_hermes_worker_reads_jsonl_and_stderr() -> None:
    async def run() -> None:
        script = (
            "import json,sys; "
            "print(json.dumps({'event_type':'final_output','payload':{'text':'done'}}), flush=True); "
            "print('warning', file=sys.stderr, flush=True)"
        )
        worker = HermesAgentWorker(
            sys.executable, command_builder=lambda _sid, _req: [sys.executable, "-c", script]
        )
        from auto_agent.models import HermesExecutionRequest

        await worker.start_task("task-1", HermesExecutionRequest(prompt="hello"))
        events = [event async for event in worker.stream_events("task-1")]
        assert events[0].event_type == EventType.FINAL_OUTPUT
        assert any(event.event_type == EventType.STDERR for event in events)
        assert await worker.get_status("task-1") == TaskStatus.COMPLETED

    asyncio.run(run())
