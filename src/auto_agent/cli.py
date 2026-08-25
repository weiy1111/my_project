import argparse
import os

import uvicorn

from auto_agent.agents import AgentRegistry
from auto_agent.config import load_config
from auto_agent.gateways.multica import HttpMulticaEventSink
from auto_agent.http import create_app
from auto_agent.im_channels import (
    BaseWebhookImChannel,
    FeishuCliChannel,
    FeishuWebhookChannel,
    ImTaskBridge,
)
from auto_agent.task_manager import TaskManager
from auto_agent.workers import HermesACPWorker, HermesAgentWorker


def _required_env(env_name: str | None, field_name: str) -> str:
    if not env_name:
        raise RuntimeError(f"{field_name} must name an environment variable")
    value = os.environ.get(env_name)
    if not value:
        raise RuntimeError(f"required environment variable is not set: {env_name}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the auto_agent HTTP bridge")
    parser.add_argument("--config", default="configs/auto_agent.yaml")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    config = load_config(args.config)
    agent_registry = AgentRegistry(
        config.agents.definitions,
        default_agent=config.agents.default_agent,
    )
    if config.hermes.protocol == "acp":
        worker = HermesACPWorker(
            config.hermes.acp_command,
            config.hermes.acp_args,
            cwd=config.hermes.working_directory,
            env=config.hermes.environment,
            startup_timeout=config.hermes.startup_timeout,
            permission_mode=config.hermes.permission_mode,
            model_provider=(
                config.hermes.model_provider.model_dump()
                if config.hermes.model_provider is not None
                else None
            ),
        )
    else:
        worker = HermesAgentWorker(config.hermes.binary)
    manager = TaskManager(
        worker,
        agent_registry=agent_registry,
        max_concurrency=config.hermes.max_concurrency,
        default_timeout=config.hermes.default_timeout,
    )
    sink = HttpMulticaEventSink(
        default_callback_url=config.multica.callback_url,
        token=config.multica.token,
        timeout=config.multica.callback_timeout,
        retries=config.multica.callback_retries,
    )
    components: list[object] = []
    webhook_channels: list[BaseWebhookImChannel] = []
    feishu_config = config.im_channels.get("feishu_cli")
    if feishu_config and feishu_config.enabled and feishu_config.command:
        channel = FeishuCliChannel(
            feishu_config.command,
            feishu_config.args,
            reconnect=feishu_config.reconnect,
            max_reconnect_attempts=feishu_config.max_reconnect_attempts,
        )
        components.append(
            ImTaskBridge(
                channel,
                manager,
                workspace_id=feishu_config.workspace_id,
                default_agent=feishu_config.default_agent,
            )
        )
    webhook_config = config.im_channels.get("feishu_webhook")
    if webhook_config and webhook_config.enabled:
        encrypt_key = None
        if webhook_config.encrypt_key_env:
            encrypt_key = _required_env(
                webhook_config.encrypt_key_env,
                "im_channels.feishu_webhook.encrypt_key_env",
            )
        webhook_channel = FeishuWebhookChannel(
            app_id=_required_env(
                webhook_config.app_id_env,
                "im_channels.feishu_webhook.app_id_env",
            ),
            app_secret=_required_env(
                webhook_config.app_secret_env,
                "im_channels.feishu_webhook.app_secret_env",
            ),
            verification_token=_required_env(
                webhook_config.verification_token_env,
                "im_channels.feishu_webhook.verification_token_env",
            ),
            encrypt_key=encrypt_key,
            webhook_path=webhook_config.webhook_path,
            base_url=webhook_config.base_url,
            group_session_scope=webhook_config.group_session_scope,
            reply_mode=webhook_config.reply_mode,
            respond_to_group_mentions_only=webhook_config.respond_to_group_mentions_only,
            stream_events=webhook_config.stream_events,
            max_message_chars=webhook_config.max_message_chars,
            max_clock_skew_seconds=webhook_config.max_clock_skew_seconds,
        )
        webhook_channels.append(webhook_channel)
        components.append(
            ImTaskBridge(
                webhook_channel,
                manager,
                workspace_id=webhook_config.workspace_id,
                default_agent=webhook_config.default_agent,
            )
        )
    uvicorn.run(
        create_app(
            manager,
            multica_sink=sink,
            components=components,
            webhook_channels=webhook_channels,
        ),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
