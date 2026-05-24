"""
scheduler.py  [Fixed - clean version]
"""

import sys
import time
import signal as _signal
from datetime import datetime, timezone
from colorama import Fore, Style, init

from config.settings import (
    SIGNAL_MODE, TIMEFRAME, CANDLE_LIMIT,
    BACKTEST_INITIAL_CASH, TRADE_MODE,
)
from database.bot_heartbeat import write_status, write_offline

init(autoreset=True)

import schedule

# ── Globals ────────────────────────────────────────────────────────────────────
_risk_manager = None
_trader       = None
_signal_mode  = None


# ── Init ───────────────────────────────────────────────────────────────────────

def _init(mode: str):
    global _risk_manager, _trader, _signal_mode
    _signal_mode = mode

    from risk.manager import RiskManager
    _risk_manager = RiskManager(account_balance=BACKTEST_INITIAL_CASH)

    if mode == "live":
        from execution.live_trader import LiveTrader
        _trader = LiveTrader(_risk_manager)
        _trader.sync_balance()
        print(f"{Fore.RED}⚠️  LIVE MODE — Real money at risk!")
    elif mode == "paper":
        from paper_trader.engine import PaperTrader
        _trader = PaperTrader(_risk_manager)
        print(f"{Fore.CYAN}📄 Paper mode active — simulated trades only.")
    else:
        print(f"{Fore.YELLOW}📡 Signal mode — alerts only, no auto-trading.")


# ── Process dashboard commands ─────────────────────────────────────────────────

def job_process_commands():
    """Check bot_commands table for instructions from the web dashboard."""
    import database.supabase_db as db

    commands = db.get_pending_commands()
    for cmd in commands:
        command = cmd.get("command")
        payload = cmd.get("payload", {})
        cmd_id  = cmd.get("id")

        try:
            if command == "pause":
                print(f"{Fore.YELLOW}[CMD] Bot paused by dashboard")
                if _risk_manager:
                    _risk_manager.daily.halted = True
                    _risk_manager._save_state()

            elif command == "resume":
                print(f"{Fore.GREEN}[CMD] Bot resumed by dashboard")
                if _risk_manager:
                    _risk_manager.daily.halted = False
                    _risk_manager._save_state()

            elif command == "close_all":
                print(f"{Fore.YELLOW}[CMD] Closing all trades by request...")
                if _trader and _risk_manager:
                    from data.fetcher import get_current_price, get_exchange
                    exchange = get_exchange()
                    for trade in list(_risk_manager.open_trades):
                        price = get_current_price(trade.symbol, exchange)
                        _trader._execute_close(trade, price, "MANUAL")

            elif command == "update_risk":
                from config import settings
                if payload.get("risk_per_trade"):
                    settings.RISK_PER_TRADE = float(payload["risk_per_trade"])
                if payload.get("max_open_trades"):
                    settings.MAX_OPEN_TRADES = int(payload["max_open_trades"])
                if payload.get("daily_loss_limit"):
                    settings.DAILY_LOSS_LIMIT = float(payload["daily_loss_limit"])
                if payload.get("min_votes"):
                    settings.MIN_STRATEGY_VOTES = int(payload["min_votes"])
                print(f"{Fore.CYAN}[CMD] Risk settings updated: {payload}")

            elif command == "reload_keys":
                print(f"{Fore.CYAN}[CMD] API key reload requested")
                # Live trader will pick up new keys on next trade

            db.mark_command_done(cmd_id, "done")

        except Exception as e:
            print(f"{Fore.RED}[CMD] Command '{command}' failed: {e}")
            db.mark_command_done(cmd_id, "failed")


# ── Main scan job ──────────────────────────────────────────────────────────────

def job_scan_and_signal():
    """Runs every 4h — fetch candles, run strategies, fire signals."""
    from data.fetcher import fetch_all
    from strategies.consensus import ConsensusEngine
    from signals.deduplicator import SignalDeduplicator
    from signals.enricher import SignalEnricher
    import tg_bot.bot as tg
    import database.supabase_db as db

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{Fore.CYAN}{'='*56}")
    print(f"  SCAN — {now}  [mode={_signal_mode}]")
    print(f"{'='*56}")

    # Process any pending dashboard commands first
    job_process_commands()

    # Halt check
    if _risk_manager and _risk_manager.is_halted:
        print(f"{Fore.RED}[SCHED] Halted (daily loss limit) — skipping scan.")
        _write_heartbeat()
        return

    # Get watchlist — try Supabase first, fallback to settings
    try:
        watchlist = db.get_global_watchlist()
        if not watchlist:
            from config.settings import WATCHLIST
            watchlist = WATCHLIST
    except Exception:
        from config.settings import WATCHLIST
        watchlist = WATCHLIST

    print(f"  Watchlist ({len(watchlist)}): {', '.join(watchlist)}")

    try:
        data    = fetch_all(watchlist, timeframe=TIMEFRAME, limit=CANDLE_LIMIT)
        engine  = ConsensusEngine()
        dedup   = SignalDeduplicator()
        enricher = SignalEnricher()
        signals = engine.scan_all(data)
    except Exception as e:
        print(f"{Fore.RED}[SCAN] Error: {e}")
        _write_heartbeat()
        return

    new_signals = [s for s in signals if not dedup.is_duplicate(s)]
    print(f"  {len(signals)} signal(s) found | {len(new_signals)} new")

    for sig in new_signals:
        # Mark as fired before sending so a crash mid-loop doesn't re-fire
        dedup.record(sig)

        # Enrich with market context
        ctx = None
        if sig.symbol in data:
            try:
                ctx = enricher.enrich(sig, data[sig.symbol], fetch_htf=False)
            except Exception:
                pass

        # Log to Supabase
        try:
            db.log_signal(sig)
        except Exception:
            pass

        if _signal_mode == "signals":
            # Alerts only — send full enriched signal to Telegram
            tg.send_signal(sig)
            if ctx:
                print(enricher.format_context_block(ctx))

        elif _signal_mode in ("paper", "live") and _trader:
            # Auto trade
            if not (_risk_manager and _risk_manager.is_halted):
                _trader.open_trade(sig)
            else:
                print(f"{Fore.RED}[SCHED] Halted — skipping {sig.symbol}")

    if not new_signals:
        print(f"{Fore.YELLOW}  No new signals this cycle.")

    _write_heartbeat()


