"""
main.py — Trading Bot Entry Point  [Week 4 / 5]

Commands:
  python main.py signals            → start live signal bot (alerts only, you trade)
  python main.py paper              → start paper trading bot (auto opens/closes)
  python main.py live               → start LIVE trading bot (real money on Binance)
  python main.py scan               → one-time scan right now
  python main.py status             → print risk/account status
  python main.py summary            → paper trade performance
  python main.py tg-ping            → test Telegram connection
  python main.py db-ping            → test Supabase connection
  python main.py balance            → fetch real Binance balance
  python main.py backtest           → backtest all watchlist pairs
  python main.py backtest BTC/USDT  → backtest specific pair
  python main.py dedup-clear        → clear all signal cooldowns
  python main.py dedup-clear BTC    → clear cooldown for one pair
  python main.py perf               → performance report from Supabase
  python main.py close BTC/USDT     → manually close a live/paper trade
"""

import sys
from colorama import Fore, Style, init
from config.settings import (
    WATCHLIST, TIMEFRAME, CANDLE_LIMIT,
    BACKTEST_INITIAL_CASH, TRADE_MODE,
)

import warnings
import pandas as pd
warnings.filterwarnings("ignore", category=pd.errors.ChainedAssignmentError)
pd.options.mode.chained_assignment = None

init(autoreset=True)

BANNER = f"""
{Fore.RED}  ████████╗██████╗  █████╗ ██████╗ ███████╗██████╗  ██████╗ ████████╗
{Fore.YELLOW}     ██╔══╝██╔══██╗██╔══██╗██╔══██╗██╔════╝██╔══██╗██╔═══██╗╚══██╔══╝
{Fore.GREEN}     ██║   ██████╔╝███████║██║  ██║█████╗  ██████╔╝██║   ██║   ██║   
{Fore.CYAN}     ██║   ██╔══██╗██╔══██║██║  ██║██╔══╝  ██╔══██╗██║   ██║   ██║   
{Fore.MAGENTA}     ██║   ██║  ██║██║  ██║██████╔╝███████╗██████╔╝╚██████╔╝   ██║   
{Fore.WHITE}     ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝ ╚══════╝╚═════╝  ╚═════╝    ╚═╝   
{Style.RESET_ALL}"""

HELP = f"""
{Fore.YELLOW}Commands:{Style.RESET_ALL}
  signals               Start live signal bot (alert only, you trade)
  paper                 Start paper trading bot (auto trade + alert)
  live                  Start LIVE trading bot (real orders on Binance)
  scan                  One-time scan of all pairs right now
  status                Show risk manager & open trade status
  summary               Show paper trading performance
  tg-ping               Test Telegram connection
  db-ping               Test Supabase connection
  balance               Fetch real Binance USDT balance
  backtest              Backtest all watchlist pairs
  backtest BTC/USDT     Backtest a specific pair
  dedup-clear           Clear all signal cooldowns
  dedup-clear BTC       Clear cooldown for one pair
  perf                  Performance report from Supabase (last 30 days)
  close BTC/USDT        Manually close an open trade at market price
"""


# ── Commands ───────────────────────────────────────────────────────────────────

def cmd_scan():
    from data.fetcher import fetch_all
    from strategies.consensus import ConsensusEngine
    from signals.deduplicator import SignalDeduplicator
    from signals.enricher import SignalEnricher

    print(f"{Fore.CYAN}Fetching data for: {', '.join(WATCHLIST)}\n")
    data     = fetch_all(WATCHLIST, timeframe=TIMEFRAME, limit=CANDLE_LIMIT)
    engine   = ConsensusEngine()
    dedup    = SignalDeduplicator()
    enricher = SignalEnricher()
    signals  = engine.scan_all(data)

    print(f"\n{Fore.CYAN}{'='*56}")
    print(f"  SCAN COMPLETE — {len(signals)} signal(s)")
    print(f"{'='*56}")

    for sig in signals:
        is_dup  = dedup.is_duplicate(sig)
        dup_tag = f"{Fore.YELLOW} [DUPLICATE — would be skipped]" if is_dup else ""
        print(f"\n{'🟢' if sig.signal.value == 'BUY' else '🔴'} "
              f"{sig.symbol} — {sig.signal.value}{dup_tag}")
        print(sig.summary())
        if sig.symbol in data:
            ctx = enricher.enrich(sig, data[sig.symbol], fetch_htf=False)
            print(enricher.format_context_block(ctx))

    if not signals:
        print(f"{Fore.YELLOW}  No signals right now.")


