"""
telegram/bot.py  [Fixed - HTML escape applied everywhere]
"""
import requests
from datetime import datetime
from colorama import Fore, init
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from strategies.consensus import ConsensusSignal
from strategies.base import SignalType

init(autoreset=True)
_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def _escape(text: str) -> str:
    """Escape <, >, & so Telegram HTML parser never chokes on indicator names."""
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def send_message(text: str, chat_id: str = None, parse_mode: str = "HTML") -> bool:
    cid = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not cid:
        print(f"{Fore.YELLOW}[TG] Not configured.\n{text}")
        return False
    try:
        r = requests.post(f"{_BASE}/sendMessage",
            json={"chat_id": cid, "text": text, "parse_mode": parse_mode,
                  "disable_web_page_preview": True}, timeout=10)
        data = r.json()
        if data.get("ok"):
            print(f"{Fore.GREEN}[TG] Sent ✓")
            return True
        print(f"{Fore.RED}[TG] Failed: {data.get('description')}")
        return False
    except requests.RequestException as e:
        print(f"{Fore.RED}[TG] Network error: {e}")
        return False


def send_error(text: str) -> bool:
    return send_message(f"❌ <b>BOT ERROR</b>\n\n{_escape(text)}")


def send_signal(signal: ConsensusSignal) -> bool:
    is_buy    = signal.signal == SignalType.BUY
    arrow     = "🟢 BUY" if is_buy else "🔴 SELL"
    header    = "🚀" if is_buy else "📉"
    conf_icon = {"HIGH": "🔥", "MEDIUM": "⚡", "LOW": "🔅"}.get(signal.confidence, "⚡")
    ts        = signal.timestamp.strftime("%Y-%m-%d %H:%M UTC") if signal.timestamp else "—"
    rr        = signal.risk_reward()

    # _escape() on every dynamic string — this is the key fix
    strategies = "\n".join(
        f"  ✅ <b>{_escape(s.strategy)}</b>\n     <i>{_escape(s.reason)}</i>"
        for s in signal.contributors
    )
    msg = (
        f"{header} <b>SIGNAL — {_escape(signal.symbol)}</b>\n\n"
        f"Direction:  <b>{arrow}</b>\n"
        f"Confidence: {conf_icon} <b>{signal.confidence}</b>  "
        f"({signal.buy_votes}B / {signal.sell_votes}S votes)\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 Entry:     <code>{signal.entry}</code>\n"
        f"🛑 Stop Loss: <code>{signal.stop_loss}</code>\n"
        f"🎯 TP1:       <code>{signal.take_profit1}</code>\n"
        f"🎯 TP2:       <code>{signal.take_profit2}</code>\n"
        f"📐 R:R:       <b>{rr}:1</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 <b>Agreeing strategies:</b>\n{strategies}\n\n"
        f"🕐 <i>{ts}</i>\n"
        f"⚠️ <i>Use stop loss. Only risk what you can afford to lose.</i>"
    )
    return send_message(msg)


def send_trade_opened(symbol: str, direction: str, entry: float,
    stop_loss: float, take_profit1: float, take_profit2: float,
    position_size: float, mode: str = "paper") -> bool:
    icon     = "✅" if direction == "BUY" else "🔻"
    mode_tag = "📄 PAPER" if mode.lower() == "paper" else "💰 LIVE"
    now      = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    msg = (
        f"{icon} <b>TRADE OPENED [{mode_tag}]</b>\n\n"
        f"Pair:   <b>{_escape(symbol)}</b>\n"
        f"Side:   <b>{_escape(direction)}</b>\n"
        f"Entry:  <code>{entry}</code>\n"
        f"SL:     <code>{stop_loss}</code>\n"
        f"TP1:    <code>{take_profit1}</code>\n"
        f"TP2:    <code>{take_profit2}</code>\n"
        f"Size:   <b>${position_size:.2f} USDT</b>\n"
        f"Time:   <i>{now}</i>"
    )
    return send_message(msg)


