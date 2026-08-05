from __future__ import annotations
"""飞书通知模块

功能:
  - 交易信号推送（买入/卖出）
  - 定时状态汇报（资产、盈亏、持仓）
  - 异常告警（连续亏损、大幅回撤等）
  - 每日收盘总结

使用方式:
  1. 在飞书群聊中添加「自定义机器人」
  2. 复制 Webhook URL 填入 config.py 的 FEISHU_WEBHOOK_URL
"""
import requests
from datetime import datetime


def send_feishu_message(webhook_url: str, title: str, content: str, template: str = "blue"):
    """发送飞书卡片消息

    Args:
        webhook_url: 飞书机器人 Webhook URL
        title: 消息标题
        content: 消息内容（支持 Markdown）
        template: 卡片颜色 (blue/green/red/orange/purple)
    """
    if not webhook_url:
        return

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": template,
            },
            "elements": [
                {"tag": "markdown", "content": content}
            ],
        },
    }

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == 0:
                print(f"  [飞书] 通知发送成功: {title}")
            else:
                print(f"  [飞书] 发送失败: {data.get('msg', 'unknown')}")
        else:
            print(f"  [飞书] HTTP错误: {resp.status_code}")
    except Exception as e:
        print(f"  [飞书] 异常: {e}")


def notify_trade_signal(webhook_url: str, code: str, action: str, price: float, shares: int):
    """推送交易信号"""
    if action == "买入":
        template = "red"
        emoji = "🔴"
    else:
        template = "green"
        emoji = "🟢"

    content = (
        f"{emoji} **{action}信号**\n"
        f"- 股票: `{code}`\n"
        f"- 价格: ¥{price:.2f}\n"
        f"- 数量: {shares} 股\n"
        f"- 金额: ¥{price * shares:,.2f}\n"
        f"- 时间: {datetime.now().strftime('%H:%M:%S')}"
    )
    send_feishu_message(webhook_url, f"交易 | {code} {action}", content, template)


def notify_status_report(
    webhook_url: str,
    total_asset: float,
    available_cash: float,
    total_pnl: float,
    pnl_pct: float,
    positions: list[dict],
    trade_count: int = 0,
    signal_count: int = 0,
):
    """定时状态汇报（每30分钟）"""
    pnl_emoji = "📈" if total_pnl >= 0 else "📉"
    pos_text = ""
    if positions:
        for p in positions[:5]:
            code = p.get("code", "")
            qty = p.get("quantity", 0)
            mv = p.get("market_value", 0)
            pos_text += f"  `{code}` {qty}股 ¥{mv:,.0f}\n"
        if len(positions) > 5:
            pos_text += f"  ...等共{len(positions)}只\n"
    else:
        pos_text = "  空仓\n"

    content = (
        f"{pnl_emoji} **资产状况**\n"
        f"- 总资产: ¥{total_asset:,.2f}\n"
        f"- 盈亏: ¥{total_pnl:+,.2f} ({pnl_pct:+.2f}%)\n"
        f"- 可用资金: ¥{available_cash:,.2f}\n\n"
        f"**持仓**\n{pos_text}\n"
        f"**本轮统计**\n"
        f"- 信号: {signal_count} 个\n"
        f"- 成交: {trade_count} 笔\n"
        f"- 时间: {datetime.now().strftime('%H:%M:%S')}"
    )

    template = "green" if total_pnl >= 0 else "orange"
    send_feishu_message(webhook_url, "状态汇报", content, template)


def notify_risk_alert(webhook_url: str, alert_type: str, detail: str):
    """风险告警

    alert_type: "止损触发" / "大幅回撤" / "连续亏损" / "异常"
    """
    content = (
        f"⚠️ **{alert_type}**\n\n"
        f"{detail}\n\n"
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    send_feishu_message(webhook_url, f"⚠️ 风险告警 | {alert_type}", content, "red")


def notify_daily_summary(
    webhook_url: str,
    strategy: str,
    initial_cash: float,
    final_asset: float,
    realized_pnl: float,
    trade_count: int,
    buy_count: int,
    sell_count: int,
):
    """每日收盘总结"""
    total_pnl = final_asset - initial_cash
    pnl_pct = (total_pnl / initial_cash) * 100 if initial_cash > 0 else 0
    emoji = "🎉" if total_pnl >= 0 else "😢"

    content = (
        f"{emoji} **今日收盘**\n\n"
        f"- 策略: {strategy}\n"
        f"- 初始资金: ¥{initial_cash:,.2f}\n"
        f"- 最终资产: ¥{final_asset:,.2f}\n"
        f"- **盈亏: ¥{total_pnl:+,.2f} ({pnl_pct:+.2f}%)**\n"
        f"- 已实现盈亏: ¥{realized_pnl:+,.2f}\n\n"
        f"**交易统计**\n"
        f"- 总成交: {trade_count} 笔\n"
        f"- 买入: {buy_count} 笔\n"
        f"- 卖出: {sell_count} 笔"
    )

    template = "green" if total_pnl >= 0 else "red"
    send_feishu_message(webhook_url, "📊 每日总结", content, template)
