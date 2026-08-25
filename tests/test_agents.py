import asyncio

from auto_agent.agents import AgentDefinition, AgentPermissionPolicy, AgentRegistry
from auto_agent.exceptions import AgentNotFoundError, AgentPolicyError
from auto_agent.models import HermesExecutionRequest, MulticaTaskRequest, TaskSource
from auto_agent.task_manager import TaskManager
from auto_agent.workers import FakeAgentWorker


class CapturingWorker(FakeAgentWorker):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[HermesExecutionRequest] = []

    async def start_task(self, task_id: str, request: HermesExecutionRequest):
        self.requests.append(request.model_copy(deep=True))
        return await super().start_task(task_id, request)


def build_registry() -> AgentRegistry:
    return AgentRegistry(
        [
            AgentDefinition(name="general", description="通用 Agent"),
            AgentDefinition(
                name="reviewer",
                description="审查 Agent",
                worker_name="review-worker",
                system_prompt="只进行审查，不修改文件。",
                runtime_config={"model": "test-model"},
                tools={"search": {"limit": 10}},
                permission_policy=AgentPermissionPolicy(
                    allowed_sources=[TaskSource.MULTICA],
                    allowed_workspaces=["ws-1"],
                    allow_unlisted_tools=False,
                ),
            ),
        ],
        default_agent="general",
    )


def test_registry_resolves_agent_and_routes_worker() -> None:
    async def run() -> None:
        default_worker = CapturingWorker()
        review_worker = CapturingWorker()
        manager = TaskManager(
            default_worker,
            agent_registry=build_registry(),
            workers={"review-worker": review_worker},
        )
        context = await manager.submit_multica(
            MulticaTaskRequest(
                workspace_id="ws-1",
                agent_name="reviewer",
                prompt="检查代码",
                tool_overrides={"search": {"limit": 20}},
            )
        )
        await asyncio.sleep(0.02)
        assert context.agent_name == "reviewer"
        assert context.worker_name == "review-worker"
        assert not default_worker.requests
        request = review_worker.requests[0]
        assert request.agent_name == "reviewer"
        assert request.agent_config["system_prompt"] == "只进行审查，不修改文件。"
        assert request.agent_config["model"] == "test-model"
        assert request.tool_overrides["search"]["limit"] == 20

    asyncio.run(run())


def test_registry_rejects_unknown_agent_and_policy_violation() -> None:
    registry = build_registry()
    try:
        registry.get("missing")
    except AgentNotFoundError:
        pass
    else:
        raise AssertionError("未知 Agent 未被拒绝")

    try:
        registry.resolve(
            agent_name="reviewer",
            source=TaskSource.IM,
            workspace_id="ws-1",
        )
    except AgentPolicyError:
        pass
    else:
        raise AssertionError("不允许的任务来源未被拒绝")

    try:
        registry.resolve(
            agent_name="reviewer",
            source=TaskSource.MULTICA,
            workspace_id="ws-1",
            tool_overrides={"shell": {}},
        )
    except AgentPolicyError:
        pass
    else:
        raise AssertionError("未声明工具未被拒绝")


def test_registry_list_does_not_expose_system_prompt() -> None:
    descriptors = build_registry().list()
    reviewer = next(item for item in descriptors if item.name == "reviewer")
    assert reviewer.tools == ["search"]
    assert "system_prompt" not in reviewer.model_dump()
