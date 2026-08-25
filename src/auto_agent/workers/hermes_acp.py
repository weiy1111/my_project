from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from auto_agent.exceptions import HermesExecutionError, HermesProcessCrashError
from auto_agent.models import AgentEvent, EventType, HermesExecutionRequest, TaskStatus
from auto_agent.workers.base import BaseAgentWorker, TaskHandle

_END_OF_STREAM = object()


class _JsonRpcError(HermesExecutionError):
    """ACP JSON-RPC error returned by Hermes."""


@dataclass
class _ACPTask:
    process: asyncio.subprocess.Process
    client: "_ACPClient"
    request: HermesExecutionRequest
    logical_session_id: str
    acp_session_id: str
    events: asyncio.Queue[dict[str, Any] | object]
    prompt_task: asyncio.Task[dict[str, Any]]
    stderr_task: asyncio.Task[None]
    response_text: list[str] = field(default_factory=list)


class _ACPClient:
    """Small ACP v1 client over newline-delimited JSON-RPC 2.0."""

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        events: asyncio.Queue[dict[str, Any] | object],
        *,
        permission_mode: str,
    ) -> None:
        if process.stdin is None or process.stdout is None:
            raise HermesExecutionError("Hermes ACP 子进程缺少 stdio 管道")
        self.process = process
        self.stdin = process.stdin
        self.stdout = process.stdout
        self.events = events
        self.permission_mode = permission_mode
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._write_lock = asyncio.Lock()
        self._reader_task = asyncio.create_task(self._read_loop())

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        request_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        try:
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def close(self) -> None:
        if not self.stdin.is_closing():
            self.stdin.close()
            with suppress(BrokenPipeError, ConnectionResetError):
                await self.stdin.wait_closed()
        if not self._reader_task.done():
            self._reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader_task

    async def _send(self, message: dict[str, Any]) -> None:
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode()
        async with self._write_lock:
            try:
                self.stdin.write(encoded + b"\n")
                await self.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise HermesProcessCrashError(
                    "Hermes ACP 连接已关闭",
                    context={"returncode": self.process.returncode},
                ) from exc

    async def _read_loop(self) -> None:
        try:
            while raw_line := await self.stdout.readline():
                try:
                    message = json.loads(raw_line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    await self.events.put(
                        {"kind": "stdout", "text": raw_line.decode(errors="replace").rstrip()}
                    )
                    continue
                if not isinstance(message, dict):
                    continue
                if "method" in message and "id" in message:
                    await self._handle_server_request(message)
                elif "method" in message:
                    await self.events.put({"kind": "notification", "message": message})
                elif "id" in message:
                    self._handle_response(message)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self._fail_pending(exc)
        finally:
            if self._pending:
                self._fail_pending(
                    HermesProcessCrashError(
                        "Hermes ACP 进程在请求完成前退出",
                        context={"returncode": self.process.returncode},
                    )
                )
            await self.events.put(_END_OF_STREAM)

    def _handle_response(self, message: dict[str, Any]) -> None:
        future = self._pending.get(message.get("id"))
        if future is None or future.done():
            return
        if "error" in message:
            error = message.get("error") or {}
            data = error.get("data")
            details = data.get("details") if isinstance(data, dict) else None
            error_message = str(details or error.get("message") or "Hermes ACP JSON-RPC 请求失败")
            future.set_exception(
                _JsonRpcError(
                    error_message,
                    context={
                        "rpc_code": error.get("code"),
                        "rpc_data": data,
                    },
                )
            )
            return
        future.set_result(message.get("result"))

    async def _handle_server_request(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if method == "session/request_permission":
            result = self._permission_response(message.get("params") or {})
            await self._send({"jsonrpc": "2.0", "id": message["id"], "result": result})
            return
        await self._send(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "error": {"code": -32601, "message": f"不支持的 ACP 客户端方法: {method}"},
            }
        )

    def _permission_response(self, params: dict[str, Any]) -> dict[str, Any]:
        options = params.get("options") or []
        option_ids = {item.get("optionId") for item in options if isinstance(item, dict)}
        preferred = {
            "allow_once": ("allow_once",),
            "allow_session": ("allow_session", "allow_once"),
            "deny": ("deny", "deny_always"),
        }.get(self.permission_mode, ("deny", "deny_always"))
        selected = next((item for item in preferred if item in option_ids), None)
        if selected is None:
            return {"outcome": {"outcome": "cancelled"}}
        return {"outcome": {"outcome": "selected", "optionId": selected}}

    def _fail_pending(self, exc: BaseException) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)


