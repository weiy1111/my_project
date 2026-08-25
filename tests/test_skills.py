import asyncio
import os
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import BaseModel

from auto_agent.agents import AgentDefinition, AgentRegistry
from auto_agent.exceptions import SkillExecutionError, SkillTimeoutError, SkillValidationError
from auto_agent.models import HermesExecutionRequest, MulticaTaskRequest
from auto_agent.skills import (
    CliSkill,
    CliSkillConfig,
    PythonSkillConfig,
    PythonSkill,
    SkillEventType,
    SkillExecutor,
    SkillMcpBridge,
    SkillRegistry,
    SkillsConfig,
    build_skill_registry,
)
from auto_agent.skills.mcp_server import build_mcp_server
from auto_agent.task_manager import TaskManager
from auto_agent.workers import FakeAgentWorker


class AddInput(BaseModel):
    left: int
    right: int


class AddOutput(BaseModel):
    total: int


def add_skill(*, timeout: float = 1) -> PythonSkill:
    async def add(arguments: AddInput, _context) -> AddOutput:
        return AddOutput(total=arguments.left + arguments.right)

    return PythonSkill(
        name="add_numbers",
        description="Add two integers",
        input_model=AddInput,
        output_model=AddOutput,
        handler=add,
        timeout=timeout,
    )


class CapturingWorker(FakeAgentWorker):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[HermesExecutionRequest] = []

    async def start_task(self, task_id: str, request: HermesExecutionRequest):
        self.requests.append(request.model_copy(deep=True))
        return await super().start_task(task_id, request)


def write_cli_skill(tmp_path: Path) -> Path:
    script = tmp_path / "echo_skill.py"
    script.write_text(
        "import json,sys\n"
        "request=json.load(sys.stdin)\n"
        "print(json.dumps({'echo': request['arguments']['text']}))\n",
        encoding="utf-8",
    )
    return script


def cli_config(script: Path) -> CliSkillConfig:
    return CliSkillConfig(
        name="echo_json",
        description="Echo text through a fixed CLI",
        command=[sys.executable, script.as_posix()],
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string", "minLength": 1}},
            "required": ["text"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"echo": {"type": "string"}},
            "required": ["echo"],
            "additionalProperties": False,
        },
    )


def test_python_skill_validates_input_and_emits_events() -> None:
    async def run() -> None:
        events = []
        executor = SkillExecutor(SkillRegistry([add_skill()]))

        async def record(event) -> None:
            events.append(event)

        executor.subscribe(record)
        result = await executor.execute("add_numbers", {"left": 2, "right": 5})
        assert result.output == {"total": 7}
        assert [event.event_type for event in events] == [
            SkillEventType.STARTED,
            SkillEventType.COMPLETED,
        ]

        try:
            await executor.execute("add_numbers", {"left": "invalid", "right": 1})
        except SkillValidationError:
            pass
        else:
            raise AssertionError("invalid Python Skill input was accepted")

    asyncio.run(run())


def test_skill_executor_enforces_timeout() -> None:
    async def run() -> None:
        class EmptyInput(BaseModel):
            pass

        async def wait_forever(_arguments, _context):
            await asyncio.sleep(1)
            return {}

        skill = PythonSkill(
            name="slow_skill",
            description="Slow test Skill",
            input_model=EmptyInput,
            output_model=None,
            handler=wait_forever,
            timeout=0.01,
        )
        executor = SkillExecutor(SkillRegistry([skill]))
        try:
            await executor.execute("slow_skill", {})
        except SkillTimeoutError:
            pass
        else:
            raise AssertionError("Skill timeout was not enforced")

    asyncio.run(run())


def test_cli_skill_uses_json_stdio_and_validates_schema(tmp_path: Path) -> None:
    async def run() -> None:
        executor = SkillExecutor(SkillRegistry([CliSkill(cli_config(write_cli_skill(tmp_path)))]))
        result = await executor.execute("echo_json", {"text": "hello"})
        assert result.output == {"echo": "hello"}
        try:
            await executor.execute("echo_json", {"text": "", "extra": "denied"})
        except SkillValidationError:
            pass
        else:
            raise AssertionError("invalid CLI Skill input was accepted")

    asyncio.run(run())


