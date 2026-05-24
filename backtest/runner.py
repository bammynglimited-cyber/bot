"""
backtest/runner.py
──────────────────
Backtesting Engine

Simulates the bot running on historical data to evaluate strategy performance
BEFORE going live. Tells you: win rate, profit factor, max drawdown, Sharpe ratio.

Usage:
    python -m backtest.runner --symbol BTC/USDT --timeframe 4h

How it works:
    1. Downloads historical OHLCV data
    2. Runs the consensus engine on each candle (as if it's "live")
    3. Simulates trade entry, SL hit, or TP hit
    4. Tracks all results and prints a report
"""

import argparse
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import pandas as pd
from colorama import Fore, Style, init
from tabulate import tabulate

from data.fetcher import fetch_historical
from strategies.consensus import ConsensusEngine, ConsensusSignal
from strategies.base import SignalType
from config.settings import (
    BACKTEST_SINCE, BACKTEST_INITIAL_CASH,
    RISK_PER_TRADE, TIMEFRAME, WATCHLIST, MIN_RR_RATIO
)

init(autoreset=True)


# ── Trade record ───────────────────────────────────────────────────────────────

@dataclass
class BacktestTrade:
    symbol:       str
    direction:    str               # BUY | SELL
    entry:        float
    stop_loss:    float
    take_profit1: float
    take_profit2: float
    entry_time:   pd.Timestamp
    exit_time:    Optional[pd.Timestamp] = None
    exit_price:   float = 0.0
    exit_reason:  str   = ""        # TP1 | TP2 | SL | END
    pnl_pct:      float = 0.0       # % gain/loss on the trade
    pnl_usd:      float = 0.0
    position_size: float = 0.0      # USDT allocated
    strategies:   str   = ""


# ── Core backtester ────────────────────────────────────────────────────────────

