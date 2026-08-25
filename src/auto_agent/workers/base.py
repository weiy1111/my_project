from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass

from auto_agent.models import AgentEvent, HermesExecutionRequest, TaskStatus


@dataclass(frozen=True)
class TaskHandle:
    task_id: str
    hermes_session_id: str


class BaseAgentWorker(ABC):
    """Runtime adapter. It must not know about Multica or IM channels."""

    @abstractmethod
    async def start_task(self, task_id: str, request: HermesExecutionRequest) -> TaskHandle:
        raise NotImplementedError

    @abstractmethod
    async def cancel_task(self, task_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_status(self, task_id: str) -> TaskStatus:
        raise NotImplementedError

    @abstractmethod
    async def stream_events(self, task_id: str) -> AsyncIterator[AgentEvent]:
        raise NotImplementedError

    async def shutdown(self) -> None:
        """Release worker-level resources after all tasks have been cancelled."""