def test_cli_skill_rejects_large_output_without_deadlocking(tmp_path: Path) -> None:
    async def run() -> None:
        script = tmp_path / "large_output.py"
        script.write_text("print('x' * 200000)\n", encoding="utf-8")
        config = cli_config(script).model_copy(update={"max_output_bytes": 1024, "timeout": 1})
        executor = SkillExecutor(SkillRegistry([CliSkill(config)]))
        try:
            await executor.execute("echo_json", {"text": "hello"})
        except SkillExecutionError as exc:
            assert "输出超过限制" in str(exc)
        else:
            raise AssertionError("large CLI Skill output was accepted")

    asyncio.run(run())


def test_task_manager_exposes_only_registered_agent_skills(tmp_path: Path) -> None:
    async def run() -> None:
        worker = CapturingWorker()
        registry = SkillRegistry([add_skill()])
        skills_config = SkillsConfig(enabled=True, definitions=[])
        bridge = SkillMcpBridge(
            registry,
            skills_config,
            config_path=tmp_path / "config.yaml",
        )
        agents = AgentRegistry(
            [
                AgentDefinition(
                    name="calculator",
                    tools={"add_numbers": {}, "native_tool": {}},
                )
            ],
            default_agent="calculator",
        )
        manager = TaskManager(worker, agent_registry=agents, skill_mcp_bridge=bridge)
        await manager.submit_multica(
            MulticaTaskRequest(task_id="task-1", workspace_id="ws", prompt="add")
        )
        await asyncio.sleep(0.02)
        server = worker.requests[0].agent_config["mcp_servers"][0]
        assert server["name"].startswith("auto-agent-skills-")
        assert server["args"][-2:] == ["--allow", "add_numbers"]
        assert "native_tool" not in server["args"]
        env = {item["name"]: item["value"] for item in server["env"]}
        assert env["AUTO_AGENT_TASK_ID"] == "task-1"
        assert env["AUTO_AGENT_AGENT_NAME"] == "calculator"
        assert [item.name for item in manager.list_skills()] == ["add_numbers"]

    asyncio.run(run())


def test_python_skill_config_passes_required_environment_to_mcp(tmp_path: Path) -> None:
    factory_module = ModuleType("test_skill_factory")
    factory_module.add_skill = add_skill
    config = SkillsConfig(
        enabled=True,
        definitions=[
            PythonSkillConfig(
                name="add_numbers",
                description="Configured description",
                factory="test_skill_factory:add_skill",
                required_env=["TEST_SKILL_TOKEN"],
            )
        ],
    )
    with (
        patch.dict(sys.modules, {"test_skill_factory": factory_module}),
        patch.dict(os.environ, {"TEST_SKILL_TOKEN": "secret"}),
    ):
        registry = build_skill_registry(config)
        bridge = SkillMcpBridge(registry, config, config_path=tmp_path / "config.yaml")
        server = bridge.build_server(
            allowed_tools={"add_numbers": {}},
            task_id="task-1",
            agent_name="calculator",
            workspace_id="workspace-1",
            source="multica",
        )
    assert server is not None
    environment = {item["name"]: item["value"] for item in server["env"]}
    assert environment["TEST_SKILL_TOKEN"] == "secret"
    assert registry.get("add_numbers").description == "Configured description"


def test_mcp_server_lists_and_calls_cli_skill(tmp_path: Path) -> None:
    async def run() -> None:
        script = write_cli_skill(tmp_path)
        registry = SkillRegistry([CliSkill(cli_config(script))])
        server = build_mcp_server(
            SkillExecutor(registry),
            {"echo_json"},
        )
        async with create_connected_server_and_client_session(server) as session:
            tools = await session.list_tools()
            assert [tool.name for tool in tools.tools] == ["echo_json"]
            result = await session.call_tool("echo_json", {"text": "from-mcp"})
            assert not result.isError
            assert result.structuredContent == {"echo": "from-mcp"}

    asyncio.run(run())
