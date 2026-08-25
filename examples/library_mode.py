import asyncio

from auto_agent.models import AgentEvent, MulticaTaskRequest
from auto_agent.task_manager import TaskManager
from auto_agent.workers import FakeAgentWorker


async def main() -> None:
    manager = TaskManager(FakeAgentWorker())

    async def print_event(event: AgentEvent) -> None:
        print(event.event_type, event.payload)

    request = MulticaTaskRequest(workspace_id="demo", prompt="hello from library mode")
    context = await manager.submit_multica(request)
    manager.subscribe(context.task_id, print_event)
    await asyncio.sleep(0.05)
    print(manager.get(context.task_id).model_dump(mode="json"))


if __name__ == "__main__":
    asyncio.run(main())
