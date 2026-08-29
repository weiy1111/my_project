"""iFinD 金融数据 skill wrapper for auto_agent.

这个 skill 把同花顺 iFinD 的金融数据查询能力封装为 auto_agent 的 Python skill，
暴露 `call` 和 `list_tools` 两个操作供 agent 调用。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Literal

import requests
from pydantic import BaseModel, Field

from auto_agent.skills.base import PythonSkill

# ===== iFinD 配置 =====
# ifind-finance-data 项目位置：F:\project\ifind-finance-data-1.4.0
CONFIG_PATH = Path(r"F:\project\ifind-finance-data-1.4.0\mcp_config.json")
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
AUTH_TOKEN = CONFIG["auth_token"]

BASE = "https://api-mcp.51ifind.com:8643/ds-mcp-servers"
SERVERS = {
    "stock": f"{BASE}/hexin-ifind-ds-stock-mcp",
    "fund": f"{BASE}/hexin-ifind-ds-fund-mcp",
    "edb": f"{BASE}/hexin-ifind-ds-edb-mcp",
    "news": f"{BASE}/hexin-ifind-ds-news-mcp",
    "bond": f"{BASE}/hexin-ifind-ds-bond-mcp",
    "global_stock": f"{BASE}/hexin-ifind-ds-global-stock-mcp",
    "index": f"{BASE}/hexin-ifind-ds-index-mcp",
    "future": f"{BASE}/hexin-ifind-ds-futures-mcp",
}

_sessions: dict[str, str] = {}
_req_ids: dict[str, int] = {}
_tool_sets: dict[str, set[str]] = {}
BLOCKED_KEYS = {"__proto__", "prototype", "constructor"}


def _next_id(server_type: str) -> int:
    _req_ids[server_type] = _req_ids.get(server_type, 0) + 1
    return _req_ids[server_type]


def _headers(server_type: str | None = None) -> dict[str, str]:
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": AUTH_TOKEN,
    }
    if server_type and server_type in _sessions:
        h["Mcp-Session-Id"] = _sessions[server_type]
    return h


def _post(server_type: str, payload: dict[str, Any], timeout: int = 60) -> tuple[requests.Response, Any]:
    resp = requests.post(
        SERVERS[server_type],
        json=payload,
        headers=_headers(server_type),
        verify=False,
        timeout=timeout,
    )
    data = None
    if resp.text.strip():
        try:
            data = resp.json()
        except Exception:
            data = resp.text
    return resp, data


def _validate_params(params: dict[str, Any]) -> None:
    if not isinstance(params, dict):
        raise TypeError("input must be a JSON object")

    def walk(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if key in BLOCKED_KEYS:
                    raise TypeError("input contains blocked field")
                walk(item)
            return
        if isinstance(value, float) and not math.isfinite(value):
            raise TypeError("input contains invalid number")
        if not isinstance(value, (str, int, float, bool)):
            raise TypeError("input contains unsupported value type")

    walk(params)
    json.dumps(params, allow_nan=False)


def _init(server_type: str) -> None:
    if server_type in _sessions:
        return

    payload = {
        "jsonrpc": "2.0",
        "id": _next_id(server_type),
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "auto_agent-ifind", "version": "1.0.0"},
        },
    }

    resp, data = _post(server_type, payload, timeout=30)
    res_payload = None
    if isinstance(data, dict):
        res_payload = data.get("result")
    resp.raise_for_status()
    if res_payload is None:
        raise RuntimeError(f"initialize 响应异常: {data}")

    session_id = resp.headers.get("Mcp-Session-Id")
    if not session_id:
        raise RuntimeError(f"initialize 成功但未返回 Mcp-Session-Id: {data}")

    _sessions[server_type] = session_id

    notify = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    requests.post(
        SERVERS[server_type],
        json=notify,
        headers=_headers(server_type),
        verify=False,
        timeout=10,
    )


# ===== 核心实现 =====

def _call_impl(server_type: str, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """调用 iFinD 金融数据工具。"""
    if server_type not in SERVERS:
        return {"ok": False, "error": f"unknown server_type: {server_type}"}

    try:
        _validate_params(params)
    except Exception as e:
        return {"ok": False, "error": f"参数校验失败: {e}"}

    try:
        allowed_tools = _load_tool_set(server_type)
    except Exception as e:
        return {"ok": False, "error": f"工具集加载失败: {e}"}
    if tool_name not in allowed_tools:
        return {"ok": False, "error": f"toolName not allowed for server_type {server_type}: {tool_name}"}

    payload = {
        "jsonrpc": "2.0",
        "id": _next_id(server_type),
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": params},
    }

    try:
        resp, data = _post(server_type, payload)
        if isinstance(data, dict) and "error" in data:
            return {"ok": False, "status_code": resp.status_code, "error": data["error"], "raw": data}
        resp.raise_for_status()
        return {"ok": True, "status_code": resp.status_code, "data": data}
    except requests.RequestException as e:
        return {"ok": False, "error": f"网络请求失败: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"调用异常: {e}"}


def _load_tool_set(server_type: str) -> set[str]:
    if server_type in _tool_sets:
        return _tool_sets[server_type]

    res = _list_tools_impl(server_type)
    tools = res.get("data", {}).get("result", {}).get("tools")
    if not isinstance(tools, list):
        raise RuntimeError("Invalid tools/list response")

    tool_set = {
        tool.get("name")
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("name"), str) and tool.get("name")
    }
    _tool_sets[server_type] = tool_set
    return tool_set


def _list_tools_impl(server_type: str) -> dict[str, Any]:
    """获取指定服务类型下可用的工具列表。"""
    if server_type not in SERVERS:
        return {"ok": False, "error": f"unknown server_type: {server_type}"}

    try:
        _init(server_type)
        payload = {
            "jsonrpc": "2.0",
            "id": _next_id(server_type),
            "method": "tools/list",
            "params": {},
        }
        resp, data = _post(server_type, payload)
        if isinstance(data, dict) and "error" in data:
            return {"ok": False, "status_code": resp.status_code, "error": data["error"], "raw": data}
        resp.raise_for_status()
        return {"ok": True, "status_code": resp.status_code, "data": data}
    except requests.RequestException as e:
        return {"ok": False, "error": f"网络请求失败: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"调用异常: {e}"}


# ===== Pydantic 输入/输出模型 =====

SERVER_TYPES = Literal["stock", "fund", "edb", "news", "bond", "global_stock", "index", "future"]


class IFindSkillInput(BaseModel):
    """iFinD skill 统一输入模型，通过 operation 字段区分操作。"""
    operation: Literal["call", "list_tools"]
    server_type: SERVER_TYPES
    tool_name: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class IFindSkillOutput(BaseModel):
    """iFinD skill 统一输出模型。"""
    ok: bool
    status_code: int | None = None
    data: Any | None = None
    error: str | None = None
    raw: Any | None = None


# ===== 统一 handler =====

async def ifind_skill_handler(input_data: IFindSkillInput, context) -> dict[str, Any]:
    """iFinD skill 统一处理器，返回原始 dict 供 PythonSkill 验证输出。"""
    if input_data.operation == "call":
        if not input_data.tool_name:
            return {"ok": False, "error": "call 操作需要 tool_name"}
        return _call_impl(input_data.server_type, input_data.tool_name, input_data.params)
    elif input_data.operation == "list_tools":
        return _list_tools_impl(input_data.server_type)
    return {"ok": False, "error": f"未知操作: {input_data.operation}"}


# ===== 向后兼容的同步函数 =====

def list_tools(server_type: str) -> dict[str, Any]:
    """向后兼容：获取指定服务类型下可用的工具列表。"""
    return _list_tools_impl(server_type)


def call(server_type: str, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """向后兼容：调用 iFinD 金融数据工具。"""
    return _call_impl(server_type, tool_name, params)


# ===== factory 入口点 =====

def create_ifind_finance_skill() -> PythonSkill:
    """创建 iFinD 金融数据 PythonSkill 实例。"""
    return PythonSkill(
        name="ifind_finance_data",
        description=(
            "同花顺 iFinD 金融数据查询，支持 A股/基金/债券/指数/宏观/港美股/期货期权等全市场数据，"
            "含智能选股、财务、行情、技术指标、资金流向、公告事件等。"
        ),
        input_model=IFindSkillInput,
        output_model=IFindSkillOutput,
        handler=ifind_skill_handler,
        timeout=60,
        required_env=[],
    )


def ifind_finance_skill_factory() -> PythonSkill:
    """PythonSkillConfig factory 入口点，返回 PythonSkill 实例。"""
    return create_ifind_finance_skill()


if __name__ == "__main__":
    import asyncio

    async def _test():
        skill = create_ifind_finance_skill()
        print("Skill created:", skill.name)
        print("Input schema:", skill.input_schema)
        from auto_agent.skills.models import SkillExecutionContext

        ctx = SkillExecutionContext(task_id="test", agent_name="test")
        result = await skill.execute({"operation": "list_tools", "server_type": "stock"}, ctx)
        print("list_tools result:", result)

    asyncio.run(_test())