# ── Monitor open trades ────────────────────────────────────────────────────────

def job_monitor_trades():
    """Runs every 1h — check open positions against live prices."""
    if _trader is None or _signal_mode not in ("paper", "live"):
        return
    try:
        _trader.monitor_open_trades()
    except Exception as e:
        print(f"{Fore.RED}[MONITOR] Error: {e}")
    _write_heartbeat()


# ── Daily snapshot ─────────────────────────────────────────────────────────────

def job_daily_snapshot():
    """23:55 UTC — log daily stats to Supabase and reset daily counters."""
    import database.supabase_db as db
    if _risk_manager is None:
        return
    d = _risk_manager.daily
    try:
        db.log_daily_stats(
            date        = d.date,
            balance     = _risk_manager.balance,
            daily_pnl   = d.realized_pnl,
            trades      = d.trades_taken,
            wins        = d.wins,
            losses      = d.losses,
            open_trades = len(_risk_manager.open_trades),
            mode        = _signal_mode,
        )
    except Exception as e:
        print(f"{Fore.YELLOW}[DAILY] Snapshot error: {e}")
    _risk_manager._refresh_daily()
    print(f"{Fore.CYAN}[DAILY] Snapshot logged.")


# ── Heartbeat helper ───────────────────────────────────────────────────────────

def _write_heartbeat():
    """Write current bot status to Supabase so the dashboard TopBar updates."""
    if _risk_manager is None:
        return
    try:
        write_status(
            mode        = _signal_mode or "unknown",
            balance     = _risk_manager.balance,
            open_trades = len(_risk_manager.open_trades),
            daily_pnl   = _risk_manager.daily.realized_pnl,
            is_halted   = _risk_manager.is_halted,
        )
    except Exception as e:
        print(f"{Fore.YELLOW}[HEARTBEAT] {e}")


# ── Graceful shutdown ──────────────────────────────────────────────────────────

def _shutdown(sig, frame):
    print(f"\n{Fore.YELLOW}[SCHED] Shutdown signal received. Stopping...")
    import tg_bot.bot as tg
    try:
        job_daily_snapshot()
        write_offline()
        tg.send_message("🔴 <b>Bot stopped.</b> Open trades NOT auto-closed.")
    except Exception:
        pass
    sys.exit(0)

_signal.signal(_signal.SIGINT,  _shutdown)
_signal.signal(_signal.SIGTERM, _shutdown)


# ── Main ───────────────────────────────────────────────────────────────────────

def main(mode: str = None):
    import os
    effective_mode = mode or os.getenv("SIGNAL_MODE", TRADE_MODE or "signals")
    _init(effective_mode)

    print(f"\n{Fore.CYAN}Scheduler starting — mode: {effective_mode.upper()}")
    print(f"Watchlist: loaded from Supabase (fallback to settings.py)")
    print(f"Timeframe: {TIMEFRAME} | checking every 4h + monitoring every 1h\n")

    # Send startup status to dashboard immediately
    _write_heartbeat()

    # Send Telegram startup message
    try:
        import tg_bot.bot as tg
        from config.settings import WATCHLIST
        tg.send_startup(mode=effective_mode, pairs=WATCHLIST, timeframe=TIMEFRAME)
    except Exception:
        pass

    # Run first scan immediately on startup
    job_scan_and_signal()

    # Schedule recurring jobs
    schedule.every(4).hours.do(job_scan_and_signal)
    schedule.every(1).hours.do(job_monitor_trades)
    schedule.every(5).minutes.do(job_process_commands)
    schedule.every().day.at("23:55").do(job_daily_snapshot)

    print(f"{Fore.GREEN}✅ Scheduler running. Press Ctrl+C to stop.\n")

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()