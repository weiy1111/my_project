import asyncio
import json
import logging
from collections.abc import Sequence

from auto_agent.exceptions import ImChannelError
from auto_agent.models import AgentEvent, EventType, ImMessage
from auto_agent.im_channels.base import BaseImChannel


class FeishuCliChannel(BaseImChannel):
    """Adapter for a line-oriented Feishu CLI.

    The CLI contract is deliberately small: inbound lines are JSON messages and
    outbound replies are JSON objects written to stdin. A real Feishu CLI can be
    plugged in without changing TaskManager or Worker code.
    """

    def __init__(
        self,
        command: str,
        args: Sequence[str] = (),
        *,
        channel_id: str = "feishu_cli",
        reconnect: bool = True,
        max_reconnect_attempts: int | None = None,
        reconnect_backoff: float = 1.0,
        dedupe_size: int = 2048,
    ) -> None:
        super().__init__(channel_id)
        self.command = command
        self.args = list(args)
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._closed = True
        self.reconnect = reconnect
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_backoff = max(0.1, reconnect_backoff)
        self._seen_ids: dict[str, None] = {}
        self._dedupe_size = max(1, dedupe_size)
        self._logger = logging.getLogger(__name__)

    async def connect(self) -> None:
        if self._reader_task and not self._reader_task.done():
            return
        self._closed = False
        self._reader_task = asyncio.create_task(self._supervise())

    async def _supervise(self) -> None:
        attempts = 0
        while not self._closed:
            try:
                await self._connect_once()
                attempts = 0
                await self._read_messages()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception(
                    "Feishu CLI connection failed", extra={"channel_id": self.channel_id}
                )
            finally:
                await self._stop_process()
            if self._closed or not self.reconnect:
                break
            attempts += 1
            if self.max_reconnect_attempts is not None and attempts > self.max_reconnect_attempts:
                self._logger.error(
                    "Feishu CLI reconnect attempts exhausted", extra={"channel_id": self.channel_id}
                )
                break
            await asyncio.sleep(min(self.reconnect_backoff * 2 ** (attempts - 1), 30))

    async def _connect_once(self) -> None:
        self._process = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if self._process.stdout is None:
            raise ImChannelError("Feishu CLI stdout is unavailable")

    async def _read_messages(self) -> None:
        if self._process is None or self._process.stdout is None:
            raise ImChannelError("Feishu CLI is not connected")
        async for raw_line in self._process.stdout:
            try:
                payload = json.loads(raw_line)
                message = ImMessage.model_validate(payload)
            except (json.JSONDecodeError, ValueError) as exc:
                self._logger.warning("invalid Feishu CLI message", extra={"error": str(exc)})
                continue
            message_id = self._message_id(message)
            if message_id and self._seen(message_id):
                continue
            await self.dispatch_message(message)

    async def send_reply(
        self, session_id: str, content: str, *, metadata: dict | None = None
    ) -> None:
        await self._write(
            {
                "type": "reply",
                "session_id": session_id,
                "content": content,
                "metadata": metadata or {},
            }
        )

    async def send_event(
        self,
        session_id: str,
        event: AgentEvent,
        *,
        metadata: dict | None = None,
    ) -> None:
        content = self._format_event(event)
        if content:
            await self.send_reply(
                session_id,
                content,
                metadata={**(metadata or {}), "event_id": event.event_id},
            )

    def _format_event(self, event: AgentEvent) -> str:
        if event.event_type in {EventType.STDOUT, EventType.STDERR}:
            return str(event.payload.get("text", ""))
        if event.event_type == EventType.FINAL_OUTPUT:
            return str(event.payload.get("text", event.payload.get("output", "")))
        if event.event_type == EventType.ERROR:
            return f"执行失败：{event.payload.get('message', 'unknown error')}"
        if event.event_type == EventType.STATUS_CHANGED:
            return f"任务状态：{event.payload.get('status', '')}"
        return ""

    async def _write(self, payload: dict) -> None:
        if self._process is None or self._process.stdin is None:
            raise ImChannelError("Feishu CLI is not connected")
        async with self._write_lock:
            self._process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
            await self._process.stdin.drain()

    def _message_id(self, message: ImMessage) -> str | None:
        value = (
            message.metadata.get("message_id")
            or message.metadata.get("event_id")
            or message.metadata.get("id")
        )
        return str(value) if value else None

    def _seen(self, message_id: str) -> bool:
        if message_id in self._seen_ids:
            return True
        self._seen_ids[message_id] = None
        while len(self._seen_ids) > self._dedupe_size:
            del self._seen_ids[next(iter(self._seen_ids))]
        return False

    async def _stop_process(self) -> None:
        process = self._process
        self._process = None
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

    async def close(self) -> None:
        self._closed = True
        task = self._reader_task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._stop_process()
        self._reader_task = None
