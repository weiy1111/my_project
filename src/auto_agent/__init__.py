"""A business-agnostic bridge between task schedulers and agent runtimes."""

from auto_agent.agents import AgentDefinition, AgentPermissionPolicy, AgentRegistry
from auto_agent.models import (
    AgentEvent,
    EventType,
    HermesExecutionRequest,
    MulticaTaskRequest,
    TaskContext,
    TaskStatus,
)
from auto_agent.task_manager.manager import TaskManager

__all__ = [
    "AgentDefinition",
    "AgentEvent",
    "AgentPermissionPolicy",
    "AgentRegistry",
    "EventType",
    "HermesExecutionRequest",
    "MulticaTaskRequest",
    "TaskContext",
    "TaskManager",
    "TaskStatus",
]
