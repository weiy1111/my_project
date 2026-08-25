from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import jsonschema

from auto_agent.exceptions import (
    SkillConfigurationError,
    SkillExecutionError,
    SkillValidationError,
)
from auto_agent.skills.base import BaseSkill
from auto_agent.skills.models import CliSkillConfig, SkillExecutionContext


class CliSkill(BaseSkill):
    """Fixed-command JSON stdin/stdout Skill. Shell expansion is never used."""

    def __init__(self, config: CliSkillConfig) -> None:
        required_env = list(config.secret_env.values())
        super().__init__(
            name=config.name,
            description=config.description,
            provider="cli",
            input_schema=config.input_schema,
            output_schema=config.output_schema,
            timeout=config.timeout,
            required_env=required_env,
        )
        self.config = config
        self.environment_names = sorted(set([*config.pass_env, *config.secret_env.values()]))
        try:
            jsonschema.Draft202012Validator.check_schema(config.input_schema)
            if config.output_schema is not None:
                jsonschema.Draft202012Validator.check_schema(config.output_schema)
        except jsonschema.SchemaError as exc:
            raise SkillConfigurationError(
                "CLI Skill JSON Schema 无效",
                context={"skill_name": config.name, "error": exc.message},
            ) from exc

    async def execute(self, arguments: dict[str, Any], context: SkillExecutionContext) -> Any:
        self._validate(arguments, self.input_schema, "输入")
        process = await asyncio.create_subprocess_exec(
            *self.config.command,
            cwd=self._cwd(),
            env=self._environment(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        payload = json.dumps(
            {
                "arguments": arguments,
                "context": context.model_dump(mode="json"),
            },
            ensure_ascii=False,
        ).encode()
        try:
            stdout, stderr = await self._communicate(process, payload)
        except asyncio.CancelledError:
            if process.returncode is None:
                process.kill()
                await self._wait_for_exit(process)
            raise
        if len(stdout) > self.config.max_output_bytes:
            raise SkillExecutionError(
                "CLI Skill 输出超过限制",
                context={"skill_name": self.name, "max_output_bytes": self.config.max_output_bytes},
            )
        if process.returncode != 0:
            raise SkillExecutionError(
                "CLI Skill 执行失败",
                context={
                    "skill_name": self.name,
                    "returncode": process.returncode,
                },
            )
        text = stdout.decode(errors="strict")
        if self.config.output_format == "text":
            output: Any = text.rstrip("\n")
        else:
            try:
                output = json.loads(text)
            except json.JSONDecodeError as exc:
                raise SkillExecutionError(
                    "CLI Skill 未返回合法 JSON",
                    context={"skill_name": self.name},
                ) from exc
        if self.output_schema is not None:
            self._validate(output, self.output_schema, "输出")
        return output

    async def _communicate(
        self,
        process: asyncio.subprocess.Process,
        payload: bytes,
    ) -> tuple[bytes, bytes]:
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise SkillExecutionError(
                "CLI Skill 子进程管道不可用", context={"skill_name": self.name}
            )
        process.stdin.write(payload)
        await process.stdin.drain()
        process.stdin.close()
        stdout, stderr, _ = await asyncio.gather(
            self._read_limited(process.stdout),
            self._read_limited(process.stderr),
            self._wait_for_exit(process),
        )
        return stdout, stderr

    @staticmethod
    async def _wait_for_exit(process: asyncio.subprocess.Process) -> int:
        # Process.wait() can miss the child-watcher notification on Python
        # 3.10 when many short-lived event loops are created by callers.
        while process.returncode is None:
            await asyncio.sleep(0.01)
        return process.returncode

    async def _read_limited(self, stream: asyncio.StreamReader) -> bytes:
        retained = bytearray()
        limit = self.config.max_output_bytes + 1
        while chunk := await stream.read(64 * 1024):
            if len(retained) < limit:
                retained.extend(chunk[: limit - len(retained)])
        return bytes(retained)

    def _cwd(self) -> str | None:
        if self.config.cwd is None:
            return None
        return str(Path(self.config.cwd).expanduser().resolve())

    def _environment(self) -> dict[str, str]:
        environment = dict(self.config.environment)
        for name in self.config.pass_env:
            if value := os.environ.get(name):
                environment[name] = value
        for target, source in self.config.secret_env.items():
            value = os.environ.get(source)
            if not value:
                raise SkillConfigurationError(
                    "CLI Skill 所需环境变量未设置",
                    context={"skill_name": self.name, "environment": source},
                )
            environment[target] = value
        return environment

    def _validate(self, value: Any, schema: dict[str, Any], direction: str) -> None:
        try:
            jsonschema.validate(value, schema)
        except jsonschema.ValidationError as exc:
            raise SkillValidationError(
                f"CLI Skill {direction}校验失败",
                context={"skill_name": self.name, "path": list(exc.path), "error": exc.message},
            ) from exc
