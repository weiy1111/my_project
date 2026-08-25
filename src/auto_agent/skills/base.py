from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from auto_agent.exceptions import SkillValidationError
from auto_agent.skills.models import SkillDescriptor, SkillExecutionContext


class BaseSkill(ABC):
    def __init__(
        self,
        *,
        name: str,
        description: str,
        provider: str,
        input_schema: dict[str, Any],
        output_schema: dict[str, Any] | None,
        timeout: float,
        required_env: list[str] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.provider = provider
        self.input_schema = input_schema
        self.output_schema = output_schema
        self.timeout = timeout
        self.required_env = sorted(set(required_env or []))
        self.environment_names = list(self.required_env)

    def descriptor(self) -> SkillDescriptor:
        return SkillDescriptor(
            name=self.name,
            description=self.description,
            provider=self.provider,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
            timeout=self.timeout,
            required_env=self.required_env,
        )

    @abstractmethod
    async def execute(self, arguments: dict[str, Any], context: SkillExecutionContext) -> Any:
        raise NotImplementedError


PythonSkillHandler = Callable[[BaseModel, SkillExecutionContext], Any | Awaitable[Any]]


class PythonSkill(BaseSkill):
    def __init__(
        self,
        *,
        name: str,
        description: str,
        input_model: type[BaseModel],
        output_model: type[BaseModel] | None,
        handler: PythonSkillHandler,
        timeout: float = 30,
        required_env: list[str] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            description=description,
            provider="python",
            input_schema=input_model.model_json_schema(),
            output_schema=output_model.model_json_schema() if output_model else None,
            timeout=timeout,
            required_env=required_env,
        )
        self.input_model = input_model
        self.output_model = output_model
        self.handler = handler

    async def execute(self, arguments: dict[str, Any], context: SkillExecutionContext) -> Any:
        try:
            validated_input = self.input_model.model_validate(arguments)
        except ValidationError as exc:
            raise SkillValidationError(
                "Skill 输入参数校验失败",
                context={"skill_name": self.name, "errors": exc.errors(include_url=False)},
            ) from exc
        output = self.handler(validated_input, context)
        if isinstance(output, Awaitable):
            output = await output
        if self.output_model is None:
            return output
        try:
            return self.output_model.model_validate(output).model_dump(mode="json")
        except ValidationError as exc:
            raise SkillValidationError(
                "Skill 输出结果校验失败",
                context={"skill_name": self.name, "errors": exc.errors(include_url=False)},
            ) from exc
