from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping

from auto_agent.models import AgentEvent, ImMessage

MessageHandler = Callable[[ImMessage], Awaitable[None]]


class BaseImChannel(ABC):
    """Message transport only; task execution belongs to TaskManager."""

    def __init__(self, channel_id: str) -> None:
        self.channel_id = channel_id
        self._message_handler: MessageHandler | None = None

    def on_message(self, handler: MessageHandler) -> None:
        self._message_handler = handler

    async def dispatch_message(self, message: ImMessage) -> None:
        if self._message_handler is not None:
            await self._message_handler(message)

    @abstractmethod
    async def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def send_reply(
        self, session_id: str, content: str, *, metadata: dict | None = None
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def send_event(
        self,
        session_id: str,
        event: AgentEvent,
        *,
        metadata: dict | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError


class BaseWebhookImChannel(BaseImChannel):
    """IM transport whose inbound messages arrive through an HTTP webhook."""

    def __init__(self, channel_id: str, webhook_path: str) -> None:
        super().__init__(channel_id)
        self.webhook_path = webhook_path

    @abstractmethod
    async def handle_webhook(self, body: bytes, headers: Mapping[str, str]) -> dict:
        raise NotImplementedError
