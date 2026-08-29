"""OpenCodeCliWorker 单元测试。"""

import asyncio
import json
from unittest.mock import patch

import pytest

from auto_agent.exceptions import HermesExecutionError
from auto_agent.models import AgentEvent, EventType, HermesExecutionRequest, TaskStatus
from auto_agent.workers.opencode_cli import OpenCodeCliWorker, _CliTask


def _make_request(prompt: str = "测试 prompt") -> HermesExecutionRequest:
    return HermesExecutionRequest(prompt=prompt)


class MockProcess:
    """模拟 asyncio.subprocess.Process。"""

    def __init__(self, stdout_lines: list[str], returncode: int = 0):
        self._stdout_lines = stdout_lines
        self.returncode = returncode
        self._wait_done = asyncio.Event()
        self.stderr = None

    @property
    def stdout(self):
        return self

    async def wait(self):
        await self._wait_done.wait()
        return self.returncode

    def kill(self):
        self.returncode = -9

    async def readline(self):
        if self._stdout_lines:
            return self._stdout_lines.pop(0).encode()
        await asyncio.sleep(0.1)
        return b""


def _text_event(text: str) -> str:
    return json.dumps({"type": "text", "part": {"type": "text", "text": text}})


def _step_start() -> str:
    return json.dumps({"type": "step_start"})


class TestOpenCodeCliWorker:
    def test_init(self):
        worker = OpenCodeCliWorker(model="opencode/mimo-v2.5-free")
        assert worker.model == "opencode/mimo-v2.5-free"
        assert worker.command == "opencode"
        assert worker.timeout == 300

    @pytest.mark.asyncio
    async def test_start_task(self):
        worker = OpenCodeCliWorker(model="opencode/mimo-v2.5-free")
        request = _make_request()
        handle = await worker.start_task("task-1", request)
        assert handle.task_id == "task-1"
        assert handle.hermes_session_id.startswith("opencode-")
        assert await worker.get_status("task-1") == TaskStatus.RUNNING
        await worker.cancel_task("task-1")

    @pytest.mark.asyncio
    async def test_start_task_duplicate_raises(self):
        worker = OpenCodeCliWorker(model="opencode/mimo-v2.5-free")
        request = _make_request()
        await worker.start_task("task-1", request)
        with pytest.raises(HermesExecutionError, match="已存在"):
            await worker.start_task("task-1", request)
        await worker.cancel_task("task-1")

    @pytest.mark.asyncio
    async def test_cancel_task(self):
        worker = OpenCodeCliWorker(model="opencode/mimo-v2.5-free")
        request = _make_request()
        await worker.start_task("task-1", request)
        await worker.cancel_task("task-1")
        assert await worker.get_status("task-1") == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_unknown_task(self):
        worker = OpenCodeCliWorker(model="opencode/mimo-v2.5-free")
        await worker.cancel_task("unknown")
        assert await worker.get_status("unknown") == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_get_status_unknown(self):
        worker = OpenCodeCliWorker(model="opencode/mimo-v2.5-free")
        assert await worker.get_status("unknown") == TaskStatus.FAILED

    def test_build_prompt_with_system(self):
        worker = OpenCodeCliWorker(model="m", system_prompt="你是助手")
        request = _make_request("你好")
        out = worker._build_prompt(request, "你是助手")
        assert "你是助手" in out
        assert "你好" in out

    def test_build_prompt_without_system(self):
        worker = OpenCodeCliWorker(model="m", system_prompt="")
        request = _make_request("你好")
        assert worker._build_prompt(request, "") == "你好"

    def test_handle_stdout_line_text(self):
        worker = OpenCodeCliWorker(model="m")
        task = _CliTask(request=_make_request(), model="m")
        worker._tasks["t"] = task
        worker._handle_stdout_line("t", task, _text_event("Hello"))
        assert task.sequence == 1
        assert "".join(task.response_text) == "Hello"
        event = task.events.get_nowait()
        assert isinstance(event, AgentEvent)
        assert event.event_type == EventType.STDOUT
        assert event.payload["text"] == "Hello"

    def test_handle_stdout_line_ignores_non_text(self):
        worker = OpenCodeCliWorker(model="m")
        task = _CliTask(request=_make_request(), model="m")
        worker._handle_stdout_line("t", task, _step_start())
        assert task.sequence == 0
        assert task.events.empty()

    def test_handle_stdout_line_ignores_garbage(self):
        worker = OpenCodeCliWorker(model="m")
        task = _CliTask(request=_make_request(), model="m")
        worker._handle_stdout_line("t", task, "not json {{{")
        assert task.sequence == 0
        assert task.events.empty()

    @pytest.mark.asyncio
    async def test_stream_events_unknown(self):
        worker = OpenCodeCliWorker(model="m")
        with pytest.raises(HermesExecutionError, match="未知"):
            async for _ in worker.stream_events("nope"):
                pass

    @pytest.mark.asyncio
    async def test_shutdown(self):
        worker = OpenCodeCliWorker(model="m")
        request = _make_request()
        await worker.start_task("task-1", request)
        await worker.shutdown()
        assert await worker.get_status("task-1") == TaskStatus.CANCELLED
