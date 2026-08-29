"""Pure builders for Feishu interactive cards used by IM channels."""

from __future__ import annotations

CARD_ACTION_CANCEL = "cancel"

_STATUS_TEMPLATES = {
    "running": "blue",
    "completed": "green",
    "cancelled": "grey",
    "timed_out": "red",
    "failed": "red",
}


def build_action_value(task_id: str, action: str = CARD_ACTION_CANCEL) -> dict[str, str]:
    """Button value payload embedded in interactive cards."""
    return {"task_id": task_id, "action": action}


def build_status_card(
    *,
    task_id: str,
    status: str,
    agent_name: str | None = None,
    allow_cancel_button: bool = True,
) -> dict:
    """Build an interactive card showing task status.

    The cancel button is only present while the task is running and when the
    transport can deliver card action callbacks.
    """
    title = f"任务状态 · {agent_name}" if agent_name else "任务状态"
    elements: list[dict] = [
        {"tag": "markdown", "content": f"**{status}**\n任务 ID：{task_id}"},
    ]
    if status == "running" and allow_cancel_button:
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "取消任务"},
                        "type": "danger",
                        "value": build_action_value(task_id),
                    }
                ],
            }
        )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": _STATUS_TEMPLATES.get(status, "blue"),
        },
        "elements": elements,
    }


def build_final_output_card(
    *,
    task_id: str,
    text: str,
    agent_name: str | None = None,
    max_chars: int = 8000,
) -> dict:
    """Build an interactive card rendering final output as markdown."""
    title = f"任务结果 · {agent_name}" if agent_name else "任务结果"
    content = text
    if len(text) > max_chars:
        content = text[:max_chars] + "\n\n…（内容过长已截断）"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "green",
        },
        "elements": [{"tag": "markdown", "content": content}],
    }
