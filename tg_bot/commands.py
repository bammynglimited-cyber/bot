"""
telegram/commands.py
────────────────────
Interactive Telegram Command Handler

Lets you and your brother control and query the bot live from Telegram.
Uses python-telegram-bot in polling mode — runs in a background thread
alongside the APScheduler.

Commands available:
  /start        → welcome message + command list
  /status       → current balance, open trades, daily P&L
  /trades       → list all open positions
  /signals      → signals fired in last 48h
  /performance  → paper trading win rate + total P&L
  /pause        → pause the bot (no new signals/trades)
  /resume       → resume the bot
  /clear <SYM>  → clear signal cooldown for a pair (re-enables alerts)
  /help         → full command list

Security: Only ALLOWED_USER_IDS from settings can use commands.
Any other user gets silently ignored.
"""

import threading
import time
from datetime import datetime
from colorama import Fore, init

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
    MessageHandler, filters,
)

from config.settings import TELEGRAM_BOT_TOKEN, ALLOWED_USER_IDS
from risk.manager import RiskManager
from paper_trader.engine import PaperTrader
from signals.deduplicator import SignalDeduplicator

init(autoreset=True)

# ── Shared pause flag ──────────────────────────────────────────────────────────
# Other modules check this before scanning/trading
_paused = False


def is_paused() -> bool:
    return _paused


def _set_paused(state: bool):
    global _paused
    _paused = state


# ── Auth decorator ─────────────────────────────────────────────────────────────

def _authorized(user_id: int) -> bool:
    """Check if a Telegram user is in the whitelist."""
    if not ALLOWED_USER_IDS or ALLOWED_USER_IDS == [0]:
        return True     # no whitelist configured → allow everyone (dev mode)
    return user_id in ALLOWED_USER_IDS


# ── Command handlers ───────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update.effective_user.id):
        return
    await update.message.reply_text(
        "🤖 <b>Trading Bot Active</b>\n\n"
        "Commands:\n"
        "/status      — balance, P&L, open trades\n"
        "/trades      — open positions\n"
        "/signals     — recent signals (48h)\n"
        "/performance — paper trading stats\n"
        "/pause       — pause the bot\n"
        "/resume      — resume the bot\n"
        "/clear BTC   — reset cooldown for a pair\n"
        "/help        — this message",
        parse_mode="HTML",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    risk: RiskManager = None,
):
    if not _authorized(update.effective_user.id):
        return

    paused_tag = "\n\n⏸ <b>BOT IS PAUSED</b>" if _paused else ""

    if risk is None:
        await update.message.reply_text("⚠️ Risk manager not available.", parse_mode="HTML")
        return

    text = f"🤖 <b>Bot Status</b>\n\n{risk.status_report()}{paused_tag}"
    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_trades(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    risk: RiskManager = None,
):
    if not _authorized(update.effective_user.id):
        return

    if risk is None or not risk.open_trades:
        await update.message.reply_text("📭 No open trades right now.", parse_mode="HTML")
        return

    lines = ["📈 <b>Open Positions</b>\n"]
    for i, t in enumerate(risk.open_trades, 1):
        is_buy  = t.direction == "BUY"
        icon    = "🟢" if is_buy else "🔴"
        opened  = t.opened_at[:16].replace("T", " ")
        lines.append(
            f"{i}. {icon} <b>{t.symbol}</b> {t.direction}\n"
            f"   Entry: <code>{t.entry}</code>\n"
            f"   SL: <code>{t.stop_loss}</code>  "
            f"TP1: <code>{t.take_profit1}</code>\n"
            f"   Size: ${t.position_size:.2f}  |  Opened: {opened} UTC"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_signals(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    dedup: SignalDeduplicator = None,
):
    if not _authorized(update.effective_user.id):
        return

    if dedup is None:
        await update.message.reply_text("⚠️ Deduplicator not available.", parse_mode="HTML")
        return

    recent = dedup.recent_signals(hours=48)
    if not recent:
        await update.message.reply_text("📭 No signals fired in the last 48h.", parse_mode="HTML")
        return

    lines = ["📡 <b>Recent Signals (48h)</b>\n"]
    for s in recent:
        icon = "🟢" if s["direction"] == "BUY" else "🔴"
        lines.append(
            f"{icon} <b>{s['symbol']}</b> {s['direction']} "
            f"— {s['fired_at']} ({s['hours_ago']}h ago)"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_performance(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    paper: PaperTrader = None,
):
    if not _authorized(update.effective_user.id):
        return

    if paper is None:
        await update.message.reply_text("⚠️ Paper trader not available.", parse_mode="HTML")
        return

    summary = paper.performance_summary()
    await update.message.reply_text(
        f"📊 <b>Paper Trading Performance</b>\n\n{summary}",
        parse_mode="HTML",
    )


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update.effective_user.id):
        return
    _set_paused(True)
    print(f"{Fore.YELLOW}[CMD] Bot PAUSED by {update.effective_user.id}")
    await update.message.reply_text(
        "⏸ <b>Bot paused.</b>\n\n"
        "No new signals or trades will be opened.\n"
        "Use /resume to restart.",
        parse_mode="HTML",
    )


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update.effective_user.id):
        return
    _set_paused(False)
    print(f"{Fore.GREEN}[CMD] Bot RESUMED by {update.effective_user.id}")
    await update.message.reply_text(
        "▶️ <b>Bot resumed.</b> Scanning on next cycle.",
        parse_mode="HTML",
    )