def cmd_start_bot(mode_override: str = None):
    """Start the full scheduler."""
    import os
    if mode_override:
        os.environ["SIGNAL_MODE"] = mode_override

    if mode_override == "live":
        print(f"\n{Fore.RED}⚠️  WARNING: LIVE MODE — REAL MONEY WILL BE USED")
        print(f"{Fore.RED}    Make sure you have tested in paper mode first!")
        confirm = input(f"{Fore.YELLOW}Type 'YES' to confirm: {Style.RESET_ALL}").strip()
        if confirm != "YES":
            print("Aborted.")
            return

    import scheduler
    scheduler.main(mode=mode_override)


def cmd_status():
    from risk.manager import RiskManager
    rm = RiskManager(account_balance=BACKTEST_INITIAL_CASH)
    print(rm.status_report())


def cmd_summary():
    from risk.manager import RiskManager
    from paper_trader.engine import PaperTrader
    rm = RiskManager(account_balance=BACKTEST_INITIAL_CASH)
    pt = PaperTrader(rm)
    print(pt.performance_summary())


def cmd_tg_ping():
    import tg_bot.bot as tg
    print("Sending ping to Telegram...")
    ok = tg.ping()
    print("✅ Success!" if ok else "❌ Failed — check .env TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID")


def cmd_db_ping():
    """Test that Supabase is reachable and tables exist."""
    import database.supabase_db as db
    print("Testing Supabase connection...")
    result = db.get_recent_trades(limit=1)
    if result is not None:
        print("✅ Supabase connected! Tables reachable.")
    else:
        print("❌ Supabase failed — check .env SUPABASE_URL + SUPABASE_KEY")
        print("   Also make sure you ran database/setup_tables.sql")


def cmd_balance():
    """Pull real USDT balance from Binance."""
    from execution.trader import get_live_exchange
    print("Fetching Binance balance...")
    try:
        exchange = get_live_exchange()
        balance  = exchange.fetch_balance()
        usdt     = balance["USDT"]
        print(f"\n💰 USDT Balance:")
        print(f"   Free:   ${usdt['free']:,.2f}")
        print(f"   Used:   ${usdt['used']:,.2f}")
        print(f"   Total:  ${usdt['total']:,.2f}")

        # Show non-zero crypto holdings
        print(f"\n📊 Other holdings:")
        for asset, val in balance["total"].items():
            if val > 0 and asset != "USDT":
                print(f"   {asset}: {val}")
    except Exception as e:
        print(f"❌ Failed: {e}")
        print("   Check BINANCE_API_KEY and BINANCE_SECRET in .env")


def cmd_backtest(symbols=None):
    from backtest.runner import Backtester
    from config.settings import BACKTEST_SINCE
    bt      = Backtester(initial_cash=BACKTEST_INITIAL_CASH)
    targets = symbols or WATCHLIST
    for sym in targets:
        bt.run(symbol=sym, timeframe=TIMEFRAME, since=BACKTEST_SINCE)
        bt.cash   = BACKTEST_INITIAL_CASH
        bt.trades = []


def cmd_dedup_clear(symbol=None):
    from signals.deduplicator import SignalDeduplicator
    dd = SignalDeduplicator()
    dd.clear(symbol)
    print(f"{'All cooldowns' if not symbol else symbol} cleared.")


