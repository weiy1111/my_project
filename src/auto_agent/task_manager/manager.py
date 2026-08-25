import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from auto_agent.agents import AgentRegistry
from auto_agent.exceptions import (
    AgentWorkerNotFoundError,
    DuplicateTaskError,
    TaskCancelledError,
    TaskNotFoundError,
    TaskTimeoutError,
)
from auto_agent.models import (
    AgentEvent,
    EventType,
    HermesExecutionRequest,
    MulticaTaskRequest,
    TaskContext,
    TaskSource,
    TaskStatus,
)
from auto_agent.skills import SkillDescriptor, SkillMcpBridge
from auto_agent.workers import BaseAgentWorker

logger = logging.getLogger(__name__)
EventSink = Callable[[AgentEvent], Awaitable[None]]


class TaskManager:
    """In-memory task coordinator shared by all input adapters."""

    def __init__(
        self,
        worker: BaseAgentWorker,
        *,
        agent_registry: AgentRegistry | None = None,
        workers: dict[str, BaseAgentWorker] | None = None,
        skill_mcp_bridge: SkillMcpBridge | None = None,
        max_concurrency: int = 4,
        default_timeout: float = 1800,
    ) -> None:
        self.worker = worker
        self.agent_registry = agent_registry or AgentRegistry()
        self._workers = {"default": worker, **(workers or {})}
        self.skill_mcp_bridge = skill_mcp_bridge
        self.default_timeout = default_timeout
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._contexts: dict[str, TaskContext] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._sinks: dict[str, list[EventSink]] = {}
        self._global_sinks: list[EventSink] = []
        self._event_history: dict[str, list[AgentEvent]] = {}
        self._event_sequences: dict[str, int] = {}
        self._task_workers: dict[str, BaseAgentWorker] = {}
        self._lock = asyncio.Lock()

    async def submit_multica(self, request: MulticaTaskRequest) -> TaskContext:
        selected_agent = request.agent_name or request.metadata.get("agent_name")
        memory_override = (
            request.metadata.get("memory_enabled") if "memory_enabled" in request.metadata else None
        )
        resolved = self.agent_registry.resolve(
            agent_name=selected_agent,
            source=TaskSource.MULTICA,
            workspace_id=request.workspace_id,
            tool_overrides=request.tool_overrides,
            runtime_overrides=request.metadata.get("agent_config", {}),
            sandbox_overrides=request.metadata.get("sandbox_config", {}),
            memory_enabled=memory_override,
        )
        agent_config = self._with_skill_server(
            resolved.agent_config,
            resolved.tool_config,
            task_id=request.task_id,
            agent_name=resolved.definition.name,
            workspace_id=request.workspace_id,
            source=TaskSource.MULTICA,
        )
        hermes_request = HermesExecutionRequest(
            agent_name=resolved.definition.name,
            prompt=request.prompt,
            tool_overrides=resolved.tool_config,
            agent_config=agent_config,
            sandbox_config=resolved.sandbox_config,
            memory_enabled=resolved.memory_enabled,
        )
        return await self._submit(
            task_id=request.task_id,
            workspace_id=request.workspace_id,
            source=TaskSource.MULTICA,
            agent_name=resolved.definition.name,
            worker_name=resolved.definition.worker_name,
            hermes_request=hermes_request,
            timeout=request.timeout,
            request_id=request.request_id,
            callback_url=str(request.webhook_url) if request.webhook_url else None,
            metadata=request.metadata,
        )

    async def submit_im(
        self,
        *,
        task_id: str,
        workspace_id: str,
        session_id: str,
        prompt: str,
        agent_name: str | None = None,
        request_id: str | None = None,
        metadata: dict | None = None,
    ) -> TaskContext:
        metadata = metadata or {}
        resolved = self.agent_registry.resolve(
            agent_name=agent_name or metadata.get("agent_name"),
            source=TaskSource.IM,
            workspace_id=workspace_id,
            tool_overrides=metadata.get("tool_overrides", {}),
            runtime_overrides=metadata.get("agent_config", {}),
            sandbox_overrides=metadata.get("sandbox_config", {}),
            memory_enabled=(
                metadata.get("memory_enabled") if "memory_enabled" in metadata else None
            ),
        )
        agent_config = self._with_skill_server(
            resolved.agent_config,
            resolved.tool_config,
            task_id=task_id,
            agent_name=resolved.definition.name,
            workspace_id=workspace_id,
            source=TaskSource.IM,
        )
        return await self._submit(
            task_id=task_id,
            workspace_id=workspace_id,
            source=TaskSource.IM,
            agent_name=resolved.definition.name,
            worker_name=resolved.definition.worker_name,
            hermes_request=HermesExecutionRequest(
                agent_name=resolved.definition.name,
                session_id=session_id,
                prompt=prompt,
                agent_config=agent_config,
                tool_overrides=resolved.tool_config,
                sandbox_config=resolved.sandbox_config,
                memory_enabled=resolved.memory_enabled,
            ),
            timeout=None,
            request_id=request_id,
            im_session_id=session_id,
            metadata=metadata,
        )

    def list_skills(self) -> list[SkillDescriptor]:
        if self.skill_mcp_bridge is None:
            return []
        return self.skill_mcp_bridge.registry.list()

    def _with_skill_server(
        self,
        agent_config: dict,
        tool_config: dict,
        *,
        task_id: str,
        agent_name: str,
        workspace_id: str,
        source: TaskSource,
    ) -> dict:
        merged = dict(agent_config)
        if self.skill_mcp_bridge is None:
            return merged
        server = self.skill_mcp_bridge.build_server(
            allowed_tools=tool_config,
            task_id=task_id,
            agent_name=agent_name,
            workspace_id=workspace_id,
            source=source.value,
        )
        if server is None:
            return merged
        configured = merged.get("mcp_servers", [])
        if not isinstance(configured, list):
            raise ValueError("agent runtime_config.mcp_servers 必须是列表")
        merged["mcp_servers"] = [*configured, server]
        return merged

    async def _submit(
        self,
        *,
        task_id: str,
        workspace_id: str,
        source: TaskSource,
        agent_name: str,
        worker_name: str,
        hermes_request: HermesExecutionRequest,
        timeout: float | None,
        request_id: str | None,
        callback_url: str | None = None,
        im_session_id: str | None = None,
        metadata: dict | None = None,
    ) -> TaskContext:
        worker = self._workers.get(worker_name)
        if worker is None:
            raise AgentWorkerNotFoundError(
                "Agent 对应的 Worker 未注册",
                context={"agent_name": agent_name, "worker_name": worker_name},
            )
        async with self._lock:
            if task_id in self._contexts:
                raise DuplicateTaskError("task_id already exists", context={"task_id": task_id})
            context = TaskContext(
                task_id=task_id,
                workspace_id=workspace_id,
                source=source,
                agent_name=agent_name,
                worker_name=worker_name,
                hermes_session_id=hermes_request.session_id,
                im_session_id=im_session_id,
                request_id=request_id,
                callback_url=callback_url,
                metadata=metadata or {},
            )
            self._contexts[task_id] = context
            self._task_workers[task_id] = worker
            self._tasks[task_id] = asyncio.create_task(
                self._run(context, hermes_request, worker, timeout)
            )
            return context.model_copy(deep=True)

    async def _run(
        self,
        context: TaskContext,
        request: HermesExecutionRequest,
        worker: BaseAgentWorker,
        timeout: float | None,
    ) -> None:
        try:
            async with self._semaphore:
                context.status = TaskStatus.RUNNING
                context.started_at = datetime.now(timezone.utc)
                await self._dispatch(self._status_event(context, TaskStatus.RUNNING))
                handle = await worker.start_task(context.task_id, request)
                context.hermes_session_id = handle.hermes_session_id
                stream = worker.stream_events(context.task_id)
                if timeout is None:
                    timeout = self.default_timeout
                await asyncio.wait_for(self._consume_events(context, stream), timeout=timeout)
                if context.status == TaskStatus.RUNNING:
                    context.status = TaskStatus.COMPLETED
        except asyncio.TimeoutError:
            context.status = TaskStatus.TIMED_OUT
            await worker.cancel_task(context.task_id)
            context.error = TaskTimeoutError(
                "task exceeded timeout", context={"task_id": context.task_id}
            ).as_dict()
            await self._dispatch(self._error_event(context, context.error))
        except asyncio.CancelledError:
            context.status = TaskStatus.CANCELLED
            context.error = TaskCancelledError(
                "task was cancelled", context={"task_id": context.task_id}
            ).as_dict()
            await self._dispatch(self._error_event(context, context.error))
        except Exception as exc:
            context.status = TaskStatus.FAILED
            if hasattr(exc, "as_dict"):
                context.error = exc.as_dict()
            else:
                context.error = {"code": "internal_error", "message": str(exc)}
            await self._dispatch(self._error_event(context, context.error))
            logger.exception("task failed", extra={"task_id": context.task_id})
        finally:
            context.finished_at = datetime.now(timezone.utc)
            await self._dispatch(self._status_event(context, context.status))

    async def _consume_events(self, context: TaskContext, stream) -> None:
        async for event in stream:
            if event.event_type == EventType.FINAL_OUTPUT:
                context.result = event.payload
            await self._dispatch(event)

    async def cancel(self, task_id: str) -> TaskContext:
        context = self._contexts.get(task_id)
        if context is None:
            raise TaskNotFoundError("task does not exist", context={"task_id": task_id})
        if context.status not in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.TIMED_OUT,
        }:
            worker = self._task_workers[task_id]
            await worker.cancel_task(task_id)
            context.status = TaskStatus.CANCELLED
            runner = self._tasks.get(task_id)
            if runner and runner is not asyncio.current_task() and not runner.done():
                runner.cancel()
                try:
                    await runner
                except asyncio.CancelledError:
                    pass
        return context.model_copy(deep=True)

    def get(self, task_id: str) -> TaskContext:
        context = self._contexts.get(task_id)
        if context is None:
            raise TaskNotFoundError("task does not exist", context={"task_id": task_id})
        return context.model_copy(deep=True)

    def subscribe(self, task_id: str, sink: EventSink) -> None:
        if task_id not in self._contexts:
            raise TaskNotFoundError("task does not exist", context={"task_id": task_id})
        self._sinks.setdefault(task_id, []).append(sink)

    def subscribe_all(self, sink: EventSink) -> None:
        """Subscribe a sink to every current and future task."""
        self._global_sinks.append(sink)

    def events(self, task_id: str, *, after_sequence: int = -1) -> list[AgentEvent]:
        if task_id not in self._contexts:
            raise TaskNotFoundError("task does not exist", context={"task_id": task_id})
        return [
            event.model_copy(deep=True)
            for event in self._event_history.get(task_id, [])
            if event.sequence > after_sequence
        ]

    async def shutdown(self) -> None:
        for task_id in list(self._tasks):
            task = self._tasks[task_id]
            if not task.done():
                await self.cancel(task_id)
        self._tasks.clear()
        for worker in {id(item): item for item in self._workers.values()}.values():
            await worker.shutdown()

    async def _dispatch(self, event: AgentEvent) -> None:
        sequence = self._event_sequences.get(event.task_id, 0)
        event = event.model_copy(update={"sequence": sequence})
        self._event_sequences[event.task_id] = sequence + 1
        history = self._event_history.setdefault(event.task_id, [])
        history.append(event)
        if len(history) > 256:
            del history[:-256]
        for sink in [*self._global_sinks, *self._sinks.get(event.task_id, [])]:
            try:
                await sink(event)
            except Exception:
                logger.exception("event sink failed", extra={"task_id": event.task_id})

    def _status_event(self, context: TaskContext, status: TaskStatus) -> AgentEvent:
        return AgentEvent(
            task_id=context.task_id,
            event_type=EventType.STATUS_CHANGED,
            sequence=0,
            payload={"status": status.value},
            request_id=context.request_id,
        )

    def _error_event(self, context: TaskContext, error: dict) -> AgentEvent:
        return AgentEvent(
            task_id=context.task_id,
            event_type=EventType.ERROR,
            sequence=0,
            payload=error,
            request_id=context.request_id,
        )
