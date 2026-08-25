from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from auto_agent.agents import AgentDescriptor
from auto_agent.exceptions import AutoAgentError
from auto_agent.models import AgentEvent, MulticaTaskRequest, TaskContext
from auto_agent.gateways.multica import MulticaEventSink
from auto_agent.im_channels import BaseWebhookImChannel
from auto_agent.skills import SkillDescriptor
from auto_agent.task_manager import TaskManager


def create_app(
    task_manager: TaskManager,
    *,
    multica_sink: MulticaEventSink | None = None,
    components: list[object] | None = None,
    webhook_channels: list[BaseWebhookImChannel] | None = None,
) -> FastAPI:
    components = components or []
    webhook_channels = webhook_channels or []
    if multica_sink is not None:

        async def publish_event(event) -> None:
            context = task_manager.get(event.task_id)
            if event.event_type.value == "status_changed":
                await multica_sink.publish_status(context)
            else:
                await multica_sink.publish_event(event, context)

        task_manager.subscribe_all(publish_event)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if multica_sink is not None and hasattr(multica_sink, "start"):
            await multica_sink.start()
        for component in components:
            if hasattr(component, "start"):
                await component.start()
        try:
            yield
        finally:
            for component in reversed(components):
                if hasattr(component, "close"):
                    await component.close()
            await task_manager.shutdown()
            if multica_sink is not None and hasattr(multica_sink, "close"):
                await multica_sink.close()

    app = FastAPI(title="auto_agent", version="0.1.0", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/agents", response_model=list[AgentDescriptor])
    async def list_agents(include_disabled: bool = False) -> list[AgentDescriptor]:
        return task_manager.agent_registry.list(include_disabled=include_disabled)

    @app.get("/v1/agents/{agent_name}", response_model=AgentDescriptor)
    async def get_agent(agent_name: str) -> AgentDescriptor:
        try:
            definition = task_manager.agent_registry.get(agent_name, require_enabled=False)
        except AutoAgentError as exc:
            raise HTTPException(status_code=404, detail=exc.as_dict()) from exc
        return AgentDescriptor(
            name=definition.name,
            description=definition.description,
            enabled=definition.enabled,
            worker_name=definition.worker_name,
            tools=sorted(definition.tools),
        )

    @app.get("/v1/skills", response_model=list[SkillDescriptor])
    async def list_skills() -> list[SkillDescriptor]:
        return task_manager.list_skills()

    @app.post("/v1/tasks", response_model=TaskContext, status_code=202)
    async def submit_task(request: MulticaTaskRequest) -> TaskContext:
        try:
            return await task_manager.submit_multica(request)
        except AutoAgentError as exc:
            raise HTTPException(
                status_code=409 if exc.code == "duplicate_task" else 400, detail=exc.as_dict()
            ) from exc

    @app.get("/v1/tasks/{task_id}", response_model=TaskContext)
    async def get_task(task_id: str) -> TaskContext:
        try:
            return task_manager.get(task_id)
        except AutoAgentError as exc:
            raise HTTPException(status_code=404, detail=exc.as_dict()) from exc

    @app.get("/v1/tasks/{task_id}/events", response_model=list[AgentEvent])
    async def get_events(task_id: str, after_sequence: int = -1) -> list[AgentEvent]:
        try:
            return task_manager.events(task_id, after_sequence=after_sequence)
        except AutoAgentError as exc:
            raise HTTPException(status_code=404, detail=exc.as_dict()) from exc

    @app.delete("/v1/tasks/{task_id}", response_model=TaskContext)
    async def cancel_task(task_id: str) -> TaskContext:
        try:
            return await task_manager.cancel(task_id)
        except AutoAgentError as exc:
            raise HTTPException(status_code=404, detail=exc.as_dict()) from exc

    registered_paths: set[str] = set()
    for channel in webhook_channels:
        if channel.webhook_path in registered_paths:
            raise ValueError(f"duplicate IM webhook path: {channel.webhook_path}")
        registered_paths.add(channel.webhook_path)

        def build_handler(webhook_channel: BaseWebhookImChannel):
            async def handle_webhook(request: Request) -> dict:
                try:
                    return await webhook_channel.handle_webhook(
                        await request.body(), request.headers
                    )
                except AutoAgentError as exc:
                    status_code = 401 if exc.code == "im_channel_auth_error" else 400
                    raise HTTPException(status_code=status_code, detail=exc.as_dict()) from exc

            return handle_webhook

        app.add_api_route(
            channel.webhook_path,
            build_handler(channel),
            methods=["POST"],
            name=f"{channel.channel_id}_webhook",
        )

    return app
