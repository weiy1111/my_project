from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from auto_agent.exceptions import HermesExecutionError
from auto_agent.models import AgentEvent, EventType, HermesExecutionRequest, TaskStatus
from auto_agent.workers.base import BaseAgentWorker, TaskHandle

_END_OF_STREAM = object()


@dataclass
class _CliTask:
    request: HermesExecutionRequest
    model: str
    cancelled: bool = False
    proc: asyncio.subprocess.Process | None = None
    consume_task: asyncio.Task[None] | None = None
    events: asyncio.Queue[AgentEvent | object] = field(default_factory=asyncio.Queue)
    response_text: list[str] = field(default_factory=list)
    error: Exception | None = None
    sequence: int = 0


class OpenCodeCliWorker(BaseAgentWorker):
    """通过调用 OpenCode CLI（opencode run）执行任务的 Worker。

    OpenCode 的自带免费模型（如 opencode/mimo-v2.5-free、opencode/gpt-5-nano）没有公开
    稳定的 HTTP API，但可以通过 CLI 的子进程方式稳定调用，且无需额外配置 API key。本 Worker
    以 `--format json` 运行 `opencode run`，解析其 NDJSON 事件流得到最终回复文本。

    支持的模型格式为 `provider/model`，例如 `opencode/mimo-v2.5-free`。
    """

    def __init__(
        self,
        model: str,
        *,
        command: str = "opencode",
        system_prompt: str | None = None,
        timeout: float = 300,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.model = model
        self.command = command
        self.system_prompt = system_prompt or ""
        self.timeout = timeout
        self.cwd = cwd
        self.env = env or {}
        self._tasks: dict[str, _CliTask] = {}
        self._statuses: dict[str, TaskStatus] = {}

    async def start_task(self, task_id: str, request: HermesExecutionRequest) -> TaskHandle:
        if task_id in self._tasks:
            raise HermesExecutionError(
                "task 已存在运行中的 OpenCode 任务", context={"task_id": task_id}
            )

        model = str(request.agent_config.get("model", self.model))
        system_prompt = str(
            request.agent_config.get("system_prompt", self.system_prompt)
        )
        prompt = self._build_prompt(request, system_prompt)

        task = _CliTask(request=request, model=model)
        self._tasks[task_id] = task
        self._statuses[task_id] = TaskStatus.RUNNING

        task.consume_task = asyncio.create_task(
            self._run_cli(task_id, task, prompt)
        )
        task.consume_task.add_done_callback(
            lambda _f: task.events.put_nowait(_END_OF_STREAM)
        )

        return TaskHandle(task_id=task_id, hermes_session_id=f"opencode-{task_id}")

    async def cancel_task(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            self._statuses[task_id] = TaskStatus.CANCELLED
            return
        task.cancelled = True
        if task.proc and task.proc.returncode is None:
            try:
                task.proc.kill()
            except ProcessLookupError:
                pass
        if task.consume_task and not task.consume_task.done():
            task.consume_task.cancel()
            try:
                await task.consume_task
            except (asyncio.CancelledError, Exception):
                pass
        self._statuses[task_id] = TaskStatus.CANCELLED
        self._tasks.pop(task_id, None)

    async def get_status(self, task_id: str) -> TaskStatus:
        return self._statuses.get(task_id, TaskStatus.FAILED)

    async def stream_events(self, task_id: str) -> AsyncIterator[AgentEvent]:
        task = self._tasks.get(task_id)
        if task is None:
            raise HermesExecutionError(
                "未知 OpenCode 任务", context={"task_id": task_id}
            )

        sequence = 0
        try:
            while True:
                event = await task.events.get()
                if event is _END_OF_STREAM:
                    break
                if isinstance(event, AgentEvent):
                    event.sequence = sequence
                    sequence += 1
                    yield event

            # 等待消费任务完成，检查是否有异常
            if task.consume_task:
                try:
                    await task.consume_task
                except asyncio.CancelledError:
                    self._statuses[task_id] = TaskStatus.CANCELLED
                    return
                except Exception as exc:
                    self._statuses[task_id] = TaskStatus.FAILED
                    raise HermesExecutionError(
                        "OpenCode CLI 调用失败",
                        context={"task_id": task_id, "error": str(exc)},
                    ) from exc

            if task.error is not None:
                self._statuses[task_id] = TaskStatus.FAILED
                raise HermesExecutionError(
                    "OpenCode CLI 调用失败",
                    context={
                        "task_id": task_id,
                        "error": str(task.error),
                    },
                ) from task.error

            if task.cancelled:
                self._statuses[task_id] = TaskStatus.CANCELLED
                return

            self._statuses[task_id] = TaskStatus.COMPLETED
            yield AgentEvent(
                task_id=task_id,
                event_type=EventType.FINAL_OUTPUT,
                sequence=sequence,
                payload={
                    "text": "".join(task.response_text),
                    "model": task.model,
                    "provider": "opencode_cli",
                },
            )
        except asyncio.CancelledError:
            raise
        except HermesExecutionError:
            self._statuses[task_id] = TaskStatus.FAILED
            raise
        except Exception as exc:
            self._statuses[task_id] = TaskStatus.FAILED
            raise HermesExecutionError(
                "OpenCode 执行中断",
                context={"task_id": task_id, "error": str(exc)},
            ) from exc
        finally:
            self._tasks.pop(task_id, None)

    async def shutdown(self) -> None:
        for task_id in list(self._tasks):
            await self.cancel_task(task_id)

    def _build_prompt(self, request: HermesExecutionRequest, system_prompt: str) -> str:
        if system_prompt and request.prompt:
            return f"{system_prompt}\n\n{request.prompt}"
        return request.prompt

    async def _run_cli(self, task_id: str, task: _CliTask, prompt: str) -> None:
        """运行 `opencode run --model <model> --format json <prompt>` 并解析 NDJSON。"""
        try:
            command = self._resolve_command()
        except FileNotFoundError as exc:
            task.error = exc
            return

        args = [
            command,
            "run",
            "--model",
            task.model,
            "--format",
            "json",
            prompt,
        ]

        env = _merged_env(self.env)
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                env=env,
            )
            task.proc = proc

            async def _read_stream(
                stream: asyncio.StreamReader | None, is_stderr: bool
            ) -> None:
                if stream is None:
                    return
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    text = line.decode(errors="replace").strip()
                    if not text:
                        continue
                    if is_stderr:
                        task.events.put_nowait(
                            AgentEvent(
                                task_id=task_id,
                                event_type=EventType.STDERR,
                                sequence=task.sequence,
                                payload={"text": text},
                            )
                        )
                        task.sequence += 1
                        continue
                    self._handle_stdout_line(task_id, task, text)

            stderr_task = asyncio.create_task(_read_stream(proc.stderr, True))
            stdout_task = asyncio.create_task(_read_stream(proc.stdout, False))

            try:
                returncode = await asyncio.wait_for(proc.wait(), timeout=self.timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                task.error = HermesExecutionError(
                    "OpenCode CLI 执行超时",
                    context={"task_id": task_id, "timeout": self.timeout},
                )
                return
            finally:
                await asyncio.gather(stderr_task, stdout_task, return_exceptions=True)

            if task.cancelled:
                return
            if returncode != 0:
                task.error = HermesExecutionError(
                    f"OpenCode CLI 返回非零退出码 {returncode}",
                    context={"task_id": task_id, "returncode": returncode},
                )
        except asyncio.CancelledError:
            if task.proc and task.proc.returncode is None:
                try:
                    task.proc.kill()
                except ProcessLookupError:
                    pass
            raise
        except Exception as exc:
            task.error = exc

    def _handle_stdout_line(self, task_id: str, task: _CliTask, line: str) -> None:
        """解析一行 NDJSON 事件；若是文本输出则入队并累加。"""
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return
        if data.get("type") != "text":
            return
        part = data.get("part")
        text = (part.get("text") or "").strip() if isinstance(part, dict) else ""
        if not text:
            return
        self._enqueue_text(task_id, task, text)

    def _enqueue_text(self, task_id: str, task: _CliTask, text: str) -> None:
        task.response_text.append(text)
        task.events.put_nowait(
            AgentEvent(
                task_id=task_id,
                event_type=EventType.STDOUT,
                sequence=task.sequence,
                payload={
                    "text": text,
                    "stream": "opencode_cli",
                    "model": task.model,
                },
            )
        )
        task.sequence += 1

    def _resolve_command(self) -> str:
        """解析命令可执行路径；找不到时报错。"""
        if "/" in self.command or "\\" in self.command or "." in self.command:
            if shutil.which(self.command):
                return self.command
            raise FileNotFoundError(f"opencode 命令不存在: {self.command}")
        resolved = shutil.which(self.command)
        if not resolved:
            raise FileNotFoundError(f"opencode 命令不存在: {self.command}")
        return resolved


def _merged_env(overrides: dict[str, str]) -> dict[str, str]:
    """合并进程环境变量：以 os.environ 为基底，用 overrides 覆盖。"""
    env = dict(os.environ)
    env.update(overrides)
    return env
