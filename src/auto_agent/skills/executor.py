from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from auto_agent.exceptions import SkillError, SkillExecutionError, SkillTimeoutError
from auto_agent.skills.models import (
    SkillEvent,
    SkillEventType,
    SkillExecutionContext,
    SkillExecutionResult,
)
from auto_agent.skills.registry import SkillRegistry

SkillEventSink = Callable[[SkillEvent], Awaitable[None]]


class SkillExecutor:
    def __init__(self, registry: SkillRegistry, *, max_concurrency: int = 4) -> None:
        self.registry = registry
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._sinks: list[SkillEventSink] = []

    def subscribe(self, sink: SkillEventSink) -> None:
        self._sinks.append(sink)

    async def execute(
        self,
        skill_name: str,
        arguments: dict[str, Any],
        *,
        context: SkillExecutionContext | None = None,
    ) -> SkillExecutionResult:
        skill = self.registry.get(skill_name)
        context = context or SkillExecutionContext()
        started = time.monotonic()
        await self._emit(
            SkillEvent(
                invocation_id=context.invocation_id,
                skill_name=skill_name,
                event_type=SkillEventType.STARTED,
            )
        )
        try:
            async with self._semaphore:
                output = await asyncio.wait_for(
                    skill.execute(arguments, context), timeout=skill.timeout
                )
        except asyncio.TimeoutError as exc:
            error = SkillTimeoutError(
                "Skill 执行超时",
                context={"skill_name": skill_name, "timeout": skill.timeout},
            )
            await self._emit_failure(context, skill_name, started, error)
            raise error from exc
        except asyncio.CancelledError:
            raise
        except SkillError as exc:
            await self._emit_failure(context, skill_name, started, exc)
            raise
        except Exception as exc:
            error = SkillExecutionError(
                "Skill 执行异常",
                context={"skill_name": skill_name, "error": str(exc)},
            )
            await self._emit_failure(context, skill_name, started, error)
            raise error from exc
        duration_ms = (time.monotonic() - started) * 1000
        await self._emit(
            SkillEvent(
                invocation_id=context.invocation_id,
                skill_name=skill_name,
                event_type=SkillEventType.COMPLETED,
                duration_ms=duration_ms,
            )
        )
        return SkillExecutionResult(
            invocation_id=context.invocation_id,
            skill_name=skill_name,
            output=output,
            duration_ms=duration_ms,
        )

    async def _emit_failure(
        self,
        context: SkillExecutionContext,
        skill_name: str,
        started: float,
        error: SkillError,
    ) -> None:
        await self._emit(
            SkillEvent(
                invocation_id=context.invocation_id,
                skill_name=skill_name,
                event_type=SkillEventType.FAILED,
                duration_ms=(time.monotonic() - started) * 1000,
                error=error.as_dict(),
            )
        )

    async def _emit(self, event: SkillEvent) -> None:
        for sink in self._sinks:
            try:
                await sink(event)
            except Exception:
                continue
