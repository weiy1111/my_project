from collections.abc import Iterable
from typing import Any

from auto_agent.agents.models import AgentDefinition, AgentDescriptor, ResolvedAgent
from auto_agent.exceptions import (
    AgentDisabledError,
    AgentNotFoundError,
    AgentPolicyError,
    DuplicateAgentError,
)
from auto_agent.models.enums import TaskSource


class AgentRegistry:
    """Agent 注册、选择和任务级配置解析中心。"""

    def __init__(
        self,
        definitions: Iterable[AgentDefinition] = (),
        *,
        default_agent: str | None = None,
    ) -> None:
        self._definitions: dict[str, AgentDefinition] = {}
        self._default_agent = default_agent
        for definition in definitions:
            self.register(definition)
        if not self._definitions:
            self.register(AgentDefinition(name="default", description="默认通用 Agent"))
        if self._default_agent is None:
            self._default_agent = next(iter(self._definitions))
        self._validate_default()

    @property
    def default_agent(self) -> str:
        assert self._default_agent is not None
        return self._default_agent

    def register(self, definition: AgentDefinition, *, replace: bool = False) -> None:
        if definition.name in self._definitions and not replace:
            raise DuplicateAgentError("Agent 已注册", context={"agent_name": definition.name})
        self._definitions[definition.name] = definition.model_copy(deep=True)

    def unregister(self, name: str) -> None:
        if name not in self._definitions:
            raise AgentNotFoundError("Agent 不存在", context={"agent_name": name})
        if name == self._default_agent:
            raise AgentPolicyError("不能删除默认 Agent", context={"agent_name": name})
        del self._definitions[name]

    def get(self, name: str | None = None, *, require_enabled: bool = True) -> AgentDefinition:
        selected = name or self.default_agent
        definition = self._definitions.get(selected)
        if definition is None:
            raise AgentNotFoundError("Agent 不存在", context={"agent_name": selected})
        if require_enabled and not definition.enabled:
            raise AgentDisabledError("Agent 已禁用", context={"agent_name": selected})
        return definition.model_copy(deep=True)

    def list(self, *, include_disabled: bool = False) -> list[AgentDescriptor]:
        return [
            AgentDescriptor(
                name=definition.name,
                description=definition.description,
                enabled=definition.enabled,
                worker_name=definition.worker_name,
                tools=sorted(definition.tools),
            )
            for definition in self._definitions.values()
            if include_disabled or definition.enabled
        ]

    def resolve(
        self,
        *,
        agent_name: str | None,
        source: TaskSource,
        workspace_id: str,
        tool_overrides: dict[str, Any] | None = None,
        runtime_overrides: dict[str, Any] | None = None,
        sandbox_overrides: dict[str, Any] | None = None,
        memory_enabled: bool | None = None,
    ) -> ResolvedAgent:
        definition = self.get(agent_name)
        policy = definition.permission_policy
        if source not in policy.allowed_sources:
            raise AgentPolicyError(
                "Agent 不允许该任务来源",
                context={"agent_name": definition.name, "source": source.value},
            )
        if policy.allowed_workspaces and workspace_id not in policy.allowed_workspaces:
            raise AgentPolicyError(
                "Agent 不允许该工作区",
                context={"agent_name": definition.name, "workspace_id": workspace_id},
            )

        task_tools = tool_overrides or {}
        if task_tools and not policy.allow_tool_overrides:
            raise AgentPolicyError(
                "Agent 不允许覆盖工具配置", context={"agent_name": definition.name}
            )
        unknown_tools = set(task_tools) - set(definition.tools)
        if unknown_tools and not policy.allow_unlisted_tools:
            raise AgentPolicyError(
                "任务请求了 Agent 未声明的工具",
                context={"agent_name": definition.name, "tools": sorted(unknown_tools)},
            )

        task_runtime = runtime_overrides or {}
        if task_runtime and not policy.allow_runtime_overrides:
            raise AgentPolicyError(
                "Agent 不允许运行时配置覆盖", context={"agent_name": definition.name}
            )
        task_sandbox = sandbox_overrides or {}
        if task_sandbox and not policy.allow_sandbox_overrides:
            raise AgentPolicyError(
                "Agent 不允许沙箱配置覆盖", context={"agent_name": definition.name}
            )
        if memory_enabled is not None and not policy.allow_memory_override:
            raise AgentPolicyError(
                "Agent 不允许记忆配置覆盖", context={"agent_name": definition.name}
            )

        agent_config = {**definition.runtime_config, **task_runtime}
        if definition.system_prompt:
            agent_config["system_prompt"] = definition.system_prompt
        return ResolvedAgent(
            definition=definition,
            agent_config=agent_config,
            tool_config=self._merge_nested(definition.tools, task_tools),
            sandbox_config={**definition.sandbox_config, **task_sandbox},
            memory_enabled=(
                definition.memory_enabled if memory_enabled is None else memory_enabled
            ),
        )

    def _validate_default(self) -> None:
        if self._default_agent not in self._definitions:
            raise AgentNotFoundError(
                "默认 Agent 不存在", context={"agent_name": self._default_agent}
            )

    @staticmethod
    def _merge_nested(base: dict[str, dict[str, Any]], overrides: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {name: dict(config) for name, config in base.items()}
        for name, override in overrides.items():
            if isinstance(override, dict) and isinstance(merged.get(name), dict):
                merged[name] = {**merged[name], **override}
            else:
                merged[name] = override
        return merged
