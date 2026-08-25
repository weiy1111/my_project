import asyncio
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from auto_agent.exceptions import HermesExecutionError, HermesProcessCrashError
from auto_agent.models import AgentEvent, EventType, HermesExecutionRequest, TaskStatus
from auto_agent.workers.base import BaseAgentWorker, TaskHandle


class HermesAgentWorker(BaseAgentWorker):
    """Subprocess adapter for a Hermes-compatible JSONL runtime.

    The exact Hermes command is intentionally injectable until its protocol is fixed.
    Each stdout JSON object should contain ``event_type`` and optional ``payload``.
    Non-JSON stdout is exposed as a stdout event.
    """

    def __init__(
        self,
        binary: str | Path,
        command_builder: Callable[[str, HermesExecutionRequest], list[str]] | None = None,
    ) -> None:
        self.binary = str(binary)
        self.command_builder = command_builder or self._default_command
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._statuses: dict[str, TaskStatus] = {}

    def _default_command(self, session_id: str, request: HermesExecutionRequest) -> list[str]:
        return [
            self.binary,
            "--session-id",
            session_id,
            "--prompt",
            request.prompt,
        ]

    async def start_task(self, task_id: str, request: HermesExecutionRequest) -> TaskHandle:
        if task_id in self._processes:
            raise HermesExecutionError(
                "task already has a running Hermes process", context={"task_id": task_id}
            )
        command = self.command_builder(request.session_id, request)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._processes[task_id] = process
        self._statuses[task_id] = TaskStatus.RUNNING
        return TaskHandle(task_id=task_id, hermes_session_id=request.session_id)

    async def cancel_task(self, task_id: str) -> None:
        process = self._processes.get(task_id)
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        self._statuses[task_id] = TaskStatus.CANCELLED

    async def get_status(self, task_id: str) -> TaskStatus:
        return self._statuses.get(task_id, TaskStatus.FAILED)

    async def stream_events(self, task_id: str) -> AsyncIterator[AgentEvent]:
        process = self._processes.get(task_id)
        if process is None or process.stdout is None or process.stderr is None:
            raise HermesExecutionError("unknown Hermes task", context={"task_id": task_id})

        sequence = 0
        stderr_task = asyncio.create_task(self._read_stderr(task_id, process.stderr))
        try:
            async for raw_line in process.stdout:
                line = raw_line.decode(errors="replace").rstrip("\n")
                event_type = EventType.STDOUT
                payload = {"text": line}
                try:
                    decoded = json.loads(line)
                    event_type = EventType(decoded.get("event_type", EventType.STDOUT))
                    payload = decoded.get("payload", decoded)
                except (json.JSONDecodeError, ValueError):
                    pass
                yield AgentEvent(
                    task_id=task_id, event_type=event_type, sequence=sequence, payload=payload
                )
                sequence += 1

            await process.wait()
            stderr_events = await stderr_task
            for event in stderr_events:
                event.sequence = sequence
                sequence += 1
                yield event
            if process.returncode != 0:
                self._statuses[task_id] = TaskStatus.FAILED
                raise HermesProcessCrashError(
                    "Hermes process exited with a non-zero code",
                    context={"task_id": task_id, "returncode": process.returncode},
                )
            self._statuses[task_id] = TaskStatus.COMPLETED
        finally:
            self._processes.pop(task_id, None)

    async def _read_stderr(self, task_id: str, stream: asyncio.StreamReader) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        sequence = 0
        async for raw_line in stream:
            events.append(
                AgentEvent(
                    task_id=task_id,
                    event_type=EventType.STDERR,
                    sequence=sequence,
                    payload={"text": raw_line.decode(errors="replace").rstrip("\n")},
                )
            )
            sequence += 1
        return events
