import asyncio
import sys
from pathlib import Path

import yaml

from auto_agent.exceptions import HermesExecutionError
from auto_agent.models import EventType, HermesExecutionRequest, TaskStatus
from auto_agent.workers import HermesACPWorker


FAKE_ACP_SERVER = r"""
import json
import sys


def send(message):
    print(json.dumps(message, ensure_ascii=False), flush=True)


pending_prompt = None
for raw_line in sys.stdin:
    message = json.loads(raw_line)
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}
    if method == "initialize":
        assert params["protocolVersion"] == 1
        assert params["clientInfo"]["name"] == "auto_agent"
        send({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": 1}})
    elif method == "session/new":
        assert "cwd" in params and params["mcpServers"] == []
        send({"jsonrpc": "2.0", "id": request_id, "result": {"sessionId": "acp-session"}})
    elif method == "session/load":
        assert params["sessionId"] == "acp-session"
        send({"jsonrpc": "2.0", "id": request_id, "result": {}})
    elif method in {"session/set_model", "session/set_mode", "session/close"}:
        send({"jsonrpc": "2.0", "id": request_id, "result": {}})
    elif method == "session/prompt":
        prompt = params["prompt"][0]["text"]
        if "rpc-error" in prompt:
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": "model unavailable", "data": {"provider": "fake"}},
            })
            continue
        pending_prompt = request_id
        session_id = params["sessionId"]
        send({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": session_id,
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "review done|"},
                },
            },
        })
        send({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": session_id,
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "tool-1",
                    "title": "git diff",
                    "kind": "execute",
                    "status": "in_progress",
                    "rawInput": {"command": "git diff"},
                },
            },
        })
        send({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": session_id,
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "tool-1",
                    "status": "completed",
                    "rawOutput": "clean",
                },
            },
        })
        send({
            "jsonrpc": "2.0",
            "id": 999,
            "method": "session/request_permission",
            "params": {
                "sessionId": session_id,
                "toolCall": {"toolCallId": "permission-1", "kind": "edit", "status": "pending"},
                "options": [
                    {"optionId": "allow_once", "kind": "allow_once", "name": "Allow once"},
                    {"optionId": "deny", "kind": "reject_once", "name": "Deny"},
                ],
            },
        })
    elif request_id == 999 and method is None:
        outcome = message["result"]["outcome"].get("optionId", message["result"]["outcome"]["outcome"])
        send({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": "acp-session",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": outcome},
                },
            },
        })
        send({
            "jsonrpc": "2.0",
            "id": pending_prompt,
            "result": {"stopReason": "end_turn", "usage": {"inputTokens": 2, "outputTokens": 3}},
        })
    elif method == "session/cancel":
        print("cancel received", file=sys.stderr, flush=True)
"""


def _write_server(tmp_path: Path) -> Path:
    server = tmp_path / "fake_acp.py"
    server.write_text(FAKE_ACP_SERVER, encoding="utf-8")
    return server


def _model_provider() -> dict[str, str]:
    return {
        "provider": "anthropic",
        "model": "xiaomi/mimo-v2.5-pro",
        "base_url": "http://model.internal/anthropic",
        "api_mode": "anthropic_messages",
        "api_key_env": "MIMO_API_KEY",
        "hermes_api_key_env": "ANTHROPIC_API_KEY",
    }


