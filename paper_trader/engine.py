"""
paper_trader/engine.py
──────────────────────
Paper Trading Engine

Simulates real trades using LIVE Binance prices — zero real money.
Run this for 1-2 weeks before going live. You'll see exactly how the
bot performs under real market conditions: real timing, real volatility.

Flow:
  Signal fires → risk manager approves → paper trade opened
  → price checked every hour → SL or TP hit → trade closed
  → P&L logged to file + Supabase → Telegram notified
"""

import json
import os
import time
from datetime import datetime, timezone
from colorama import Fore, init

from strategies.consensus import ConsensusSignal
from strategies.base import SignalType
from risk.manager import RiskManager
from data.fetcher import get_current_price, get_exchange
import tg_bot.bot as tg
import database.supabase_db as db

init(autoreset=True)

PAPER_LOG = "paper_trades.json"


class PaperTrader:

    def __init__(self, risk_manager: RiskManager):
        self.risk     = risk_manager
        self.exchange = get_exchange()

    # ── Open a paper trade ─────────────────────────────────────────────────────

    def open_trade(self, signal: ConsensusSignal) -> bool:
        """
        Attempt to open a paper trade on a consensus signal.
        Risk manager gates it first. Returns True if trade was opened.
        """
        trade = self.risk.open_trade(signal)
        if not trade:
            return False

        # Log locally
        self._append_log({
            "event":       "OPENED",
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "symbol":      trade.symbol,
            "direction":   trade.direction,
            "entry":       trade.entry,
            "stop_loss":   trade.stop_loss,
            "tp1":         trade.take_profit1,
            "tp2":         trade.take_profit2,
            "size_usdt":   trade.position_size,
            "strategy":    trade.strategy,
        })

        # Log to Supabase
        db.log_signal(signal)
        db.log_trade_opened(
            symbol        = trade.symbol,
            direction     = trade.direction,
            entry         = trade.entry,
            stop_loss     = trade.stop_loss,
            take_profit1  = trade.take_profit1,
            take_profit2  = trade.take_profit2,
            position_size = trade.position_size,
            quantity      = trade.quantity,
            strategy      = trade.strategy,
            mode          = "paper",
            trade_id      = str(trade.trade_id) if hasattr(trade, 'trade_id') else "",
        )

        # Telegram: signal alert first, then trade confirmation
        tg.send_signal(signal)
        tg.send_trade_opened(
            symbol        = trade.symbol,
            direction     = trade.direction,
            entry         = trade.entry,
            stop_loss     = trade.stop_loss,
            take_profit1  = trade.take_profit1,
            take_profit2  = trade.take_profit2,
            position_size = trade.position_size,
            mode          = "paper",
        )

        print(f"{Fore.GREEN}[PAPER] Trade opened → "
              f"{trade.direction} {trade.symbol} @ {trade.entry}")
        return True

    # ── Monitor open trades ────────────────────────────────────────────────────

    def monitor_open_trades(self):
        """
        Check all open trades against current live prices.
        Called every hour by the scheduler.
        Closes any trade that has hit its SL or TP.
        """
        if not self.risk.open_trades:
            print(f"{Fore.YELLOW}[PAPER] No open trades to monitor.")
            return

        print(f"\n{Fore.CYAN}[PAPER] Monitoring {len(self.risk.open_trades)} open trade(s)...")

        # Fetch prices for all open symbols at once
        prices: dict[str, float] = {}
        for trade in self.risk.open_trades:
            try:
                prices[trade.symbol] = get_current_price(trade.symbol, self.exchange)
                time.sleep(0.2)
            except Exception as e:
                print(f"{Fore.RED}[PAPER] Price fetch error ({trade.symbol}): {e}")

        # Check each trade
        to_close = self.risk.check_open_trades(prices)
        for item in to_close:
            self._execute_close(item["trade"], item["exit"], item["reason"])

        # Print unrealized P&L for trades still open
        for trade in self.risk.open_trades:
            price = prices.get(trade.symbol)
            if price is None:
                continue
            is_buy   = trade.direction == "BUY"
            unreal   = ((price - trade.entry) / trade.entry * 100) if is_buy else \
                       ((trade.entry - price) / trade.entry * 100)
            color    = Fore.GREEN if unreal >= 0 else Fore.RED
            print(f"  {color}{trade.direction} {trade.symbol}: "
                  f"entry={trade.entry} now={price:.4f} unrealized={unreal:+.2f}%")

    # ── Execute close ──────────────────────────────────────────────────────────
        
    def _execute_close(self, trade, exit_price: float, reason: str):
        is_buy  = trade.direction == "BUY"
        pnl_pct = ((exit_price - trade.entry) / trade.entry * 100) if is_buy else \
                  ((trade.entry - exit_price) / trade.entry * 100)

        # ── Partial close at TP1 ──────────────────────────────────────
        if reason == "TP1_PARTIAL":
            # Half position already closed in risk manager
            # Calculate PnL on the half that was closed
            closed_size = trade.position_size  # already halved, so this is the closed half
            pnl_usd = round(closed_size * (pnl_pct / 100), 2)
            self.risk.balance += pnl_usd
            self.risk.daily.realized_pnl += pnl_usd
            self.risk.daily.wins += 1
            self.risk._save_state()

            print(f"{Fore.GREEN}[PAPER] TP1 partial close → {trade.symbol} "
                f"@ {exit_price} | 50% closed | PnL: ${pnl_usd:+.2f} | "
                f"SL moved to entry, targeting TP2: {trade.take_profit2}")

            self._append_log({
                "event": "TP1_PARTIAL", "timestamp": datetime.now(timezone.utc).isoformat(),
                "symbol": trade.symbol, "direction": trade.direction,
                "entry": trade.entry, "exit_price": exit_price,
                "reason": "TP1_PARTIAL", "pnl_usd": pnl_usd, "pnl_pct": round(pnl_pct, 3),
                "strategy": trade.strategy, "note": "50% closed, SL moved to entry",
            })

            tg.send_trade_closed(
                symbol=trade.symbol, direction=trade.direction,
                entry=trade.entry, exit_price=exit_price,
                pnl_usd=pnl_usd, pnl_pct=pnl_pct,
                reason="TP1 (50% partial — holding for TP2)",
                balance=self.risk.balance, mode="paper",
            )
            return  # don't fully close — trade stays open for TP2

        # ── Full close (SL or TP2) ────────────────────────────────────
        pnl_usd = self.risk.close_trade(trade.symbol, exit_price, reason)
        if pnl_usd is None:
            return

        self._append_log({
            "event": "CLOSED", "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": trade.symbol, "direction": trade.direction,
            "entry": trade.entry, "exit_price": exit_price,
            "reason": reason, "pnl_usd": pnl_usd,
            "pnl_pct": round(pnl_pct, 3), "strategy": trade.strategy,
        })

        db.log_trade_closed(
            symbol=trade.symbol, direction=trade.direction,
            entry=trade.entry, exit_price=exit_price,
            stop_loss=trade.stop_loss, take_profit1=trade.take_profit1,
            take_profit2=trade.take_profit2, position_size=trade.position_size,
            pnl_usd=pnl_usd, pnl_pct=pnl_pct, exit_reason=reason,
            strategy=trade.strategy, opened_at=trade.opened_at, mode="paper",
        )

        tg.send_trade_closed(
            symbol=trade.symbol, direction=trade.direction,
            entry=trade.entry, exit_price=exit_price,
            pnl_usd=pnl_usd, pnl_pct=pnl_pct,
            reason=reason, balance=self.risk.balance, mode="paper",
        )

        if self.risk.is_halted:
            tg.send_halt_alert(
                daily_pnl=self.risk.daily.realized_pnl,
                limit_pct=5.0, balance=self.risk.balance,
            )    
                 

    # ── Performance summary ────────────────────────────────────────────────────

    def performance_summary(self) -> str:
        if not os.path.exists(PAPER_LOG):
            return "No paper trades recorded yet."
        try:
            with open(PAPER_LOG) as f:
                logs = json.load(f)
        except Exception:
            return "Error reading paper trade log."

        closed    = [r for r in logs if r.get("event") == "CLOSED"]
        if not closed:
            return "No closed paper trades yet."

        wins      = [r for r in closed if r.get("pnl_usd", 0) > 0]
        losses    = [r for r in closed if r.get("pnl_usd", 0) <= 0]
        total_pnl = sum(r.get("pnl_usd", 0) for r in closed)
        wr        = len(wins) / len(closed) * 100

        return (
            f"📊 Paper Performance\n"
            f"Trades: {len(closed)} | W:{len(wins)} L:{len(losses)} | WR:{wr:.1f}%\n"
            f"Total P&L: ${total_pnl:+.2f} USDT"
        )

    # ── Logging ────────────────────────────────────────────────────────────────

    def _append_log(self, record: dict):
        logs = []
        if os.path.exists(PAPER_LOG):
            try:
                with open(PAPER_LOG) as f:
                    logs = json.load(f)
            except Exception:
                pass
        logs.append(record)
        with open(PAPER_LOG, "w") as f:
            json.dump(logs, f, indent=2, default=str)
