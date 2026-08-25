from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PythonSkillConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["python"] = "python"
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    description: str = ""
    enabled: bool = True
    factory: str = Field(pattern=r"^[a-zA-Z_][\w.]*:[a-zA-Z_][\w.]*$")
    timeout: float = Field(default=30, gt=0)
    required_env: list[str] = Field(default_factory=list)


class CliSkillConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["cli"] = "cli"
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    description: str = ""
    enabled: bool = True
    command: list[str] = Field(min_length=1)
    cwd: str | None = None
    timeout: float = Field(default=30, gt=0)
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "additionalProperties": True}
    )
    output_schema: dict[str, Any] | None = None
    output_format: Literal["json", "text"] = "json"
    environment: dict[str, str] = Field(default_factory=dict)
    secret_env: dict[str, str] = Field(default_factory=dict)
    pass_env: list[str] = Field(default_factory=lambda: ["PATH", "PYTHONPATH", "LANG", "LC_ALL"])
    max_output_bytes: int = Field(default=1_048_576, gt=0)


SkillDefinitionConfig = Annotated[
    PythonSkillConfig | CliSkillConfig,
    Field(discriminator="provider"),
]


class SkillsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    server_name: str = Field(default="auto-agent-skills", pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    max_concurrency: int = Field(default=4, gt=0)
    definitions: list[SkillDefinitionConfig] = Field(default_factory=list)


class SkillDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    provider: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    timeout: float
    required_env: list[str] = Field(default_factory=list)


class SkillExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invocation_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str | None = None
    agent_name: str | None = None
    workspace_id: str | None = None
    source: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invocation_id: str
    skill_name: str
    output: Any
    duration_ms: float = Field(ge=0)


class SkillEventType(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class SkillEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invocation_id: str
    skill_name: str
    event_type: SkillEventType
    timestamp: datetime = Field(default_factory=utc_now)
    duration_ms: float | None = Field(default=None, ge=0)
    error: dict[str, Any] | None = None
