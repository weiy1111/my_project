#!/usr/bin/env python3
from __future__ import annotations

"""List curated external A-share GitHub skill candidates."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from discovery.external_skills import categories, list_external_skills
from runtime_compat import configure_stdio


def _shorten(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    return text[: max(0, width - 3)] + "..."


def _print_table(items):
    if not items:
        print("没有匹配的外部 A股 skill 候选。")
        return

    print(f"{'分数':>4} {'类别':<12} {'Codex':<6} {'项目':<34} {'用途':<42}")
    print("-" * 106)
    for item in items:
        print(
            f"{item.fit_score:>4} "
            f"{item.category:<12} "
            f"{'yes' if item.codex_ready else 'no':<6} "
            f"{_shorten(item.name, 34):<34} "
            f"{_shorten(item.use_case, 42):<42}"
        )
    print("\n使用 --format markdown 查看URL、接入建议和风险。")


def _print_markdown(items):
    if not items:
        print("没有匹配的外部 A股 skill 候选。")
        return

    for item in items:
        sources = ", ".join(item.data_sources)
        print(f"## {item.name}")
        print(f"- URL: {item.url}")
        print(f"- Fit score: {item.fit_score}")
        print(f"- Category: {item.category}")
        print(f"- Codex ready: {'yes' if item.codex_ready else 'no'}")
        print(f"- Data sources: {sources}")
        print(f"- Use case: {item.use_case}")
        print(f"- Integration: {item.integration_action}")
        print(f"- Risks: {item.risks}")
        print()


def main():
    configure_stdio()
    parser = argparse.ArgumentParser(description="列出适配A股股票发现的GitHub skill候选")
    parser.add_argument("--category", choices=categories(), default="", help="按类别过滤")
    parser.add_argument("--min-score", type=int, default=0, help="最低适配分")
    parser.add_argument("--codex-ready", action="store_true", help="只显示明确适合Codex/skill接入的候选")
    parser.add_argument("--top", type=int, default=0, help="只展示前N个")
    parser.add_argument("--format", choices=["table", "markdown"], default="table")
    args = parser.parse_args()

    items = list_external_skills(
        category=args.category,
        min_score=args.min_score,
        codex_ready_only=args.codex_ready,
    )
    if args.top > 0:
        items = items[: args.top]

    if args.format == "markdown":
        _print_markdown(items)
    else:
        _print_table(items)


if __name__ == "__main__":
    main()