def test_acp_worker_builds_isolated_model_profile_without_secret(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("MIMO_API_KEY", "secret-only-in-process-env")
    worker = HermesACPWorker(
        sys.executable,
        [_write_server(tmp_path).as_posix()],
        cwd=tmp_path,
        model_provider=_model_provider(),
    )
    assert worker._runtime_home is not None
    runtime_home = Path(worker._runtime_home.name)
    config_text = (runtime_home / "config.yaml").read_text(encoding="utf-8")
    config = yaml.safe_load(config_text)

    assert config["model"] == {
        "default": "xiaomi/mimo-v2.5-pro",
        "provider": "anthropic",
        "base_url": "http://model.internal/anthropic",
        "api_mode": "anthropic_messages",
    }
    assert "secret-only-in-process-env" not in config_text
    process_env = worker._build_process_env()
    assert process_env["ANTHROPIC_API_KEY"] == "secret-only-in-process-env"
    assert process_env["HERMES_HOME"] == runtime_home.as_posix()

    asyncio.run(worker.shutdown())
    assert not runtime_home.exists()


def test_acp_worker_requires_model_api_key(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        monkeypatch.delenv("MIMO_API_KEY", raising=False)
        worker = HermesACPWorker(
            sys.executable,
            [_write_server(tmp_path).as_posix()],
            cwd=tmp_path,
            model_provider=_model_provider(),
        )
        try:
            await worker.start_task("task-1", HermesExecutionRequest(prompt="hello"))
        except HermesExecutionError as exc:
            assert exc.context == {"api_key_env": "MIMO_API_KEY"}
        else:
            raise AssertionError("missing model API key was accepted")
        await worker.shutdown()

    asyncio.run(run())


def test_acp_worker_maps_protocol_events_and_denies_permission(tmp_path: Path) -> None:
    async def run() -> None:
        worker = HermesACPWorker(
            sys.executable,
            [_write_server(tmp_path).as_posix()],
            cwd=tmp_path,
            permission_mode="deny",
        )
        request = HermesExecutionRequest(
            session_id="logical-session",
            prompt="review this change",
            agent_config={"system_prompt": "read only", "model": "fake:test"},
            memory_enabled=False,
        )
        handle = await worker.start_task("task-1", request)
        events = [event async for event in worker.stream_events("task-1")]

        assert handle.hermes_session_id == "acp-session"
        assert [event.event_type for event in events] == [
            EventType.STDOUT,
            EventType.TOOL_CALL,
            EventType.TOOL_RESULT,
            EventType.STDOUT,
            EventType.FINAL_OUTPUT,
        ]
        assert events[-1].payload["text"] == "review done|deny"
        assert events[-1].payload["stop_reason"] == "end_turn"
        assert await worker.get_status("task-1") == TaskStatus.COMPLETED
        assert worker._tasks == {}

    asyncio.run(run())


def test_acp_worker_resumes_logical_session(tmp_path: Path) -> None:
    async def run() -> None:
        worker = HermesACPWorker(sys.executable, [_write_server(tmp_path).as_posix()], cwd=tmp_path)
        for task_id in ("task-1", "task-2"):
            request = HermesExecutionRequest(
                session_id="logical-session", prompt="continue", memory_enabled=True
            )
            handle = await worker.start_task(task_id, request)
            assert handle.hermes_session_id == "acp-session"
            _ = [event async for event in worker.stream_events(task_id)]
        assert worker._sessions == {"logical-session": "acp-session"}

    asyncio.run(run())


def test_acp_worker_surfaces_json_rpc_error(tmp_path: Path) -> None:
    async def run() -> None:
        worker = HermesACPWorker(sys.executable, [_write_server(tmp_path).as_posix()], cwd=tmp_path)
        await worker.start_task(
            "task-1", HermesExecutionRequest(prompt="rpc-error", memory_enabled=False)
        )
        try:
            _ = [event async for event in worker.stream_events("task-1")]
        except HermesExecutionError as exc:
            assert "model unavailable" in str(exc)
        else:
            raise AssertionError("JSON-RPC error was not surfaced")
        assert await worker.get_status("task-1") == TaskStatus.FAILED

    asyncio.run(run())


def test_acp_worker_cancel_sends_notification(tmp_path: Path) -> None:
    async def run() -> None:
        server = tmp_path / "cancel_acp.py"
        source = FAKE_ACP_SERVER.replace(
            "pending_prompt = request_id",
            'pending_prompt = request_id\n        if "wait forever" in prompt:\n            continue',
        )
        server.write_text(source, encoding="utf-8")
        worker = HermesACPWorker(sys.executable, [server.as_posix()], cwd=tmp_path)
        await worker.start_task(
            "task-1", HermesExecutionRequest(prompt="wait forever", memory_enabled=True)
        )
        await worker.cancel_task("task-1")
        assert await worker.get_status("task-1") == TaskStatus.CANCELLED
        assert worker._tasks == {}

    asyncio.run(run())


def test_acp_worker_rejects_protocol_version_mismatch(tmp_path: Path) -> None:
    async def run() -> None:
        server = _write_server(tmp_path)
        source = server.read_text(encoding="utf-8").replace(
            '{"protocolVersion": 1}})', '{"protocolVersion": 2}})', 1
        )
        server.write_text(source, encoding="utf-8")
        worker = HermesACPWorker(sys.executable, [server.as_posix()], cwd=tmp_path)
        try:
            await worker.start_task("task-1", HermesExecutionRequest(prompt="hello"))
        except HermesExecutionError as exc:
            assert "协议版本不兼容" in str(exc)
        else:
            raise AssertionError("protocol mismatch was accepted")

    asyncio.run(run())