class HermesACPWorker(BaseAgentWorker):
    """Hermes Agent Client Protocol v1 adapter.

    A dedicated ACP subprocess is used for each running task. Hermes sessions are
    persisted by Hermes itself; this worker only keeps the logical-to-ACP session
    mapping in memory so IM conversations can be resumed while this service lives.
    """

    def __init__(
        self,
        command: str | Path = "hermes-acp",
        args: Sequence[str] = (),
        *,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        startup_timeout: float = 30,
        permission_mode: str = "deny",
        model_provider: Mapping[str, str] | None = None,
    ) -> None:
        self.command = str(command)
        self.args = tuple(args)
        self.cwd = str(Path(cwd).expanduser().resolve()) if cwd else None
        self.env = dict(env or {})
        self.startup_timeout = startup_timeout
        self.permission_mode = permission_mode
        self.model_provider = dict(model_provider or {})
        self._runtime_home: tempfile.TemporaryDirectory[str] | None = None
        if self.model_provider:
            self._validate_model_provider()
            self._runtime_home = tempfile.TemporaryDirectory(prefix="auto-agent-hermes-")
            self._write_runtime_config(Path(self._runtime_home.name))
        self._tasks: dict[str, _ACPTask] = {}
        self._statuses: dict[str, TaskStatus] = {}
        self._sessions: dict[str, str] = {}

    async def start_task(self, task_id: str, request: HermesExecutionRequest) -> TaskHandle:
        if task_id in self._tasks:
            raise HermesExecutionError(
                "task 已存在运行中的 Hermes ACP 进程", context={"task_id": task_id}
            )
        events: asyncio.Queue[dict[str, Any] | object] = asyncio.Queue()
        process_env = self._build_process_env()
        try:
            process = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._resolve_cwd(request),
                env=process_env,
            )
        except OSError as exc:
            raise HermesExecutionError(
                "无法启动 Hermes ACP",
                context={"command": self.command, "error": str(exc)},
            ) from exc

        permission_mode = str(request.agent_config.get("permission_mode", self.permission_mode))
        if permission_mode not in {"deny", "allow_once", "allow_session"}:
            permission_mode = "deny"
        client = _ACPClient(process, events, permission_mode=permission_mode)
        stderr_task = asyncio.create_task(self._read_stderr(process, events))
        try:
            logical_session_id, acp_session_id = await asyncio.wait_for(
                self._initialize_task(client, request), timeout=self.startup_timeout
            )
        except Exception:
            await self._stop_process(process, client, stderr_task)
            raise

        prompt_task = asyncio.create_task(
            client.request(
                "session/prompt",
                {
                    "sessionId": acp_session_id,
                    "prompt": [{"type": "text", "text": self._build_prompt(request)}],
                },
            )
        )
        prompt_task.add_done_callback(lambda _future: events.put_nowait({"kind": "prompt_done"}))
        state = _ACPTask(
            process=process,
            client=client,
            request=request,
            logical_session_id=logical_session_id,
            acp_session_id=acp_session_id,
            events=events,
            prompt_task=prompt_task,
            stderr_task=stderr_task,
        )
        self._tasks[task_id] = state
        self._statuses[task_id] = TaskStatus.RUNNING
        self._sessions[logical_session_id] = acp_session_id
        return TaskHandle(task_id=task_id, hermes_session_id=acp_session_id)

    async def _initialize_task(
        self, client: _ACPClient, request: HermesExecutionRequest
    ) -> tuple[str, str]:
        initialized = await client.request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": False,
                },
                "clientInfo": {
                    "name": "auto_agent",
                    "title": "auto_agent",
                    "version": "0.1.0",
                },
            },
        )
        if not isinstance(initialized, dict) or initialized.get("protocolVersion") != 1:
            received = initialized.get("protocolVersion") if isinstance(initialized, dict) else None
            raise HermesExecutionError(
                "Hermes ACP 协议版本不兼容",
                context={"received": received, "expected": 1},
            )
        logical_session_id = request.session_id
        acp_session_id = await self._open_session(client, logical_session_id, request)
        await self._configure_session(client, acp_session_id, request)
        return logical_session_id, acp_session_id

    async def cancel_task(self, task_id: str) -> None:
        state = self._tasks.get(task_id)
        if state is None:
            self._statuses[task_id] = TaskStatus.CANCELLED
            return
        with suppress(HermesExecutionError, BrokenPipeError, ConnectionResetError):
            await state.client.notify("session/cancel", {"sessionId": state.acp_session_id})
        try:
            await asyncio.wait_for(asyncio.shield(state.prompt_task), timeout=0.25)
        except (asyncio.TimeoutError, HermesExecutionError):
            state.prompt_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await state.prompt_task
        await self._cleanup_task(task_id, state)
        self._statuses[task_id] = TaskStatus.CANCELLED

    async def get_status(self, task_id: str) -> TaskStatus:
        return self._statuses.get(task_id, TaskStatus.FAILED)

    async def stream_events(self, task_id: str) -> AsyncIterator[AgentEvent]:
        state = self._tasks.get(task_id)
        if state is None:
            raise HermesExecutionError("未知 Hermes ACP 任务", context={"task_id": task_id})

        sequence = 0
        try:
            while True:
                if state.prompt_task.done() and state.events.empty():
                    break
                event = await state.events.get()
                if event is _END_OF_STREAM:
                    if not state.prompt_task.done():
                        await state.prompt_task
                    break
                mapped = self._map_event(task_id, event, state)
                if mapped is not None:
                    mapped.sequence = sequence
                    sequence += 1
                    yield mapped

            response = await state.prompt_task
            stop_reason = str(response.get("stopReason") or "end_turn")
            if stop_reason == "cancelled":
                self._statuses[task_id] = TaskStatus.CANCELLED
                return
            if stop_reason == "refusal":
                self._statuses[task_id] = TaskStatus.FAILED
                raise HermesExecutionError(
                    "Hermes ACP 拒绝执行 prompt",
                    context={"task_id": task_id, "stop_reason": stop_reason},
                )
            self._statuses[task_id] = TaskStatus.COMPLETED
            yield AgentEvent(
                task_id=task_id,
                event_type=EventType.FINAL_OUTPUT,
                sequence=sequence,
                payload={
                    "text": "".join(state.response_text),
                    "stop_reason": stop_reason,
                    "usage": response.get("usage"),
                    "hermes_session_id": state.acp_session_id,
                },
            )
        except asyncio.CancelledError:
            raise
        except HermesExecutionError:
            self._statuses[task_id] = TaskStatus.FAILED
            raise
        except Exception as exc:
            self._statuses[task_id] = TaskStatus.FAILED
            raise HermesProcessCrashError(
                "Hermes ACP 执行中断",
                context={
                    "task_id": task_id,
                    "returncode": state.process.returncode,
                    "error": str(exc),
                },
            ) from exc
        finally:
            await self._cleanup_task(task_id, state)

    async def shutdown(self) -> None:
        for task_id in list(self._tasks):
            await self.cancel_task(task_id)
        if self._runtime_home is not None:
            self._runtime_home.cleanup()
            self._runtime_home = None

    def _validate_model_provider(self) -> None:
        required = {
            "provider",
            "model",
            "base_url",
            "api_mode",
            "api_key_env",
            "hermes_api_key_env",
        }
        missing = sorted(required - self.model_provider.keys())
        if missing:
            raise ValueError(f"Hermes model_provider 缺少字段: {', '.join(missing)}")

    def _write_runtime_config(self, runtime_home: Path) -> None:
        runtime_home.mkdir(parents=True, exist_ok=True)
        config = {
            "model": {
                "default": self.model_provider["model"],
                "provider": self.model_provider["provider"],
                "base_url": self.model_provider["base_url"].rstrip("/"),
                "api_mode": self.model_provider["api_mode"],
            }
        }
        (runtime_home / "config.yaml").write_text(
            yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

    def _build_process_env(self) -> dict[str, str]:
        process_env = os.environ.copy()
        process_env.update(self.env)
        if not self.model_provider:
            return process_env
        source_name = self.model_provider["api_key_env"]
        api_key = process_env.get(source_name, "").strip()
        if not api_key:
            raise HermesExecutionError(
                "Hermes 模型 API 凭据环境变量未设置",
                context={"api_key_env": source_name},
            )
        assert self._runtime_home is not None
        process_env["HERMES_HOME"] = self._runtime_home.name
        process_env[self.model_provider["hermes_api_key_env"]] = api_key
        return process_env

    async def _open_session(
        self,
        client: _ACPClient,
        logical_session_id: str,
        request: HermesExecutionRequest,
    ) -> str:
        params = {"cwd": self._resolve_cwd(request), "mcpServers": self._mcp_servers(request)}
        existing = self._sessions.get(logical_session_id)
        if existing and request.memory_enabled:
            loaded = await client.request("session/load", {**params, "sessionId": existing})
            if loaded is not None:
                return existing
            self._sessions.pop(logical_session_id, None)
        created = await client.request("session/new", params)
        session_id = created.get("sessionId") if isinstance(created, dict) else None
        if not isinstance(session_id, str) or not session_id:
            raise HermesExecutionError("Hermes ACP 未返回 sessionId", context={"response": created})
        return session_id

    async def _configure_session(
        self,
        client: _ACPClient,
        session_id: str,
        request: HermesExecutionRequest,
    ) -> None:
        model = request.agent_config.get("model")
        if model:
            await client.request(
                "session/set_model", {"sessionId": session_id, "modelId": str(model)}
            )
        mode = request.agent_config.get("mode")
        if mode:
            await client.request("session/set_mode", {"sessionId": session_id, "modeId": str(mode)})

    def _resolve_cwd(self, request: HermesExecutionRequest) -> str:
        configured = (
            request.sandbox_config.get("cwd")
            or request.sandbox_config.get("workspace_path")
            or self.cwd
            or os.getcwd()
        )
        return str(Path(configured).expanduser().resolve())

    @staticmethod
    def _mcp_servers(request: HermesExecutionRequest) -> list[dict[str, Any]]:
        servers = request.agent_config.get("mcp_servers", [])
        if not isinstance(servers, list):
            raise HermesExecutionError("agent_config.mcp_servers 必须是列表")
        return [dict(server) for server in servers if isinstance(server, dict)]

    @staticmethod
    def _build_prompt(request: HermesExecutionRequest) -> str:
        system_prompt = str(request.agent_config.get("system_prompt") or "").strip()
        if not system_prompt:
            return request.prompt
        return f"<auto_agent_system_instructions>\n{system_prompt}\n</auto_agent_system_instructions>\n\n{request.prompt}"

    @staticmethod
    def _map_event(task_id: str, raw_event: dict[str, Any], state: _ACPTask) -> AgentEvent | None:
        kind = raw_event.get("kind")
        if kind == "stderr":
            return AgentEvent(
                task_id=task_id,
                event_type=EventType.STDERR,
                sequence=0,
                payload={"text": raw_event.get("text", "")},
            )
        if kind == "stdout":
            return AgentEvent(
                task_id=task_id,
                event_type=EventType.STDOUT,
                sequence=0,
                payload={"text": raw_event.get("text", ""), "stream": "protocol_stdout"},
            )
        message = raw_event.get("message") or {}
        if message.get("method") != "session/update":
            return None
        params = message.get("params") or {}
        if params.get("sessionId") != state.acp_session_id:
            return None
        update = params.get("update") or {}
        update_type = update.get("sessionUpdate")
        if update_type == "agent_message_chunk":
            content = update.get("content") or {}
            text = content.get("text", "") if content.get("type") == "text" else ""
            if text:
                state.response_text.append(str(text))
            return AgentEvent(
                task_id=task_id,
                event_type=EventType.STDOUT,
                sequence=0,
                payload={"text": text, "stream": "agent_message", "acp": update},
            )
        if update_type == "agent_thought_chunk":
            content = update.get("content") or {}
            return AgentEvent(
                task_id=task_id,
                event_type=EventType.STDOUT,
                sequence=0,
                payload={
                    "text": content.get("text", ""),
                    "stream": "agent_thought",
                    "acp": update,
                },
            )
        if update_type == "tool_call":
            return AgentEvent(
                task_id=task_id,
                event_type=EventType.TOOL_CALL,
                sequence=0,
                payload=update,
            )
        if update_type == "tool_call_update":
            terminal = update.get("status") in {"completed", "failed"}
            return AgentEvent(
                task_id=task_id,
                event_type=EventType.TOOL_RESULT if terminal else EventType.TOOL_CALL,
                sequence=0,
                payload=update,
            )
        return None

    @staticmethod
    async def _read_stderr(
        process: asyncio.subprocess.Process,
        events: asyncio.Queue[dict[str, Any] | object],
    ) -> None:
        if process.stderr is None:
            return
        async for raw_line in process.stderr:
            await events.put(
                {"kind": "stderr", "text": raw_line.decode(errors="replace").rstrip("\n")}
            )

    async def _cleanup_task(self, task_id: str, state: _ACPTask) -> None:
        if self._tasks.get(task_id) is not state:
            return
        if not state.request.memory_enabled and state.process.returncode is None:
            with suppress(Exception):
                await asyncio.wait_for(
                    state.client.request("session/close", {"sessionId": state.acp_session_id}),
                    timeout=2,
                )
            self._sessions.pop(state.logical_session_id, None)
        await self._stop_process(state.process, state.client, state.stderr_task)
        self._tasks.pop(task_id, None)

    @staticmethod
    async def _stop_process(
        process: asyncio.subprocess.Process,
        client: _ACPClient,
        stderr_task: asyncio.Task[None],
    ) -> None:
        await client.close()
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.terminate()
            for _ in range(100):
                if process.returncode is not None:
                    break
                await asyncio.sleep(0.01)
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
        with suppress(asyncio.TimeoutError, asyncio.CancelledError, ProcessLookupError):
            await asyncio.wait_for(asyncio.shield(process.wait()), timeout=1)
        if not stderr_task.done():
            stderr_task.cancel()
        with suppress(asyncio.CancelledError):
            await stderr_task
