#!/usr/bin/env python3
from __future__ import annotations
"""工作流总结 → 飞书推送

运行完整的股票发现扫描，生成 Markdown 总结，通过 lark-cli 发送到飞书私信。

用法:
  # 默认扫描即时资金流，发送给你自己
  python scripts/workflow_summary.py

  # 指定周期和 Top N
  python scripts/workflow_summary.py --period 5日排行 --top 10

  # 只生成总结不发送（调试用）
  python scripts/workflow_summary.py --dry-run

  # 发送到群聊
  python scripts/workflow_summary.py --chat-id oc_xxxxxxxx

环境要求:
  1. 已安装 lark-cli: npm install -g @larksuite/cli
  2. 已完成认证: lark-cli auth login
  3. 已安装项目依赖: pip install -r requirements.txt
"""
import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from discovery.scorer import DiscoveryFilters, discover_stocks


# ===== 飞书用户配置 =====
# 你的飞书 open_id（从 lark-cli config show 获取）
DEFAULT_USER_ID = "ou_5ba3ae2c943ce54e8cb514eab9f8faf8"


def _fmt_amount(value: float) -> str:
    """格式化金额：亿/万"""
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1e8:
        return f"{sign}{value / 1e8:.2f}亿"
    if value >= 1e4:
        return f"{sign}{value / 1e4:.2f}万"
    return f"{sign}{value:.0f}"


def _fmt_score_bar(score: float) -> str:
    """分数转文本等级"""
    if score >= 80:
        return "⭐ 强烈关注"
    elif score >= 65:
        return "🔵 候选关注"
    elif score >= 50:
        return "⚪ 等待确认"
    else:
        return "⚫ 暂不优先"


def run_discovery(period: str = "即时", limit: int = 40, top: int = 10, min_score: float = 0) -> dict:
    """运行股票发现扫描"""
    print(f"[工作流] 开始扫描... 周期={period}, Top={top}")
    result = discover_stocks(DiscoveryFilters(
        period=period,
        limit=limit,
        min_score=min_score,
    ))
    print(f"[工作流] 扫描完成，候选 {len(result['items'])} 只")
    return result


