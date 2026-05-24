"""
risk/manager.py
───────────────
Risk Manager — the financial gatekeeper.

Every trade must pass through here. Handles:
  - Position sizing  (how much USDT per trade)
  - Daily loss halt  (stop trading if down X% today)
  - Open trade cap   (never exceed MAX_OPEN_TRADES)
  - Pair deduplication (no double-entry on same pair)
  - R:R filter       (reject poor reward:risk setups)

State is persisted to risk/state.json so restarts don't lose context.
"""

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, date, timezone
from typing import Optional
from colorama import Fore, init

from config.settings import (
    RISK_PER_TRADE, MAX_OPEN_TRADES,
    DAILY_LOSS_LIMIT, MIN_RR_RATIO,
)
from strategies.consensus import ConsensusSignal
from strategies.base import SignalType

init(autoreset=True)

STATE_FILE = "risk/state.json"


@dataclass
class OpenTrade:
    symbol:        str
    direction:     str            # "BUY" | "SELL"
    entry:         float
    stop_loss:     float
    take_profit1:  float
    take_profit2:  float
    position_size: float          # USDT allocated
    quantity:      float          # asset units
    opened_at:     str            # ISO timestamp
    strategy:      str   = ""
    trade_id:      str   = ""     # exchange order ID (Week 4)
    tp1_hit:       bool  = False


@dataclass
class DailyStats:
    date:          str   = ""
    starting_cash: float = 0.0
    realized_pnl:  float = 0.0
    trades_taken:  int   = 0
    wins:          int   = 0
    losses:        int   = 0
    halted:        bool  = False


