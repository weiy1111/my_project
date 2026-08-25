from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Any

from auto_agent.exceptions import SkillConfigurationError
from auto_agent.skills.models import SkillsConfig
from auto_agent.skills.registry import SkillRegistry


class SkillMcpBridge:
    """Builds per-Agent ACP MCP descriptors for the internal Skill server."""

    def __init__(
        self,
        registry: SkillRegistry,
        config: SkillsConfig,
        *,
        config_path: str | Path,
        python_executable: str | None = None,
    ) -> None:
        self.registry = registry
        self.config = config
        self.config_path = str(Path(config_path).expanduser().resolve())
        self.python_executable = python_executable or sys.executable

    def build_server(
        self,
        *,
        allowed_tools: dict[str, Any],
        task_id: str,
        agent_name: str,
        workspace_id: str,
        source: str,
    ) -> dict[str, Any] | None:
        allowed = sorted(set(allowed_tools) & self.registry.names())
        if not allowed:
            return None
        digest = hashlib.sha256("\0".join(allowed).encode()).hexdigest()[:10]
        environment = self._environment(allowed)
        environment.update(
            {
                "AUTO_AGENT_TASK_ID": task_id,
                "AUTO_AGENT_AGENT_NAME": agent_name,
                "AUTO_AGENT_WORKSPACE_ID": workspace_id,
                "AUTO_AGENT_TASK_SOURCE": source,
            }
        )
        arguments = [
            "-m",
            "auto_agent.skills.mcp_server",
            "--config",
            self.config_path,
        ]
        for name in allowed:
            arguments.extend(["--allow", name])
        return {
            "name": f"{self.config.server_name}-{digest}",
            "command": self.python_executable,
            "args": arguments,
            "env": [{"name": name, "value": value} for name, value in sorted(environment.items())],
        }

    def _environment(self, allowed: list[str]) -> dict[str, str]:
        environment: dict[str, str] = {}
        for name in allowed:
            skill = self.registry.get(name)
            missing = [item for item in skill.required_env if not os.environ.get(item)]
            if missing:
                raise SkillConfigurationError(
                    "Skill 所需环境变量未设置",
                    context={"skill_name": name, "environment": sorted(missing)},
                )
            for env_name in skill.environment_names:
                if value := os.environ.get(env_name):
                    environment[env_name] = value
        return environment
