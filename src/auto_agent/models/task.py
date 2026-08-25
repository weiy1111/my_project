from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from auto_agent.models.enums import EventType, TaskSource, TaskStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MulticaTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    workspace_id: str = Field(min_length=1)
    agent_name: str | None = None
    prompt: str = Field(min_length=1)
    tool_overrides: dict[str, Any] = Field(default_factory=dict)
    timeout: float | None = Field(default=None, gt=0)
    webhook_url: HttpUrl | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class HermesExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_name: str = Field(default="default", min_length=1)
    agent_config: dict[str, Any] = Field(default_factory=dict)
    session_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    prompt: str = Field(min_length=1)
    sandbox_config: dict[str, Any] = Field(default_factory=dict)
    memory_enabled: bool = True
    tool_overrides: dict[str, Any] = Field(default_factory=dict)


class ImMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    channel_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    sender: str = Field(min_length=1)
    agent_name: str | None = None
    content: str = Field(min_length=1)
    msg_type: str = "text"
    metadata: dict[str, Any] = Field(default_factory=dict)
    reply_callback: str | None = None


class AgentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    event_type: EventType
    sequence: int = Field(ge=0)
    timestamp: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class TaskContext(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    task_id: str
    workspace_id: str
    source: TaskSource
    agent_name: str = "default"
    worker_name: str = "default"
    status: TaskStatus = TaskStatus.QUEUED
    hermes_session_id: str | None = None
    im_session_id: str | None = None
    request_id: str | None = None
    callback_url: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskResult(BaseModel):
    task_id: str
    status: TaskStatus
    output: str | None = None
    error: dict[str, Any] | None = None