def send_trade_closed(symbol: str, direction: str, entry: float,
    exit_price: float, pnl_usd: float, pnl_pct: float,
    reason: str, balance: float, mode: str = "paper") -> bool:
    won      = pnl_usd > 0
    result   = "💚 WIN" if won else "❌ LOSS"
    mode_tag = "📄 PAPER" if mode.lower() == "paper" else "💰 LIVE"
    reasons  = {"TP1": "🎯 Take Profit 1", "TP2": "🎯🎯 Take Profit 2",
                "SL": "🛑 Stop Loss", "END": "⏰ Session end", "MANUAL": "👤 Manual"}
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    msg = (
        f"{result} <b>TRADE CLOSED [{mode_tag}]</b>\n\n"
        f"Pair:    <b>{_escape(symbol)}</b>\n"
        f"Side:    <b>{_escape(direction)}</b>\n"
        f"Entry:   <code>{entry}</code>\n"
        f"Exit:    <code>{exit_price}</code>\n"
        f"Reason:  {reasons.get(reason, _escape(reason))}\n\n"
        f"P&L:     <b>{'+' if pnl_usd > 0 else ''}{pnl_usd:.2f} USDT "
        f"({pnl_pct:+.2f}%)</b>\n"
        f"Balance: <b>${balance:,.2f} USDT</b>\n"
        f"Time:    <i>{now}</i>"
    )
    return send_message(msg)


def send_daily_summary(date_str: str, total_trades: int, wins: int,
    losses: int, daily_pnl: float, balance: float) -> bool:
    wr     = (wins / total_trades * 100) if total_trades else 0
    icon   = "📈" if daily_pnl >= 0 else "📉"
    status = "Profitable day 🔥" if daily_pnl >= 0 else "Rough day — stay disciplined 💪"
    msg = (
        f"{icon} <b>DAILY SUMMARY — {_escape(date_str)}</b>\n\n"
        f"{status}\n\n"
        f"Trades:    <b>{total_trades}</b>\n"
        f"Wins:      <b>{wins}</b>\n"
        f"Losses:    <b>{losses}</b>\n"
        f"Win Rate:  <b>{wr:.1f}%</b>\n\n"
        f"Daily P&L: <b>{'+' if daily_pnl >= 0 else ''}{daily_pnl:.2f} USDT</b>\n"
        f"Balance:   <b>${balance:,.2f} USDT</b>\n\n"
        f"<i>See you tomorrow 🤖</i>"
    )
    return send_message(msg)


def send_status(text: str) -> bool:
    return send_message(f"🤖 <b>BOT STATUS</b>\n\n{_escape(text)}")


def send_halt_alert(daily_pnl: float, limit_pct: float, balance: float) -> bool:
    msg = (
        f"🚨 <b>BOT HALTED</b>\n\n"
        f"Daily loss limit reached.\n\n"
        f"Daily P&L:  <b>{daily_pnl:.2f} USDT</b>\n"
        f"Limit:      <b>{limit_pct}%</b>\n"
        f"Balance:    <b>${balance:,.2f} USDT</b>\n\n"
        f"No new trades today. Resumes automatically tomorrow. ✅"
    )
    return send_message(msg)


def send_startup(mode: str, pairs: list, timeframe: str) -> bool:
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    msg = (
        f"🟢 <b>BOT STARTED</b>\n\n"
        f"Mode:      <b>{_escape(mode.upper())}</b>\n"
        f"Pairs:     <b>{_escape(' | '.join(pairs))}</b>\n"
        f"Timeframe: <b>{_escape(timeframe)}</b>\n"
        f"Time:      <i>{now}</i>\n\n"
        f"Multi-strategy consensus engine active.\n"
        f"Watching for signals... 👁"
    )
    return send_message(msg)


def ping() -> bool:
    return send_message("🏓 <b>Telegram ping — connection working!</b>")


if __name__ == "__main__":
    ping()