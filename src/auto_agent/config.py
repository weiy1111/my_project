from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from auto_agent.agents import AgentDefinition
from auto_agent.skills.models import SkillsConfig


class AgentsConfig(BaseModel):
    default_agent: str | None = None
    definitions: list[AgentDefinition] = Field(default_factory=list)


class HermesModelProviderConfig(BaseModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_mode: Literal["anthropic_messages", "chat_completions", "codex_responses"]
    api_key_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    hermes_api_key_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")


class OpenAICompatibleConfig(BaseModel):
    """直接调用 OpenAI 兼容 API 的配置，绕过 hermes-acp 子进程。"""

    enabled: bool = False
    api_base: str = Field(min_length=1, description="API 地址，如 http://model.mify.ai.srv/v1")
    api_key_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", description="API key 的环境变量名")
    model: str = Field(min_length=1, description="模型名称，如 xiaomi/mimo-v2.5-flash")
    system_prompt: str = ""
    timeout: float = Field(default=120, gt=0)
    max_tokens: int = Field(default=4096, gt=0)
    temperature: float = Field(default=0.7, ge=0, le=2)


class OpenCodeCliConfig(BaseModel):
    """通过 OpenCode CLI（opencode run）调用自带免费模型的配置。

    无需 API key；模型格式为 `provider/model`，例如 `opencode/mimo-v2.5-free`。
    """

    enabled: bool = False
    command: str = Field(default="opencode", min_length=1, description="opencode 可执行文件")
    model: str = Field(default="opencode/mimo-v2.5-free", min_length=1, description="模型，如 opencode/mimo-v2.5-free")
    system_prompt: str = ""
    timeout: float = Field(default=300, gt=0)
    working_directory: str | None = None
    environment: dict[str, str] = Field(default_factory=dict)


class HermesConfig(BaseModel):
    protocol: Literal["acp", "jsonl"] = "acp"
    acp_command: str = "hermes-acp"
    acp_args: list[str] = Field(default_factory=list)
    binary: str = "hermes-agent"
    working_directory: str | None = None
    environment: dict[str, str] = Field(default_factory=dict)
    startup_timeout: float = Field(default=30, gt=0)
    permission_mode: Literal["deny", "allow_once", "allow_session"] = "deny"
    model_provider: HermesModelProviderConfig | None = None
    max_concurrency: int = Field(default=4, gt=0)
    default_timeout: float = Field(default=1800, gt=0)


class MulticaConfig(BaseModel):
    gateway_url: str = ""
    callback_url: str | None = None
    token: str | None = None
    webhook_path: str = "/v1/tasks"
    callback_timeout: float = Field(default=10, gt=0)
    callback_retries: int = Field(default=2, ge=0)


class ImChannelConfig(BaseModel):
    enabled: bool = False
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    workspace_id: str = "default"
    default_agent: str | None = None
    reconnect: bool = True
    max_reconnect_attempts: int | None = Field(default=None, ge=0)
    webhook_path: str = Field(default="/v1/im/feishu/events", pattern=r"^/")
    base_url: str = "https://open.feishu.cn"
    app_id_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    app_secret_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    verification_token_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    encrypt_key_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    group_session_scope: Literal["chat", "sender", "thread"] = "sender"
    reply_mode: Literal["reply_message", "send_to_chat"] = "reply_message"
    respond_to_group_mentions_only: bool = True
    allowed_chats: list[str] = Field(default_factory=list)
    allowed_senders: list[str] = Field(default_factory=list)
    stream_events: bool = False
    max_message_chars: int = Field(default=8000, gt=0)
    max_clock_skew_seconds: int = Field(default=300, ge=0)


class AutoAgentConfig(BaseModel):
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    hermes: HermesConfig = Field(default_factory=HermesConfig)
    openai_compatible: OpenAICompatibleConfig = Field(default_factory=OpenAICompatibleConfig)
    opencode_cli: OpenCodeCliConfig = Field(default_factory=OpenCodeCliConfig)
    multica: MulticaConfig = Field(default_factory=MulticaConfig)
    im_channels: dict[str, ImChannelConfig] = Field(default_factory=dict)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)


def load_config(path: str | Path) -> AutoAgentConfig:
    with Path(path).open(encoding="utf-8") as file:
        data: dict[str, Any] = yaml.safe_load(file) or {}
    return AutoAgentConfig.model_validate(data)
