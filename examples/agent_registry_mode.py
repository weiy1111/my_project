import asyncio

from auto_agent.agents import AgentDefinition, AgentRegistry
from auto_agent.models import MulticaTaskRequest
from auto_agent.task_manager import TaskManager
from auto_agent.workers import FakeAgentWorker


async def main() -> None:
    registry = AgentRegistry(
        [
            AgentDefinition(
                name="reviewer",
                description="只读代码审查 Agent",
                system_prompt="检查代码风险并给出结论，不修改文件。",
                tools={"search": {}},
            )
        ],
        default_agent="reviewer",
    )
    manager = TaskManager(FakeAgentWorker(), agent_registry=registry)
    context = await manager.submit_multica(
        MulticaTaskRequest(
            workspace_id="demo",
            agent_name="reviewer",
            prompt="检查当前改动",
        )
    )
    await asyncio.sleep(0.05)
    print(manager.get(context.task_id).model_dump(mode="json"))


if __name__ == "__main__":
    asyncio.run(main())