def cmd_perf():
    """Pull last 30 days of performance from Supabase and display it."""
    import database.supabase_db as db

    print(f"{Fore.CYAN}\n📊 Performance Report (last 30 days)")
    print("="*50)

    stats = db.get_daily_stats(days=30)
    if not stats:
        print(f"{Fore.YELLOW}No daily stats yet. Run the bot for a few days first.")
        return

    total_pnl  = sum(s.get("daily_pnl", 0) or 0 for s in stats)
    total_tr   = sum(s.get("trades", 0) or 0 for s in stats)
    total_wins = sum(s.get("wins", 0) or 0 for s in stats)
    wr = (total_wins / total_tr * 100) if total_tr > 0 else 0
    latest_bal = stats[0].get("balance", 0) if stats else 0

    print(f"Balance:      ${latest_bal:,.2f} USDT")
    print(f"Total P&L:    ${total_pnl:+,.2f} USDT")
    print(f"Total Trades: {total_tr} | Wins: {total_wins} | WR: {wr:.1f}%")
    print()

    print(f"{'Date':<12} {'P&L':>10} {'Trades':>8} {'WR':>8} {'Balance':>12}")
    print("-"*52)
    for s in stats[:14]:   # Show last 14 days
        pnl   = s.get("daily_pnl", 0) or 0
        tr    = s.get("trades", 0) or 0
        wr_d  = s.get("win_rate", 0) or 0
        bal   = s.get("balance", 0) or 0
        color = Fore.GREEN if pnl >= 0 else Fore.RED
        print(f"{s['date']:<12} {color}${pnl:>+9.2f}{Style.RESET_ALL} {tr:>8} {wr_d:>7.1f}% ${bal:>10,.2f}")

    # Also show recent closed trades
    print(f"\n{Fore.CYAN}Recent Closed Trades:")
    print("-"*52)
    trades = db.get_recent_trades(limit=10)
    if not trades:
        print(f"{Fore.YELLOW}No closed trades yet.")
    for t in trades:
        pnl   = t.get("pnl_usd", 0) or 0
        color = Fore.GREEN if pnl >= 0 else Fore.RED
        print(f"  {color}{t.get('direction',''):<4} {t.get('symbol',''):<10} "
              f"${pnl:>+8.2f}  {t.get('exit_reason',''):<5}  {t.get('strategy','')[:30]}{Style.RESET_ALL}")


def cmd_close(symbol: str):
    """Manually close an open paper or live trade at current market price."""
    from risk.manager import RiskManager
    rm    = RiskManager(account_balance=BACKTEST_INITIAL_CASH)
    trade = rm.get_trade(symbol)
    if not trade:
        print(f"{Fore.YELLOW}No open trade for {symbol}")
        return

    from data.fetcher import get_current_price, get_exchange
    exchange = get_exchange()
    price    = get_current_price(symbol, exchange)
    print(f"Closing {symbol} at ${price:.4f}...")

    if TRADE_MODE == "live":
        from execution.trader import LiveTrader
        trader = LiveTrader(rm)
        trader._execute_close(trade, price, "MANUAL")
    else:
        from paper_trader.engine import PaperTrader
        pt = PaperTrader(rm)
        pt._execute_close(trade, price, "MANUAL")

    print(f"✅ {symbol} closed.")


# ── Entry ──────────────────────────────────────────────────────────────────────

def main():
    print(BANNER)
    if len(sys.argv) < 2:
        print(HELP)
        return

    cmd = sys.argv[1].lower()

    if   cmd == "signals":     cmd_start_bot("signals")
    elif cmd == "paper":       cmd_start_bot("paper")
    elif cmd == "live":        cmd_start_bot("live")
    elif cmd == "scan":        cmd_scan()
    elif cmd == "status":      cmd_status()
    elif cmd == "summary":     cmd_summary()
    elif cmd == "tg-ping":     cmd_tg_ping()
    elif cmd == "db-ping":     cmd_db_ping()
    elif cmd == "balance":     cmd_balance()
    elif cmd == "perf":        cmd_perf()
    elif cmd == "backtest":
        syms = None
        if len(sys.argv) > 2:
            s = sys.argv[2].upper()
            syms = [s if "/" in s else s + "/USDT"]
        cmd_backtest(syms)
    elif cmd == "dedup-clear":
        sym = sys.argv[2].upper() if len(sys.argv) > 2 else None
        if sym and "/" not in sym:
            sym += "/USDT"
        cmd_dedup_clear(sym)
    elif cmd == "close":
        if len(sys.argv) < 3:
            print(f"{Fore.RED}Usage: python main.py close BTC/USDT")
        else:
            sym = sys.argv[2].upper()
            if "/" not in sym:
                sym += "/USDT"
            cmd_close(sym)
    else:
        print(f"{Fore.RED}Unknown command: {cmd}")
        print(HELP)


if __name__ == "__main__":
    main()