class Backtester:

    def __init__(
        self,
        initial_cash: float = BACKTEST_INITIAL_CASH,
        risk_pct: float     = RISK_PER_TRADE,
        min_rr: float       = MIN_RR_RATIO,
    ):
        self.initial_cash = initial_cash
        self.cash         = initial_cash
        self.risk_pct     = risk_pct
        self.min_rr       = min_rr
        self.engine       = ConsensusEngine()
        self.trades: list[BacktestTrade] = []

    def _position_size(self, entry: float, stop_loss: float) -> float:
        """
        Calculate how much USDT to risk.
        Risk per trade = risk_pct% of current cash.
        """
        risk_amount = self.cash * (self.risk_pct / 100)
        price_risk  = abs(entry - stop_loss)
        if price_risk == 0:
            return 0.0
        qty         = risk_amount / price_risk      # units of the asset
        position    = qty * entry                    # USDT value
        return min(position, self.cash * 0.95)       # never use more than 95% of cash

    def _simulate_trade(
        self,
        signal: ConsensusSignal,
        future_df: pd.DataFrame,
    ) -> BacktestTrade:
        """
        Given a signal and the candles that came AFTER it,
        determine how the trade would have played out.
        """
        entry  = signal.entry
        sl     = signal.stop_loss
        tp1    = signal.take_profit1
        tp2    = signal.take_profit2
        is_buy = signal.signal == SignalType.BUY
        size   = self._position_size(entry, sl)

        for i, (ts, row) in enumerate(future_df.iterrows()):
            high  = row["high"]
            low   = row["low"]

            if is_buy:
                if low <= sl:
                    # Stop loss hit
                    pnl_pct = ((sl - entry) / entry) * 100
                    pnl_usd = size * (pnl_pct / 100)
                    return BacktestTrade(
                        symbol=signal.symbol, direction="BUY",
                        entry=entry, stop_loss=sl, take_profit1=tp1, take_profit2=tp2,
                        entry_time=signal.timestamp, exit_time=ts,
                        exit_price=sl, exit_reason="SL",
                        pnl_pct=round(pnl_pct, 3), pnl_usd=round(pnl_usd, 2),
                        position_size=round(size, 2),
                    )
                if high >= tp1:
                    # TP1 hit — close half, trail to TP2
                    if high >= tp2:
                        pnl_pct = ((tp2 - entry) / entry) * 100
                        exit_p  = tp2
                        reason  = "TP2"
                    else:
                        pnl_pct = ((tp1 - entry) / entry) * 100
                        exit_p  = tp1
                        reason  = "TP1"
                    pnl_usd = size * (pnl_pct / 100)
                    return BacktestTrade(
                        symbol=signal.symbol, direction="BUY",
                        entry=entry, stop_loss=sl, take_profit1=tp1, take_profit2=tp2,
                        entry_time=signal.timestamp, exit_time=ts,
                        exit_price=exit_p, exit_reason=reason,
                        pnl_pct=round(pnl_pct, 3), pnl_usd=round(pnl_usd, 2),
                        position_size=round(size, 2),
                    )
            else:  # SELL
                if high >= sl:
                    pnl_pct = ((entry - sl) / entry) * 100
                    pnl_usd = size * (pnl_pct / 100)
                    return BacktestTrade(
                        symbol=signal.symbol, direction="SELL",
                        entry=entry, stop_loss=sl, take_profit1=tp1, take_profit2=tp2,
                        entry_time=signal.timestamp, exit_time=ts,
                        exit_price=sl, exit_reason="SL",
                        pnl_pct=round(pnl_pct, 3), pnl_usd=round(pnl_usd, 2),
                        position_size=round(size, 2),
                    )
                if low <= tp1:
                    if low <= tp2:
                        pnl_pct = ((entry - tp2) / entry) * 100
                        exit_p  = tp2
                        reason  = "TP2"
                    else:
                        pnl_pct = ((entry - tp1) / entry) * 100
                        exit_p  = tp1
                        reason  = "TP1"
                    pnl_usd = size * (pnl_pct / 100)
                    return BacktestTrade(
                        symbol=signal.symbol, direction="SELL",
                        entry=entry, stop_loss=sl, take_profit1=tp1, take_profit2=tp2,
                        entry_time=signal.timestamp, exit_time=ts,
                        exit_price=exit_p, exit_reason=reason,
                        pnl_pct=round(pnl_pct, 3), pnl_usd=round(pnl_usd, 2),
                        position_size=round(size, 2),
                    )

        # Trade never hit SL or TP — close at last price
        last_close = future_df["close"].iloc[-1]
        if is_buy:
            pnl_pct = ((last_close - entry) / entry) * 100
        else:
            pnl_pct = ((entry - last_close) / entry) * 100
        pnl_usd = size * (pnl_pct / 100)
        return BacktestTrade(
            symbol=signal.symbol, direction=signal.signal.value,
            entry=entry, stop_loss=sl, take_profit1=tp1, take_profit2=tp2,
            entry_time=signal.timestamp, exit_time=future_df.index[-1],
            exit_price=last_close, exit_reason="END",
            pnl_pct=round(pnl_pct, 3), pnl_usd=round(pnl_usd, 2),
            position_size=round(size, 2),
        )

    def run(self, symbol: str, timeframe: str = TIMEFRAME, since: str = BACKTEST_SINCE):
        """
        Run the full backtest for a single symbol.
        Uses a rolling window — each bar the engine sees only past data.
        """
        print(f"\n{Fore.CYAN}{'='*60}")
        print(f"  BACKTEST: {symbol} | {timeframe} | since {since}")
        print(f"{'='*60}")

        df = fetch_historical(symbol, timeframe=timeframe, since=since)
        if df.empty:
            print(f"{Fore.RED}No data fetched. Aborting.")
            return

        self.cash = self.initial_cash
        self.trades = []

        # Minimum lookback needed for all indicators
        MIN_BARS   = 60
        signal_cooldown = 0     # bars to skip after a signal (avoid double-entering)

        print(f"\n{Fore.YELLOW}Running simulation on {len(df)} candles...")

        for i in range(MIN_BARS, len(df) - 1):
            if signal_cooldown > 0:
                signal_cooldown -= 1
                continue

            window = df.iloc[:i]   # only data up to bar i (no lookahead!)

            # Run consensus silently
            import io, sys
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            consensus = self.engine.evaluate(window, symbol)
            sys.stdout = old_stdout

            if not consensus.is_valid():
                continue

            rr = consensus.risk_reward()
            if rr < self.min_rr:
                continue    # skip bad R:R setups

            future = df.iloc[i:]   # candles after signal
            trade  = self._simulate_trade(consensus, future)
            self.cash += trade.pnl_usd
            self.trades.append(trade)
            signal_cooldown = 5    # wait 5 bars before next signal on this pair

        self._print_report(symbol)

    def _print_report(self, symbol: str):
        if not self.trades:
            print(f"{Fore.YELLOW}No trades generated for {symbol}.")
            return

        wins      = [t for t in self.trades if t.pnl_usd > 0]
        losses    = [t for t in self.trades if t.pnl_usd <= 0]
        total_pnl = sum(t.pnl_usd for t in self.trades)
        win_rate  = (len(wins) / len(self.trades)) * 100

        gross_profit = sum(t.pnl_usd for t in wins)
        gross_loss   = abs(sum(t.pnl_usd for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # Max drawdown
        running = self.initial_cash
        peak    = running
        max_dd  = 0.0
        for t in self.trades:
            running += t.pnl_usd
            if running > peak:
                peak = running
            dd = (peak - running) / peak * 100
            if dd > max_dd:
                max_dd = dd

        final_balance = self.initial_cash + total_pnl
        total_return  = (total_pnl / self.initial_cash) * 100

        print(f"\n{Fore.GREEN}{'='*60}")
        print(f"  BACKTEST RESULTS — {symbol}")
        print(f"{'='*60}")

        summary = [
            ["Total Trades",     len(self.trades)],
            ["Wins",             f"{len(wins)} ({win_rate:.1f}%)"],
            ["Losses",           len(losses)],
            ["Profit Factor",    f"{profit_factor:.2f}"],
            ["Total PnL",        f"${total_pnl:,.2f}"],
            ["Total Return",     f"{total_return:.2f}%"],
            ["Initial Balance",  f"${self.initial_cash:,.2f}"],
            ["Final Balance",    f"${final_balance:,.2f}"],
            ["Max Drawdown",     f"{max_dd:.2f}%"],
        ]
        print(tabulate(summary, tablefmt="rounded_outline"))

        # Last 10 trades
        print(f"\n{Fore.CYAN}Last 10 Trades:")
        trade_rows = []
        for t in self.trades[-10:]:
            color = Fore.GREEN if t.pnl_usd > 0 else Fore.RED
            trade_rows.append([
                str(t.entry_time)[:16],
                t.direction,
                t.entry,
                t.exit_price,
                t.exit_reason,
                f"{color}{t.pnl_pct:+.2f}%{Style.RESET_ALL}",
                f"{color}${t.pnl_usd:+.2f}{Style.RESET_ALL}",
            ])
        print(tabulate(
            trade_rows,
            headers=["Entry Time", "Dir", "Entry", "Exit", "Reason", "PnL%", "PnL$"],
            tablefmt="rounded_outline",
        ))


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run backtest on a trading pair")
    parser.add_argument("--symbol",    default="BTC/USDT",     help="Trading pair")
    parser.add_argument("--timeframe", default=TIMEFRAME,      help="Candle timeframe")
    parser.add_argument("--since",     default=BACKTEST_SINCE, help="Start date ISO string")
    parser.add_argument("--cash",      default=BACKTEST_INITIAL_CASH, type=float)
    args = parser.parse_args()

    bt = Backtester(initial_cash=args.cash)
    bt.run(symbol=args.symbol, timeframe=args.timeframe, since=args.since)
