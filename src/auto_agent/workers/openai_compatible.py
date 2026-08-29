from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from auto_agent.exceptions import HermesExecutionError
from auto_agent.models import AgentEvent, EventType, HermesExecutionRequest, TaskStatus
from auto_agent.workers.base import BaseAgentWorker, TaskHandle

_END_OF_STREAM = object()


@dataclass
class _OpenAITask:
    request: HermesExecutionRequest
    model: str
    api_base: str
    api_key: str
    cancelled: bool = False
    events: asyncio.Queue[AgentEvent | object] = field(default_factory=asyncio.Queue)
    response_text: list[str] = field(default_factory=list)
    http_task: asyncio.Task[dict[str, Any]] | None = None


class OpenAICompatibleWorker(BaseAgentWorker):
    """直接调用 OpenAI 兼容 API 的 Worker，绕过 hermes-acp 子进程。

    支持所有 OpenAI 兼容的 API（MiniMax、Zhipu、Anthropic via proxy、OpenAI、Xiaomi、Kimi、Google 等）。
    通过 httpx 直接调用 /v1/chat/completions，支持 streaming。
    """

    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str,
        *,
        system_prompt: str | None = None,
        timeout: float = 120,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.system_prompt = system_prompt or ""
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._tasks: dict[str, _OpenAITask] = {}
        self._statuses: dict[str, TaskStatus] = {}

    async def start_task(self, task_id: str, request: HermesExecutionRequest) -> TaskHandle:
        if task_id in self._tasks:
            raise HermesExecutionError(
                "task 已存在运行中的 OpenAI 请求", context={"task_id": task_id}
            )

        model = request.agent_config.get("model", self.model)
        system_prompt = request.agent_config.get("system_prompt", self.system_prompt)

        task = _OpenAITask(
            request=request,
            model=str(model),
            api_base=self.api_base,
            api_key=self.api_key,
        )
        self._tasks[task_id] = task
        self._statuses[task_id] = TaskStatus.RUNNING

        # 构建消息
        messages = self._build_messages(request, system_prompt)

        # 启动 HTTP 请求
        task.http_task = asyncio.create_task(
            self._call_api(task_id, task, messages)
        )
        task.http_task.add_done_callback(
            lambda _f: task.events.put_nowait(_END_OF_STREAM)
        )

        return TaskHandle(task_id=task_id, hermes_session_id=f"openai-{task_id}")

    async def cancel_task(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            self._statuses[task_id] = TaskStatus.CANCELLED
            return
        task.cancelled = True
        if task.http_task and not task.http_task.done():
            task.http_task.cancel()
            try:
                await task.http_task
            except (asyncio.CancelledError, Exception):
                pass
        self._statuses[task_id] = TaskStatus.CANCELLED
        self._tasks.pop(task_id, None)

    async def get_status(self, task_id: str) -> TaskStatus:
        return self._statuses.get(task_id, TaskStatus.FAILED)

    async def stream_events(self, task_id: str) -> AsyncIterator[AgentEvent]:
        task = self._tasks.get(task_id)
        if task is None:
            raise HermesExecutionError("未知 OpenAI 任务", context={"task_id": task_id})

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

            # 等待 HTTP 任务完成，检查是否有异常
            if task.http_task:
                try:
                    await task.http_task
                except asyncio.CancelledError:
                    self._statuses[task_id] = TaskStatus.CANCELLED
                    return
                except Exception as exc:
                    self._statuses[task_id] = TaskStatus.FAILED
                    raise HermesExecutionError(
                        "OpenAI API 调用失败",
                        context={"task_id": task_id, "error": str(exc)},
                    ) from exc

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
                    "provider": "openai_compatible",
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
                "OpenAI 执行中断",
                context={"task_id": task_id, "error": str(exc)},
            ) from exc
        finally:
            self._tasks.pop(task_id, None)

    async def shutdown(self) -> None:
        for task_id in list(self._tasks):
            await self.cancel_task(task_id)

    def _build_messages(
        self, request: HermesExecutionRequest, system_prompt: str
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": request.prompt})
        return messages

    async def _call_api(
        self, task_id: str, task: _OpenAITask, messages: list[dict[str, str]]
    ) -> dict[str, Any]:
        """调用 OpenAI 兼容 API，支持 streaming。"""
        url = f"{self.api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {task.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": task.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": True,
        }

        sequence = 0
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    raise HermesExecutionError(
                        f"OpenAI API 返回 {response.status_code}",
                        context={
                            "task_id": task_id,
                            "status_code": response.status_code,
                            "body": body.decode(errors="replace")[:500],
                        },
                    )

                async for line in response.aiter_lines():
                    if task.cancelled:
                        break
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    # 解析 choices
                    choices = chunk.get("choices", [])
                    for choice in choices:
                        delta = choice.get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            task.response_text.append(content)
                            event = AgentEvent(
                                task_id=task_id,
                                event_type=EventType.STDOUT,
                                sequence=sequence,
                                payload={
                                    "text": content,
                                    "stream": "openai_compatible",
                                    "model": task.model,
                                },
                            )
                            sequence += 1
                            await task.events.put(event)

        return {"status": "completed"}
