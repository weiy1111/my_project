from __future__ import annotations

import importlib
from collections.abc import Iterable

from auto_agent.exceptions import (
    DuplicateSkillError,
    SkillConfigurationError,
    SkillNotFoundError,
)
from auto_agent.skills.base import BaseSkill
from auto_agent.skills.cli import CliSkill
from auto_agent.skills.models import (
    CliSkillConfig,
    PythonSkillConfig,
    SkillDescriptor,
    SkillsConfig,
)


class SkillRegistry:
    def __init__(self, skills: Iterable[BaseSkill] = ()) -> None:
        self._skills: dict[str, BaseSkill] = {}
        for skill in skills:
            self.register(skill)

    def register(self, skill: BaseSkill, *, replace: bool = False) -> None:
        if skill.name in self._skills and not replace:
            raise DuplicateSkillError("Skill 已注册", context={"skill_name": skill.name})
        self._skills[skill.name] = skill

    def get(self, name: str) -> BaseSkill:
        try:
            return self._skills[name]
        except KeyError as exc:
            raise SkillNotFoundError("Skill 不存在", context={"skill_name": name}) from exc

    def names(self) -> set[str]:
        return set(self._skills)

    def list(self, allowed: Iterable[str] | None = None) -> list[SkillDescriptor]:
        allowed_names = set(allowed) if allowed is not None else None
        return [
            skill.descriptor()
            for name, skill in sorted(self._skills.items())
            if allowed_names is None or name in allowed_names
        ]


def build_skill_registry(config: SkillsConfig) -> SkillRegistry:
    registry = SkillRegistry()
    for definition in config.definitions:
        if not definition.enabled:
            continue
        if isinstance(definition, CliSkillConfig):
            skill = CliSkill(definition)
        elif isinstance(definition, PythonSkillConfig):
            skill = _load_python_skill(definition)
        else:  # pragma: no cover - discriminated Pydantic union prevents this
            raise SkillConfigurationError("未知 Skill provider")
        registry.register(skill)
    return registry


def _load_python_skill(config: PythonSkillConfig) -> BaseSkill:
    module_name, attribute_name = config.factory.split(":", 1)
    try:
        module = importlib.import_module(module_name)
        factory = getattr(module, attribute_name)
        skill = factory()
    except Exception as exc:
        raise SkillConfigurationError(
            "Python Skill 工厂加载失败",
            context={"skill_name": config.name, "factory": config.factory, "error": str(exc)},
        ) from exc
    if not isinstance(skill, BaseSkill):
        raise SkillConfigurationError(
            "Python Skill 工厂必须返回 BaseSkill",
            context={"skill_name": config.name, "factory": config.factory},
        )
    if skill.name != config.name:
        raise SkillConfigurationError(
            "Python Skill 名称与配置不一致",
            context={"configured": config.name, "factory": skill.name},
        )
    if config.description:
        skill.description = config.description
    skill.timeout = config.timeout
    skill.required_env = sorted(set([*skill.required_env, *config.required_env]))
    skill.environment_names = sorted(set([*skill.environment_names, *config.required_env]))
    return skill
