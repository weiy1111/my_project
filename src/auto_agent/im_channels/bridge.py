import logging
from collections import OrderedDict
from uuid import uuid4

from auto_agent.exceptions import AutoAgentError, DuplicateTaskError
from auto_agent.im_channels.base import BaseImChannel
from auto_agent.im_channels.cards import CARD_ACTION_CANCEL
from auto_agent.models import AgentEvent, EventType, ImAction, ImMessage
from auto_agent.task_manager import TaskManager


class ImTaskBridge:
    """Connect one IM transport to the shared TaskManager.

    This class owns only message-to-task conversion and reply formatting. It
    deliberately does not inspect prompts or implement agent behavior.
    """

    def __init__(
        self,
        channel: BaseImChannel,
        task_manager: TaskManager,
        *,
        workspace_id: str,
        default_agent: str | None = None,
        dedupe_size: int = 2048,
    ) -> None:
        self.channel = channel
        self.task_manager = task_manager
        self.workspace_id = workspace_id
        self.default_agent = default_agent
        self.dedupe_size = max(1, dedupe_size)
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._session_tasks: dict[str, str] = {}
        self._logger = logging.getLogger(__name__)
        channel.on_message(self.handle_message)

    async def start(self) -> None:
        await self.channel.connect()

    async def close(self) -> None:
        await self.channel.close()

    async def handle_message(self, message: ImMessage) -> None:
        message_id = self._message_id(message)
        reply_metadata = self._reply_metadata(message)
        if message_id and self._already_seen(message_id):
            return
        active_task_id = self._session_tasks.get(message.session_id)
        if active_task_id:
            try:
                status = self.task_manager.get(active_task_id).status
                if status.value in {"queued", "running"}:
                    await self.channel.send_reply(
                        message.session_id,
                        "该会话已有任务执行中，请稍后再试。",
                        metadata=reply_metadata,
                    )
                    return
            except AutoAgentError:
                self._session_tasks.pop(message.session_id, None)

        task_id = str(uuid4())
        try:
            context = await self.task_manager.submit_im(
                task_id=task_id,
                workspace_id=self.workspace_id,
                session_id=message.session_id,
                prompt=message.content,
                agent_name=message.agent_name
                or message.metadata.get("agent_name")
                or self.default_agent,
                request_id=message_id,
                metadata={"sender": message.sender, "channel_id": message.channel_id},
            )
        except DuplicateTaskError:
            await self.channel.send_reply(
                message.session_id,
                "任务已提交，请勿重复发送。",
                metadata=reply_metadata,
            )
            return
        self._session_tasks[message.session_id] = context.task_id
        self.task_manager.subscribe(
            context.task_id,
            self._make_event_sink(message.session_id, reply_metadata),
        )
        await self.channel.send_reply(
            message.session_id,
            "已收到，任务开始执行。",
            metadata=reply_metadata,
        )

    async def handle_action(self, action: ImAction) -> None:
        """Handle an interactive action such as a card cancel button."""
        if action.action != CARD_ACTION_CANCEL or not action.task_id:
            self._logger.debug(
                "ignored IM action %s", action.action, extra={"channel_id": action.channel_id}
            )
            return
        metadata = {"chat_id": action.chat_id} if action.chat_id else {}
        try:
            await self.task_manager.cancel(action.task_id)
        except AutoAgentError:
            await self.channel.send_reply(
                action.session_id,
                "任务已结束或不存在，无法取消。",
                metadata=metadata,
            )
            return
        await self.channel.send_reply(action.session_id, "任务已取消。", metadata=metadata)

    def _make_event_sink(self, session_id: str, reply_metadata: dict):
        async def sink(event: AgentEvent) -> None:
            await self.channel.send_event(session_id, event, metadata=reply_metadata)
            if event.event_type in {
                EventType.FINAL_OUTPUT,
                EventType.ERROR,
            } or (
                event.event_type == EventType.STATUS_CHANGED
                and event.payload.get("status") in {"completed", "failed", "cancelled", "timed_out"}
            ):
                self._session_tasks.pop(session_id, None)

        return sink

    def _reply_metadata(self, message: ImMessage) -> dict:
        metadata: dict = {}
        if message.reply_callback:
            metadata["reply_callback"] = message.reply_callback
        for key in ("message_id", "chat_id"):
            if value := message.metadata.get(key):
                metadata[key] = value
        return metadata

    def _message_id(self, message: ImMessage) -> str | None:
        metadata = message.metadata
        value = metadata.get("message_id") or metadata.get("event_id") or metadata.get("id")
        return str(value) if value else None

    def _already_seen(self, message_id: str) -> bool:
        if message_id in self._seen:
            self._seen.move_to_end(message_id)
            return True
        self._seen[message_id] = None
        self._seen.move_to_end(message_id)
        while len(self._seen) > self.dedupe_size:
            self._seen.popitem(last=False)
        return False