class RiskManager:

    def __init__(self, account_balance: float):
        self.balance      = account_balance
        self.open_trades: list[OpenTrade] = []
        self.daily        = DailyStats()
        self._load_state()
        self._refresh_daily()

    # ── Properties ─────────────────────────────────────────────────────────────

    @property
    def is_halted(self) -> bool:
        return self.daily.halted

    # ── Gate ───────────────────────────────────────────────────────────────────

    def can_trade(self, signal: ConsensusSignal) -> tuple[bool, str]:
        """Master gate. Returns (allowed, reason). Call before every trade."""
        if self.is_halted:
            return False, f"🛑 Daily loss limit hit ({DAILY_LOSS_LIMIT}%). Halted for today."
        if len(self.open_trades) >= MAX_OPEN_TRADES:
            return False, f"⚠️ Max open trades ({MAX_OPEN_TRADES}) reached."
        if any(t.symbol == signal.symbol for t in self.open_trades):
            return False, f"⚠️ Already in {signal.symbol}."
        rr = signal.risk_reward()
        if rr < MIN_RR_RATIO:
            return False, f"⚠️ R:R {rr:.2f} below minimum {MIN_RR_RATIO}."
        size = self.calculate_position_size(signal)
        if size <= 0:
            return False, "⚠️ Position size is zero — balance too low."
        return True, "OK"

    # ── Position sizing ────────────────────────────────────────────────────────

    def calculate_position_size(self, signal: ConsensusSignal) -> float:
        """Risk RISK_PER_TRADE% of balance per trade, capped at 30% of balance."""
        risk_amount   = self.balance * (RISK_PER_TRADE / 100)
        price_risk    = abs(signal.entry - signal.stop_loss)
        if price_risk == 0 or signal.entry == 0:
            return 0.0
        risk_pct      = price_risk / signal.entry
        position_usdt = risk_amount / risk_pct
        return round(min(position_usdt, self.balance * 0.30), 2)

    def calculate_quantity(self, entry: float, position_usdt: float) -> float:
        return round(position_usdt / entry, 6) if entry > 0 else 0.0

    # ── Open / close ───────────────────────────────────────────────────────────

    def open_trade(self, signal: ConsensusSignal) -> Optional[OpenTrade]:
        """Register a new open trade. Returns the OpenTrade object or None."""
        allowed, reason = self.can_trade(signal)
        if not allowed:
            print(f"{Fore.YELLOW}[RISK] Trade rejected: {reason}")
            return None

        size     = self.calculate_position_size(signal)
        qty      = self.calculate_quantity(signal.entry, size)
        strategy = " + ".join(s.strategy for s in signal.contributors)

        trade = OpenTrade(
            symbol        = signal.symbol,
            direction     = signal.signal.value,
            entry         = signal.entry,
            stop_loss     = signal.stop_loss,
            take_profit1  = signal.take_profit1,
            take_profit2  = signal.take_profit2,
            position_size = size,
            quantity      = qty,
            opened_at     = datetime.now(timezone.utc).isoformat(),
            strategy      = strategy,
        )
        self.open_trades.append(trade)
        self.daily.trades_taken += 1
        self._save_state()

        print(f"{Fore.GREEN}[RISK] Trade opened: {trade.direction} {trade.symbol} "
              f"@ {trade.entry} | ${size:.2f} USDT | qty={qty}")
        return trade

    def close_trade(self, symbol: str, exit_price: float, reason: str) -> Optional[float]:
        """Close a trade, update balance/daily stats. Returns PnL in USDT."""
        trade = next((t for t in self.open_trades if t.symbol == symbol), None)
        if not trade:
            print(f"{Fore.YELLOW}[RISK] No open trade for {symbol}")
            return None

        is_buy  = trade.direction == "BUY"
        pnl_pct = ((exit_price - trade.entry) / trade.entry) if is_buy else \
                  ((trade.entry - exit_price) / trade.entry)
        pnl_usd = round(trade.position_size * pnl_pct, 2)

        self.balance             += pnl_usd
        self.daily.realized_pnl  += pnl_usd
        if pnl_usd > 0:
            self.daily.wins   += 1
        else:
            self.daily.losses += 1

        self.open_trades = [t for t in self.open_trades if t.symbol != symbol]

        # Check halt condition
        daily_pnl_pct = (self.daily.realized_pnl / self.daily.starting_cash * 100
                         if self.daily.starting_cash > 0 else 0)
        if daily_pnl_pct <= -DAILY_LOSS_LIMIT:
            self.daily.halted = True
            print(f"{Fore.RED}[RISK] ⛔ DAILY LOSS LIMIT HIT ({daily_pnl_pct:.2f}%). Halted.")

        self._save_state()

        color = Fore.GREEN if pnl_usd >= 0 else Fore.RED
        print(f"{color}[RISK] Closed {symbol} @ {exit_price} | {reason} | "
              f"PnL: ${pnl_usd:+.2f} ({pnl_pct*100:+.2f}%)")
        return pnl_usd

    def get_trade(self, symbol: str) -> Optional[OpenTrade]:
        return next((t for t in self.open_trades if t.symbol == symbol), None)


    def check_open_trades(self, prices: dict[str, float]) -> list[dict]:
        """Return list of {trade, exit, reason} for any trade that hit SL/TP."""
        to_close = []
        for t in self.open_trades:
            price = prices.get(t.symbol)
            if price is None:
                continue
            buy = t.direction == "BUY"
            if buy:
                if price <= t.stop_loss:
                    to_close.append({"trade": t, "exit": t.stop_loss, "reason": "SL"})
                elif price >= t.take_profit2:
                    to_close.append({"trade": t, "exit": t.take_profit2, "reason": "TP2"})
                elif price >= t.take_profit1 and not t.tp1_hit:
                    # TP1 hit — take 50% profit, move SL to entry, keep trade open for TP2
                    t.tp1_hit   = True
                    t.stop_loss = t.entry   # move SL to breakeven
                    t.position_size = round(t.position_size * 0.5, 2)  # halve position
                    t.quantity  = round(t.quantity * 0.5, 6)
                    self._save_state()
                    to_close.append({"trade": t, "exit": t.take_profit1, "reason": "TP1_PARTIAL"})
            else:  # SELL
                if price >= t.stop_loss:
                    to_close.append({"trade": t, "exit": t.stop_loss, "reason": "SL"})
                elif price <= t.take_profit2:
                    to_close.append({"trade": t, "exit": t.take_profit2, "reason": "TP2"})
                elif price <= t.take_profit1 and not t.tp1_hit:
                    t.tp1_hit   = True
                    t.stop_loss = t.entry
                    t.position_size = round(t.position_size * 0.5, 2)
                    t.quantity  = round(t.quantity * 0.5, 6)
                    self._save_state()
                    to_close.append({"trade": t, "exit": t.take_profit1, "reason": "TP1_PARTIAL"})
        return to_close

    # ── Reporting ──────────────────────────────────────────────────────────────

    def status_report(self) -> str:
        d = self.daily
        pct = (d.realized_pnl / d.starting_cash * 100) if d.starting_cash else 0
        total = d.wins + d.losses
        wr    = (d.wins / total * 100) if total else 0
        lines = [
            f"💰 Balance:      ${self.balance:,.2f} USDT",
            f"📊 Daily P&L:    ${d.realized_pnl:+.2f} ({pct:+.2f}%)",
            f"📈 Open Trades:  {len(self.open_trades)}/{MAX_OPEN_TRADES}",
            f"🔄 Today:        {total} trades | {d.wins}W / {d.losses}L | WR {wr:.0f}%",
        ]
        if self.is_halted:
            lines.append("🛑 BOT HALTED — daily loss limit reached")
        for t in self.open_trades:
            lines.append(f"  • {t.symbol} {t.direction} @ {t.entry} | ${t.position_size:.2f}")
        return "\n".join(lines)

    # ── Persistence ────────────────────────────────────────────────────────────

    def _save_state(self):
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump({
                "balance":     self.balance,
                "open_trades": [asdict(t) for t in self.open_trades],
                "daily":       asdict(self.daily),
            }, f, indent=2)

    def _load_state(self):
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE) as f:
                s = json.load(f)
            self.balance     = s.get("balance", self.balance)
            self.open_trades = [OpenTrade(**t) for t in s.get("open_trades", [])]
            if "daily" in s:
                self.daily = DailyStats(**s["daily"])
            print(f"{Fore.CYAN}[RISK] State loaded — ${self.balance:,.2f} | "
                  f"{len(self.open_trades)} open trades")
        except Exception as e:
            print(f"{Fore.YELLOW}[RISK] Could not load state ({e}) — starting fresh")

    def _refresh_daily(self):
        today = date.today().isoformat()
        if self.daily.date != today:
            print(f"{Fore.CYAN}[RISK] New day — resetting daily stats ({today})")
            self.daily = DailyStats(date=today, starting_cash=self.balance)
            self._save_state()