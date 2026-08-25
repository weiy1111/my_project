import asyncio
from collections.abc import AsyncIterator

from auto_agent.models import AgentEvent, EventType, HermesExecutionRequest, TaskStatus
from auto_agent.workers.base import BaseAgentWorker, TaskHandle


class FakeAgentWorker(BaseAgentWorker):
    """Deterministic worker for examples and tests."""

    def __init__(self) -> None:
        self._events: dict[str, list[AgentEvent]] = {}
        self._statuses: dict[str, TaskStatus] = {}

    async def start_task(self, task_id: str, request: HermesExecutionRequest) -> TaskHandle:
        self._statuses[task_id] = TaskStatus.RUNNING
        self._events[task_id] = [
            AgentEvent(
                task_id=task_id,
                event_type=EventType.STDOUT,
                sequence=0,
                payload={"text": request.prompt},
            ),
            AgentEvent(
                task_id=task_id,
                event_type=EventType.FINAL_OUTPUT,
                sequence=1,
                payload={"text": "completed"},
            ),
        ]
        return TaskHandle(task_id=task_id, hermes_session_id=request.session_id)

    async def cancel_task(self, task_id: str) -> None:
        self._statuses[task_id] = TaskStatus.CANCELLED

    async def get_status(self, task_id: str) -> TaskStatus:
        return self._statuses.get(task_id, TaskStatus.FAILED)

    async def stream_events(self, task_id: str) -> AsyncIterator[AgentEvent]:
        for event in self._events.get(task_id, []):
            await asyncio.sleep(0)
            yield event
        self._statuses[task_id] = TaskStatus.COMPLETED
