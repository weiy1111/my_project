from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from auto_agent.models.enums import TaskSource


class AgentPermissionPolicy(BaseModel):
    """控制一个 Agent 可接受的来源、工作区和任务级覆盖。"""

    model_config = ConfigDict(extra="forbid")

    allowed_sources: list[TaskSource] = Field(
        default_factory=lambda: [TaskSource.MULTICA, TaskSource.IM]
    )
    allowed_workspaces: list[str] = Field(default_factory=list)
    allow_tool_overrides: bool = True
    allow_unlisted_tools: bool = True
    allow_runtime_overrides: bool = True
    allow_sandbox_overrides: bool = True
    allow_memory_override: bool = True


class AgentDefinition(BaseModel):
    """与执行引擎无关的 Agent 声明。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    description: str = ""
    enabled: bool = True
    worker_name: str = Field(default="default", min_length=1)
    system_prompt: str = ""
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    tools: dict[str, dict[str, Any]] = Field(default_factory=dict)
    memory_enabled: bool = True
    sandbox_config: dict[str, Any] = Field(default_factory=dict)
    permission_policy: AgentPermissionPolicy = Field(default_factory=AgentPermissionPolicy)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentDescriptor(BaseModel):
    """可通过 HTTP 安全公开的 Agent 摘要。"""

    name: str
    description: str
    enabled: bool
    worker_name: str
    tools: list[str]


class ResolvedAgent(BaseModel):
    definition: AgentDefinition
    agent_config: dict[str, Any]
    tool_config: dict[str, Any]
    sandbox_config: dict[str, Any]
    memory_enabled: bool
