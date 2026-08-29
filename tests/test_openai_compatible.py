"""OpenAICompatibleWorker 单元测试。"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auto_agent.exceptions import HermesExecutionError
from auto_agent.models import AgentEvent, EventType, HermesExecutionRequest, TaskStatus
from auto_agent.workers.openai_compatible import OpenAICompatibleWorker


def _make_request(prompt: str = "测试 prompt") -> HermesExecutionRequest:
    return HermesExecutionRequest(prompt=prompt)


def _make_sse_lines(chunks: list[str]) -> list[str]:
    """构造 SSE 格式的响应行。"""
    lines = []
    for chunk in chunks:
        data = json.dumps({"choices": [{"delta": {"content": chunk}}]})
        lines.append(f"data: {data}")
    lines.append("data: [DONE]")
    return lines


class MockStreamContext:
    """模拟 httpx 的 stream 上下文管理器。"""

    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *args):
        pass


class MockResponse:
    """模拟 httpx 响应。"""

    def __init__(self, status_code: int, lines: list[str] | None = None, body: bytes = b""):
        self.status_code = status_code
        self._lines = lines or []
        self._body = body

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return self._body


class MockClient:
    """模拟 httpx.AsyncClient。"""

    def __init__(self, response: MockResponse):
        self._response = response

    def stream(self, *args, **kwargs):
        return MockStreamContext(self._response)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class MockClientFactory:
    """模拟 httpx.AsyncClient 工厂，支持作为上下文管理器使用。"""

    def __init__(self, response: MockResponse):
        self._response = response

    async def __aenter__(self):
        return MockClient(self._response)

    async def __aexit__(self, *args):
        pass


class TestOpenAICompatibleWorker:
    def test_init(self):
        worker = OpenAICompatibleWorker(
            api_base="http://model.mify.ai.srv/v1",
            api_key="test-key",
            model="test-model",
        )
        assert worker.api_base == "http://model.mify.ai.srv/v1"
        assert worker.api_key == "test-key"
        assert worker.model == "test-model"

    def test_init_strips_trailing_slash(self):
        worker = OpenAICompatibleWorker(
            api_base="http://model.mify.ai.srv/v1/",
            api_key="key",
            model="model",
        )
        assert worker.api_base == "http://model.mify.ai.srv/v1"

    @pytest.mark.asyncio
    async def test_start_task(self):
        worker = OpenAICompatibleWorker(
            api_base="http://test.com/v1",
            api_key="key",
            model="model",
        )
        request = _make_request()
        handle = await worker.start_task("task-1", request)
        assert handle.task_id == "task-1"
        assert handle.hermes_session_id.startswith("openai-")
        assert await worker.get_status("task-1") == TaskStatus.RUNNING
        await worker.cancel_task("task-1")

    @pytest.mark.asyncio
    async def test_start_task_duplicate_raises(self):
        worker = OpenAICompatibleWorker(
            api_base="http://test.com/v1",
            api_key="key",
            model="model",
        )
        request = _make_request()
        await worker.start_task("task-1", request)
        with pytest.raises(HermesExecutionError, match="已存在"):
            await worker.start_task("task-1", request)
        await worker.cancel_task("task-1")

    @pytest.mark.asyncio
    async def test_cancel_task(self):
        worker = OpenAICompatibleWorker(
            api_base="http://test.com/v1",
            api_key="key",
            model="model",
        )
        request = _make_request()
        await worker.start_task("task-1", request)
        await worker.cancel_task("task-1")
        assert await worker.get_status("task-1") == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_unknown_task(self):
        worker = OpenAICompatibleWorker(
            api_base="http://test.com/v1",
            api_key="key",
            model="model",
        )
        await worker.cancel_task("unknown")
        assert await worker.get_status("unknown") == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_get_status_unknown(self):
        worker = OpenAICompatibleWorker(
            api_base="http://test.com/v1",
            api_key="key",
            model="model",
        )
        assert await worker.get_status("unknown") == TaskStatus.FAILED

    @pytest.mark.asyncio
    async def test_build_messages_with_system_prompt(self):
        worker = OpenAICompatibleWorker(
            api_base="http://test.com/v1",
            api_key="key",
            model="model",
            system_prompt="你是助手",
        )
        request = _make_request("你好")
        messages = worker._build_messages(request, "你是助手")
        assert len(messages) == 2
        assert messages[0] == {"role": "system", "content": "你是助手"}
        assert messages[1] == {"role": "user", "content": "你好"}

    @pytest.mark.asyncio
    async def test_build_messages_without_system_prompt(self):
        worker = OpenAICompatibleWorker(
            api_base="http://test.com/v1",
            api_key="key",
            model="model",
        )
        request = _make_request("你好")
        messages = worker._build_messages(request, "")
        assert len(messages) == 1
        assert messages[0] == {"role": "user", "content": "你好"}

    @pytest.mark.asyncio
    async def test_call_api_success(self):
        """测试 API 调用成功并返回 streaming 响应。"""
        worker = OpenAICompatibleWorker(
            api_base="http://test.com/v1",
            api_key="test-key",
            model="test-model",
        )
        request = _make_request()
        await worker.start_task("task-1", request)

        # 模拟 SSE 响应
        sse_lines = _make_sse_lines(["Hello", " World"])
        mock_response = MockResponse(status_code=200, lines=sse_lines)
        mock_factory = MockClientFactory(mock_response)

        with patch("auto_agent.workers.openai_compatible.httpx.AsyncClient", return_value=mock_factory):
            events = []
            async for event in worker.stream_events("task-1"):
                events.append(event)

        assert len(events) >= 2  # 至少有 2 个 STDOUT 事件 + 1 个 FINAL_OUTPUT
        stdout_events = [e for e in events if e.event_type == EventType.STDOUT]
        assert len(stdout_events) == 2
        assert stdout_events[0].payload["text"] == "Hello"
        assert stdout_events[1].payload["text"] == " World"
        final_events = [e for e in events if e.event_type == EventType.FINAL_OUTPUT]
        assert len(final_events) == 1
        assert final_events[0].payload["text"] == "Hello World"

    @pytest.mark.asyncio
    async def test_call_api_error_status(self):
        """测试 API 返回错误状态码。"""
        worker = OpenAICompatibleWorker(
            api_base="http://test.com/v1",
            api_key="test-key",
            model="test-model",
        )
        request = _make_request()
        await worker.start_task("task-1", request)

        mock_response = MockResponse(status_code=401, body=b"Unauthorized")
        mock_factory = MockClientFactory(mock_response)

        with patch("auto_agent.workers.openai_compatible.httpx.AsyncClient", return_value=mock_factory):
            with pytest.raises(HermesExecutionError, match="调用失败"):
                async for _ in worker.stream_events("task-1"):
                    pass

    @pytest.mark.asyncio
    async def test_shutdown(self):
        worker = OpenAICompatibleWorker(
            api_base="http://test.com/v1",
            api_key="key",
            model="model",
        )
        request = _make_request()
        await worker.start_task("task-1", request)
        await worker.shutdown()
        assert await worker.get_status("task-1") == TaskStatus.CANCELLED
