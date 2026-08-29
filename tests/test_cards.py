from auto_agent.im_channels.cards import (
    CARD_ACTION_CANCEL,
    build_action_value,
    build_final_output_card,
    build_status_card,
)
from auto_agent.models import ImAction


def test_status_card_running_has_cancel_button() -> None:
    card = build_status_card(task_id="t1", status="running", agent_name="code_review")
    actions = [element for element in card["elements"] if element["tag"] == "action"]
    assert len(actions) == 1
    button = actions[0]["actions"][0]
    assert button["value"] == {"task_id": "t1", "action": CARD_ACTION_CANCEL}
    assert card["header"]["template"] == "blue"


def test_status_card_terminal_states_have_no_button() -> None:
    for status in ("completed", "cancelled", "timed_out", "failed"):
        card = build_status_card(task_id="t1", status=status)
        assert all(element["tag"] != "action" for element in card["elements"]), status


def test_status_card_template_mapping() -> None:
    expected = {
        "running": "blue",
        "completed": "green",
        "cancelled": "grey",
        "timed_out": "red",
        "failed": "red",
        "unknown": "blue",
    }
    for status, template in expected.items():
        card = build_status_card(task_id="t1", status=status)
        assert card["header"]["template"] == template, status


def test_final_output_card_truncates_long_text() -> None:
    card = build_final_output_card(task_id="t1", text="x" * 100, max_chars=50)
    content = card["elements"][0]["content"]
    assert content.startswith("x" * 50)
    assert "已截断" in content


def test_final_output_card_keeps_short_text() -> None:
    card = build_final_output_card(task_id="t1", text="done", agent_name="review")
    assert card["elements"][0]["content"] == "done"
    assert "review" in card["header"]["title"]["content"]


def test_action_value_default_is_cancel() -> None:
    assert build_action_value("t1") == {"task_id": "t1", "action": CARD_ACTION_CANCEL}


def test_im_action_roundtrip_preserves_fields() -> None:
    action = ImAction(
        channel_id="feishu_webhook",
        session_id="oc_chat",
        sender="ou_user",
        action=CARD_ACTION_CANCEL,
        task_id="t1",
        message_id="om_1",
        chat_id="oc_chat",
        metadata={"source": "card"},
    )
    restored = ImAction.model_validate(action.model_dump())
    assert restored == action
