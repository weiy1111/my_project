from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.stdio import stdio_server

from auto_agent.config import load_config
from auto_agent.exceptions import SkillConfigurationError
from auto_agent.skills.executor import SkillExecutor
from auto_agent.skills.models import SkillExecutionContext
from auto_agent.skills.registry import build_skill_registry

logger = logging.getLogger(__name__)


def build_mcp_server(executor: SkillExecutor, allowed: set[str]) -> Server:
    server = Server("auto_agent_skills", version="0.1.0")
    unknown = allowed - executor.registry.names()
    if unknown:
        raise SkillConfigurationError(
            "MCP 请求了未注册的 Skill", context={"skills": sorted(unknown)}
        )

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=item.name,
                description=item.description,
                inputSchema=item.input_schema,
                outputSchema=item.output_schema,
            )
            for item in executor.registry.list(allowed)
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> Any:
        if name not in allowed:
            raise SkillConfigurationError("Agent 无权调用该 Skill", context={"skill_name": name})
        result = await executor.execute(
            name,
            arguments,
            context=SkillExecutionContext(
                task_id=os.environ.get("AUTO_AGENT_TASK_ID"),
                agent_name=os.environ.get("AUTO_AGENT_AGENT_NAME"),
                workspace_id=os.environ.get("AUTO_AGENT_WORKSPACE_ID"),
                source=os.environ.get("AUTO_AGENT_TASK_SOURCE"),
            ),
        )
        if isinstance(result.output, dict):
            return result.output
        return [
            types.TextContent(
                type="text",
                text=(
                    result.output
                    if isinstance(result.output, str)
                    else json.dumps(result.output, ensure_ascii=False)
                ),
            )
        ]

    return server


async def _run(config_path: str, allowed: set[str]) -> None:
    config = load_config(config_path)
    if not config.skills.enabled:
        raise SkillConfigurationError("Skill 功能未启用")
    registry = build_skill_registry(config.skills)
    executor = SkillExecutor(registry, max_concurrency=config.skills.max_concurrency)
    server = build_mcp_server(executor, allowed)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(
                notification_options=NotificationOptions(),
                experimental_capabilities={},
            ),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve configured auto_agent Skills over MCP")
    parser.add_argument("--config", required=True)
    parser.add_argument("--allow", action="append", default=[])
    args = parser.parse_args()
    if not args.allow:
        raise SystemExit("at least one --allow Skill is required")
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    anyio.run(_run, args.config, set(args.allow))


if __name__ == "__main__":
    main()
