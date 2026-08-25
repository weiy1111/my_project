import asyncio

from auto_agent.exceptions import DuplicateTaskError
from auto_agent.http import create_app
from auto_agent.models import MulticaTaskRequest, TaskStatus
from auto_agent.task_manager import TaskManager
from auto_agent.workers import FakeAgentWorker


def test_task_manager_runs_fake_worker() -> None:
    async def run() -> None:
        manager = TaskManager(FakeAgentWorker(), default_timeout=1)
        context = await manager.submit_multica(
            MulticaTaskRequest(workspace_id="ws", prompt="hello")
        )
        await asyncio.sleep(0.01)
        result = manager.get(context.task_id)
        assert result.status == TaskStatus.COMPLETED
        assert result.result == {"text": "completed"}

    asyncio.run(run())


def test_duplicate_task_is_rejected() -> None:
    async def run() -> None:
        manager = TaskManager(FakeAgentWorker())
        request = MulticaTaskRequest(task_id="same", workspace_id="ws", prompt="hello")
        await manager.submit_multica(request)
        try:
            await manager.submit_multica(request)
        except DuplicateTaskError:
            return
        raise AssertionError("duplicate task was accepted")

    asyncio.run(run())


def test_app_has_health_endpoint() -> None:
    app = create_app(TaskManager(FakeAgentWorker()))
    paths = {route.path for route in app.routes}
    assert "/healthz" in paths
    assert "/v1/agents" in paths
    assert "/v1/agents/{agent_name}" in paths
    assert "/v1/skills" in paths
    assert "/v1/tasks" in paths
    assert "/v1/tasks/{task_id}/events" in paths