def build_markdown_summary(result: dict, top: int = 10) -> str:
    """将扫描结果格式化为飞书 Markdown 消息"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    items = result["items"][:top]
    summary = result.get("summary", {})

    lines = [
        f"## 📊 大科技股票发现报告",
        f"",
        f"**扫描时间**: {now}",
        f"**资金周期**: {result['period']}",
        f"**科技股票池**: {result.get('tech_pool_size', 0)} 只",
        f"**候选数量**: {summary.get('count', 0)} 只 | **净流入**: {_fmt_amount(summary.get('total_main_net', 0))}",
        f"",
        f"---",
        f"",
    ]

    if not items:
        lines.append("⚠️ 本次扫描无符合条件的候选股票")
        return "\n".join(lines)

    # Top 候选列表
    lines.append(f"### 🏆 Top {len(items)} 候选")
    lines.append("")

    for i, item in enumerate(items, 1):
        ai = item.get("ai", {})
        code = item["code"]
        name = item.get("name", "")
        price = item["price"]
        pct = item["pct_change"]
        main_net = item["main_net"]
        main_pct = item["main_pct"]
        score = item["score"]
        tomorrow_score = item.get("tomorrow_score", 0)
        action = item.get("tomorrow_action", ai.get("action", "-"))

        pct_emoji = "📈" if pct >= 0 else "📉"
        lines.append(f"**{i}. {code} {name}**  {_fmt_score_bar(score)}")
        lines.append(f"   - 价格: ¥{price:.2f} {pct_emoji} {pct:+.2f}%")
        lines.append(f"   - 主力净额: {_fmt_amount(main_net)} ({main_pct:+.2f}%)")
        lines.append(f"   - 3日资金: {_fmt_amount(item.get('main_net_3d', 0))} | 30日: {_fmt_amount(item.get('main_net_30d', 0))}")
        lines.append(f"   - 综合评分: {score:.1f} | 建仓评分: {tomorrow_score:.1f}")
        lines.append(f"   - 明日建议: **{action}**")
        lines.append("")

    # 最佳候选解读
    best = items[0]
    best_ai = best.get("ai", {})
    lines.append("---")
    lines.append("")
    lines.append(f"### 🎯 最佳候选解读: {best['code']} {best.get('name', '')}")
    lines.append("")
    lines.append(f"> {best_ai.get('summary', '')}")
    lines.append("")

    strengths = best_ai.get("strengths", [])
    if strengths:
        lines.append("**入选理由**:")
        for s in strengths:
            lines.append(f"- ✅ {s}")
        lines.append("")

    risks = best_ai.get("risks", [])
    if risks:
        lines.append("**风险提示**:")
        for r in risks:
            lines.append(f"- ⚠️ {r}")
        lines.append("")

    watch = best_ai.get("watch", [])
    if watch:
        lines.append("**观察点**:")
        for w in watch:
            lines.append(f"- 👀 {w}")
        lines.append("")

    # 买入时机
    buy_timing = best.get("buy_timing")
    if buy_timing:
        lines.append("**买入参考**:")
        if buy_timing.get("buy_zone"):
            lines.append(f"- 观察区间: {buy_timing['buy_zone']}")
        if buy_timing.get("trigger"):
            lines.append(f"- 触发条件: {buy_timing['trigger']}")
        if buy_timing.get("stop_loss"):
            lines.append(f"- 止损参考: {buy_timing['stop_loss']}")
        lines.append("")

    # 风险汇总
    risk_items = [item for item in items if item.get("risk_score", 0) >= 50]
    if risk_items:
        lines.append("---")
        lines.append("")
        lines.append(f"### ⚠️ 风险提醒 ({len(risk_items)} 只评分偏高)")
        for item in risk_items[:3]:
            lines.append(f"- {item['code']} {item.get('name', '')} 风险分: {item.get('risk_score', 0):.0f}")
        lines.append("")

    # 免责声明
    lines.append("---")
    lines.append("*以上分析仅供参考，不构成投资建议。投资有风险，入市需谨慎。*")

    return "\n".join(lines)


def _find_lark_cli() -> str:
    """查找 lark-cli 可执行文件路径（兼容 conda 环境 PATH 不含 npm 的情况）"""
    import shutil
    # 优先从 PATH 找
    found = shutil.which("lark-cli")
    if found:
        return found
    # 兜底：Windows npm 全局目录
    npm_global = Path.home() / "AppData" / "Roaming" / "npm" / "lark-cli.cmd"
    if npm_global.exists():
        return str(npm_global)
    return "lark-cli"  # 让 subprocess 报错


def send_via_lark_cli(markdown: str, user_id: str | None = None, chat_id: str | None = None, dry_run: bool = False) -> bool:
    """通过 lark-cli 发送 Markdown 消息到飞书"""
    lark_bin = _find_lark_cli()

    target_args = []
    if chat_id:
        target_args = ["--chat-id", chat_id]
    elif user_id:
        target_args = ["--user-id", user_id]
    else:
        target_args = ["--user-id", DEFAULT_USER_ID]

    cmd = [
        lark_bin, "im", "+messages-send",
        *target_args,
        "--as", "bot",
        "--markdown", markdown,
    ]

    if dry_run:
        cmd.append("--dry-run")
        print("[工作流] Dry-run 模式，不实际发送")

    print(f"[工作流] 执行: lark-cli im +messages-send ... --as bot --markdown <{len(markdown)} chars>")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
        )

        if result.returncode == 0:
            print(f"[工作流] ✅ 发送成功")
            if result.stdout:
                print(result.stdout)
            return True
        else:
            print(f"[工作流] ❌ 发送失败 (exit={result.returncode})")
            if result.stderr:
                print(result.stderr)
            if result.stdout:
                print(result.stdout)
            return False

    except FileNotFoundError:
        print("[工作流] ❌ lark-cli 未安装，请运行: npm install -g @larksuite/cli")
        return False
    except subprocess.TimeoutExpired:
        print("[工作流] ❌ lark-cli 执行超时")
        return False
    except Exception as e:
        print(f"[工作流] ❌ 异常: {e}")
        return False


def main():
    # 修复 Windows 控制台 GBK 编码问题
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="股票发现工作流 → 飞书总结推送")
    parser.add_argument("--period", default="即时", choices=["即时", "3日排行", "5日排行", "10日排行"],
                        help="资金流排行周期")
    parser.add_argument("--limit", type=int, default=40, help="资金流候选数量")
    parser.add_argument("--top", type=int, default=10, help="总结中展示前N只")
    parser.add_argument("--min-score", type=float, default=0, help="最低综合评分")
    parser.add_argument("--user-id", default=None, help="飞书用户 open_id (默认发给自己)")
    parser.add_argument("--chat-id", default=None, help="飞书群聊 chat_id (oc_xxx)")
    parser.add_argument("--dry-run", action="store_true", help="只生成总结不发送")
    parser.add_argument("--output", default=None, help="同时保存总结到文件")
    args = parser.parse_args()

    # 1. 运行扫描
    result = run_discovery(
        period=args.period,
        limit=args.limit,
        top=args.top,
        min_score=args.min_score,
    )

    # 2. 生成 Markdown
    markdown = build_markdown_summary(result, top=args.top)

    # 3. 保存到文件（可选）
    if args.output:
        Path(args.output).write_text(markdown, encoding="utf-8")
        print(f"[工作流] 总结已保存到: {args.output}")

    # 4. 打印预览
    print("\n" + "=" * 60)
    print("消息预览:")
    print("=" * 60)
    print(markdown)
    print("=" * 60 + "\n")

    # 5. 发送到飞书
    if not args.dry_run:
        ok = send_via_lark_cli(
            markdown,
            user_id=args.user_id,
            chat_id=args.chat_id,
            dry_run=False,
        )
        sys.exit(0 if ok else 1)
    else:
        print("[工作流] Dry-run 完成，未发送消息")


if __name__ == "__main__":
    main()
