"""定时股票分析工作流调度器（独立于 auto_agent server 运行）。

每个交易日（周一~周五）在三个时段触发一次对目标股票的 A-share 分析：
  09:30 盘前 = 基于昨日/隔夜收盘与盘前消息做日内预判
  12:00 盘中 = 实时行情 + 当日量价/资金
  15:30 盘后 = 当日收盘总结 + 次日展望

流程：
  1. POST /v1/tasks -> 让 a_stock agent 生成 HTML 报告文件到归档目录并返回路径+摘要
  2. 轮询 GET /v1/tasks/{id} 直到终态
  3. 解析结果文本里的 REPORT_FILE:<path> 行拿到 HTML 路径
  4. POST /v1/im/send -> 把 HTML 文件 + 文本摘要主动推到飞书群

依赖：仅标准库 + httpx（auto_agent 已依赖）。
运行：E:\\anaconda\\python.exe scheduler.py
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import sys
import time

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("stock_scheduler")

# ---------------------------------------------------------------- config
SERVER_BASE = "http://127.0.0.1:8080"
AGENT_NAME = "a_stock"
CHAT_ID = "oc_7d6db58ccec600783e4ad1cd1f84b419"
WORKSPACE_ID = "stock_scheduler"          # MulticaTaskRequest.workspace_id 必填
ARCHIVE_DIR = r"F:\project\auto_agent\reports"
TARGET_STOCKS = "多氟多、中国巨石、长电科技"

# 时段: (HH, MM, 时段名, 侧重说明)
SESSIONS = [
    (9, 30, "盘前", "基于昨日/隔夜收盘与盘前消息做日内预判"),
    (12, 0, "盘中", "实时行情 + 当日量价/资金流向"),
    (15, 30, "盘后", "当日收盘总结 + 次日展望"),
]

TASK_POLL_TIMEOUT = 60 * 40        # 单次任务最多等待 40 分钟
TASK_POLL_INTERVAL = 10            # 轮询间隔（秒）
LONG_RUN_TIMEOUT = 1800            # 传给 server 的 task timeout（秒）

_REPORT_LINE = re.compile(r"^\s*REPORT_FILE:\s*(.+?)\s*$", re.MULTILINE)


def _now() -> dt.datetime:
    return dt.datetime.now()


def _path_for(date: dt.date, session_name: str) -> str:
    return f"{ARCHIVE_DIR}\\{date.strftime('%Y%m%d')}_{session_name}.html".replace("\\", "\\\\")


def _build_prompt(html_path: str, session_name: str, focus: str) -> str:
    return (
        f"请为 {TARGET_STOCKS} 生成一份《{session_name}复盘报告》HTML 文件。\n"
        f"本时段侧重：{focus}。\n"
        f"请把整份报告写成 HTML 文件，目标路径：{html_path}\n"
        "（使用 file_write 工具写入，UTF-8、自包含内联 CSS、中文、美观排版）。\n"
        "文件写好后，回复必须以 REPORT_FILE:<该 HTML 绝对路径> 开头，"
        "随后给出一段 120 字以内的中文纯文本摘要，供飞书群消息展示。"
    )


def _submit_task(client: httpx.Client, html_path: str, session_name: str, focus: str) -> str:
    """POST /v1/tasks，返回 task_id。"""
    body = {
        "workspace_id": WORKSPACE_ID,
        "agent_name": AGENT_NAME,
        "prompt": _build_prompt(html_path, session_name, focus),
        "timeout": LONG_RUN_TIMEOUT,
    }
    resp = client.post(f"{SERVER_BASE}/v1/tasks", json=body)
    resp.raise_for_status()
    task_id = resp.json()["task_id"]
    logger.info("已提交任务 %s (%s)", task_id, session_name)
    return task_id


def _wait_until_terminal(client: httpx.Client, task_id: str, deadline: float) -> dict:
    """轮询 GET /v1/tasks/{id} 直到终态，返回 TaskContext JSON。"""
    while time.monotonic() < deadline:
        resp = client.get(f"{SERVER_BASE}/v1/tasks/{task_id}")
        resp.raise_for_status()
        ctx = resp.json()
        status = ctx.get("status")
        if status in {"completed", "failed", "cancelled", "timed_out"}:
            logger.info("任务 %s 终态: %s", task_id, status)
            return ctx
        time.sleep(TASK_POLL_INTERVAL)
    raise TimeoutError(f"task {task_id} 轮询超时")


def _extract(path: str, result: dict | None) -> tuple[str | None, str]:
    """从任务结果中解析 REPORT_FILE 行和摘要文本。

    返回 (报告文件路径, 摘要)。找不到 REPORT_FILE 时路径为 None，原样返回全文。
    """
    text = ""
    if isinstance(result, dict):
        text = str(result.get("text") or "")
    text = text.strip()
    if not text:
        return None, ""
    m = _REPORT_LINE.search(text)
    if not m:
        return None, text[:500]
    report_path = m.group(1).strip()
    summary = (text[: m.start()] + text[m.end():]).strip()
    summary = re.sub(r"\s*\n+\s*", "\n", summary)[:500]
    return report_path, summary


def _run_once(session_name: str, focus: str) -> None:
    day = _now().date()
    html_path = _path_for(day, session_name)
    with httpx.Client(timeout=30) as client:
        task_id = _submit_task(client, html_path, session_name, focus)
        try:
            ctx = _wait_until_terminal(client, task_id, time.monotonic() + TASK_POLL_TIMEOUT)
        except (TimeoutError, httpx.HTTPError) as exc:
            logger.error("任务 %s 等待失败: %s", task_id, exc)
            return
        status = ctx.get("status")
        if status != "completed":
            logger.warning("任务 %s 未完成（%s），不发送：%s", task_id, status, ctx.get("error"))
            return
        report_path, summary = _extract(html_path, ctx.get("result"))
        # 模型写出的路径若存在则采用，否则回退到调度器预生成的路径
        final_file = report_path if report_path and _file_exists(report_path) else (
            html_path if _file_exists(html_path) else None
        )
        if not final_file:
            logger.warning("未找到生成的 HTML 报告（task=%s），只发文本摘要", task_id)
        payload: dict = {"chat_id": CHAT_ID}
        if summary:
            payload["text"] = f"【{session_name}复盘】{TARGET_STOCKS}\n{summary}"
        if final_file:
            payload["file_path"] = final_file
        if not (summary or final_file):
            logger.warning("无任何可发送内容（task=%s）", task_id)
            return
        resp = client.post(f"{SERVER_BASE}/v1/im/send", json=payload)
        resp.raise_for_status()
        logger.info("已发送到群 %s: %s", CHAT_ID, resp.json())


def _file_exists(path: str) -> bool:
    try:
        import os

        return os.path.isfile(path.replace("\\\\", "\\"))
    except Exception:
        return False


def _should_tick(last_trigger: dict, now: dt.datetime) -> tuple[str | None, str | None]:
    """返回 (时段名, 侧重说明)；命中且当日该时段未触发时返回，否则 (None, None)。"""
    if now.weekday() >= 5:
        return None, None
    key = now.strftime("%Y%m%d")
    for hh, mm, name, focus in SESSIONS:
        if now.hour == hh and now.minute == mm:
            if last_trigger.get(name) != key:
                return name, focus
    return None, None


def main() -> None:
    logger.info("股票定时分析调度器启动：target=%s chat=%s", TARGET_STOCKS, CHAT_ID)
    last_trigger: dict[str, str] = {}
    try:
        while True:
            name, focus = _should_tick(last_trigger, _now())
            if name:
                last_trigger[name] = _now().strftime("%Y%m%d")
                logger.info("触发 %s 时段分析", name)
                try:
                    _run_once(name, focus)
                except Exception as exc:
                    logger.exception("本次 %s 分析失败: %s", name, exc)
            time.sleep(20)
    except KeyboardInterrupt:
        logger.info("调度器已停止")


if __name__ == "__main__":
    sys.exit(main())