async def cmd_clear(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    dedup: SignalDeduplicator = None,
):
    if not _authorized(update.effective_user.id):
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: /clear BTC  or  /clear BTC/USDT\n"
            "Clears signal cooldown so that pair can fire again.",
            parse_mode="HTML",
        )
        return

    raw = args[0].upper()
    symbol = raw if "/" in raw else f"{raw}/USDT"

    if dedup:
        dedup.clear(symbol)

    await update.message.reply_text(
        f"🔄 Cooldown cleared for <b>{symbol}</b>.\n"
        f"Next scan can fire a new signal for this pair.",
        parse_mode="HTML",
    )


async def _unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update.effective_user.id):
        return
    await update.message.reply_text("❓ Unknown command. Use /help for the list.")


# ── Bot runner ─────────────────────────────────────────────────────────────────

class CommandBot:
    """
    Wraps the Telegram Application and runs it in a background thread
    so it doesn't block the APScheduler.
    """

    def __init__(
        self,
        risk:  RiskManager,
        paper: PaperTrader,
        dedup: SignalDeduplicator,
    ):
        self.risk  = risk
        self.paper = paper
        self.dedup = dedup
        self._app  = None
        self._thread = None

    def build(self) -> "CommandBot":
        """Build the Application and register all handlers."""
        if not TELEGRAM_BOT_TOKEN:
            print(f"{Fore.YELLOW}[CMD BOT] No token configured — command bot disabled.")
            return self

        app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

        # Inject dependencies via closures
        risk  = self.risk
        paper = self.paper
        dedup = self.dedup

        async def _status(u, c):  await cmd_status(u, c, risk)
        async def _trades(u, c):  await cmd_trades(u, c, risk)
        async def _signals(u, c): await cmd_signals(u, c, dedup)
        async def _perf(u, c):    await cmd_performance(u, c, paper)
        async def _clear(u, c):   await cmd_clear(u, c, dedup)

        app.add_handler(CommandHandler("start",       cmd_start))
        app.add_handler(CommandHandler("help",        cmd_help))
        app.add_handler(CommandHandler("status",      _status))
        app.add_handler(CommandHandler("trades",      _trades))
        app.add_handler(CommandHandler("signals",     _signals))
        app.add_handler(CommandHandler("performance", _perf))
        app.add_handler(CommandHandler("pause",       cmd_pause))
        app.add_handler(CommandHandler("resume",      cmd_resume))
        app.add_handler(CommandHandler("clear",       _clear))
        app.add_handler(MessageHandler(filters.COMMAND, _unknown))

        self._app = app
        return self

    def start_polling(self):
        """Start polling in a daemon background thread."""
        if self._app is None:
            return

        def _run():
            print(f"{Fore.GREEN}[CMD BOT] Command handler started — polling Telegram...")
            self._app.run_polling(drop_pending_updates=True)

        self._thread = threading.Thread(target=_run, daemon=True, name="TelegramCmdBot")
        self._thread.start()

    def stop(self):
        if self._app:
            try:
                self._app.stop()
            except Exception:
                pass
