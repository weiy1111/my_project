from abc import ABC, abstractmethod
import asyncio
import logging
from typing import Any

import httpx

from auto_agent.exceptions import MulticaAuthError, MulticaProtocolError, MulticaRejectedError
from auto_agent.mappings.status import to_multica_status
from auto_agent.models import AgentEvent, TaskContext


class MulticaEventSink(ABC):
    """Outbound Multica contract, kept separate from the task coordinator."""

    @abstractmethod
    async def publish_event(self, event: AgentEvent, context: TaskContext) -> None:
        raise NotImplementedError

    @abstractmethod
    async def publish_status(self, context: TaskContext) -> None:
        raise NotImplementedError


class NullMulticaEventSink(MulticaEventSink):
    """Default sink for library users that do not configure callbacks."""

    async def publish_event(self, event: AgentEvent, context: TaskContext) -> None:
        return None

    async def publish_status(self, context: TaskContext) -> None:
        return None


class HttpMulticaEventSink(MulticaEventSink):
    """POST task events to a Multica-compatible callback endpoint.

    ``TaskContext.callback_url`` takes precedence over ``default_callback_url``.
    The payload is intentionally versioned and contains the original event as a
    nested object, allowing a gateway to evolve without changing TaskManager.
    """

    def __init__(
        self,
        *,
        default_callback_url: str | None = None,
        token: str | None = None,
        timeout: float = 10,
        retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.default_callback_url = default_callback_url
        self.token = token
        self.timeout = timeout
        self.retries = max(0, retries)
        self._client = client
        self._owns_client = client is None
        self._logger = logging.getLogger(__name__)

    async def __aenter__(self) -> "HttpMulticaEventSink":
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None if self._owns_client else self._client

    async def publish_event(self, event: AgentEvent, context: TaskContext) -> None:
        url = context.callback_url or self.default_callback_url
        if not url:
            return
        await self._post(
            url,
            {
                "schema_version": "1",
                "kind": "agent_event",
                "task_id": context.task_id,
                "workspace_id": context.workspace_id,
                "request_id": context.request_id,
                "status": to_multica_status(context.status),
                "event": event.model_dump(mode="json"),
            },
        )

    async def publish_status(self, context: TaskContext) -> None:
        url = context.callback_url or self.default_callback_url
        if not url:
            return
        await self._post(
            url,
            {
                "schema_version": "1",
                "kind": "task_status",
                "task_id": context.task_id,
                "workspace_id": context.workspace_id,
                "request_id": context.request_id,
                "status": to_multica_status(context.status),
            },
        )

    async def _post(self, url: str, payload: dict[str, Any]) -> None:
        await self.start()
        assert self._client is not None
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = await self._client.post(url, json=payload, headers=headers)
                if response.status_code == 401:
                    raise MulticaAuthError(
                        "Multica callback authentication failed", context={"url": url}
                    )
                if response.status_code >= 500 or response.status_code == 429:
                    response.raise_for_status()
                elif response.status_code >= 400:
                    raise MulticaRejectedError(
                        "Multica callback rejected event",
                        context={"url": url, "status_code": response.status_code},
                    )
                return
            except MulticaAuthError:
                raise
            except MulticaProtocolError as exc:
                if not exc.retryable:
                    raise
                last_error = exc
                if attempt < self.retries:
                    await asyncio.sleep(min(2**attempt, 8))
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self.retries:
                    await asyncio.sleep(min(2**attempt, 8))
        raise MulticaProtocolError(
            "Multica callback failed after retries",
            context={"url": url, "error": str(last_error)},
        ) from last_error
