"""
strategies/consensus.py  [Upgraded]
─────────────────────────────────────
Consensus Engine — smarter signal aggregation

Upgrades:
  - Conflict detection: if BUY and SELL votes are equal → skip (market confused)
  - Confidence auto-upgrade: 3+ strategies = always HIGH
  - LOW confidence signals still fire (MIN_VOTES=1) but are labeled clearly
  - vote_count property for Supabase logging
"""

from dataclasses import dataclass, field
from typing import Optional
import pandas as pd
from colorama import Fore, init

from strategies.base import Signal, SignalType
from strategies.trend import TrendStrategy
from strategies.momentum import MomentumStrategy
from strategies.breakout import BreakoutStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.rsi_divergence import RSIDivergenceStrategy
from config.settings import MIN_STRATEGY_VOTES

init(autoreset=True)


@dataclass
class ConsensusSignal:
    symbol:       str
    signal:       SignalType
    buy_votes:    int                    = 0
    sell_votes:   int                    = 0
    total_votes:  int                    = 0
    strategies:   list                   = field(default_factory=list)
    contributors: list                   = field(default_factory=list)
    entry:        float                  = 0.0
    stop_loss:    float                  = 0.0
    take_profit1: float                  = 0.0
    take_profit2: float                  = 0.0
    confidence:   str                    = "LOW"
    timestamp:    Optional[pd.Timestamp] = None

    @property
    def vote_count(self) -> int:
        return max(self.buy_votes, self.sell_votes)

    def is_valid(self) -> bool:
        return self.signal != SignalType.NONE and self.entry > 0

    def risk_reward(self) -> float:
        if self.signal == SignalType.BUY:
            risk   = self.entry - self.stop_loss
            reward = self.take_profit1 - self.entry
        else:
            risk   = self.stop_loss - self.entry
            reward = self.entry - self.take_profit1
        return round(reward / risk, 2) if risk > 0 else 0.0

    def summary(self) -> str:
        icon  = "🟢 BUY" if self.signal == SignalType.BUY else "🔴 SELL"
        conf_color = Fore.GREEN if self.confidence == "HIGH" else Fore.YELLOW if self.confidence == "MEDIUM" else Fore.WHITE
        lines = [
            f"{icon} — {self.symbol}",
            f"Votes: {self.buy_votes} BUY | {self.sell_votes} SELL",
            f"Entry: {self.entry}  SL: {self.stop_loss}  TP1: {self.take_profit1}  TP2: {self.take_profit2}",
            f"R:R = {self.risk_reward()}  | Confidence: {conf_color}{self.confidence}{Fore.RESET}",
            "Agreeing strategies:",
        ]
        for s in self.contributors:
            lines.append(f"  ✓ {s.strategy}: {s.reason}")
        return "\n".join(lines)


class ConsensusEngine:

    def __init__(self):
        self.strategies = [
            TrendStrategy(),
            MomentumStrategy(),
            BreakoutStrategy(),
            MeanReversionStrategy(),
            RSIDivergenceStrategy(),
        ]

    def evaluate(self, df: pd.DataFrame, symbol: str) -> ConsensusSignal:
        votes: list[Signal] = []

        for strategy in self.strategies:
            try:
                sig = strategy.generate_signal(df, symbol)
                votes.append(sig)
                if sig.signal != SignalType.NONE:
                    icon = "🟢" if sig.signal == SignalType.BUY else "🔴"
                    print(f"  {icon} [{sig.strategy}] → {sig.signal.value} | {sig.reason}")
                else:
                    print(f"  ⚪ [{strategy.name}] → no signal")
            except Exception as e:
                print(f"  {Fore.RED}[ERROR] {strategy.name}: {e}")

        buy_signals  = [s for s in votes if s.signal == SignalType.BUY]
        sell_signals = [s for s in votes if s.signal == SignalType.SELL]
        n_buy        = len(buy_signals)
        n_sell       = len(sell_signals)

        # ── Not enough votes ──────────────────────────────────────────
        if n_buy < MIN_STRATEGY_VOTES and n_sell < MIN_STRATEGY_VOTES:
            print(f"{Fore.YELLOW}  → No consensus for {symbol} (B:{n_buy} S:{n_sell})")
            return ConsensusSignal(
                symbol=symbol, signal=SignalType.NONE,
                buy_votes=n_buy, sell_votes=n_sell, total_votes=len(votes), strategies=votes,
            )

        # ── Conflict: equal votes → skip ──────────────────────────────
        if n_buy == n_sell and n_buy >= MIN_STRATEGY_VOTES:
            print(f"{Fore.YELLOW}  → Conflict for {symbol} (B:{n_buy} S:{n_sell}) — skipping")
            return ConsensusSignal(
                symbol=symbol, signal=SignalType.NONE,
                buy_votes=n_buy, sell_votes=n_sell, total_votes=len(votes), strategies=votes,
            )

        # ── Pick direction ────────────────────────────────────────────
        if n_buy > n_sell:
            direction, contributors = SignalType.BUY, buy_signals
        else:
            direction, contributors = SignalType.SELL, sell_signals

        # ── Average the agreeing signals ──────────────────────────────
        entry = sum(s.entry        for s in contributors) / len(contributors)
        sl    = sum(s.stop_loss    for s in contributors) / len(contributors)
        tp1   = sum(s.take_profit1 for s in contributors) / len(contributors)
        tp2   = sum(s.take_profit2 for s in contributors) / len(contributors)

        # ── Confidence ────────────────────────────────────────────────
        n        = len(contributors)
        any_high = any(s.confidence == "HIGH" for s in contributors)
        any_med  = any(s.confidence == "MEDIUM" for s in contributors)

        if n >= 3:
            confidence = "HIGH"     # 3+ strategies always = HIGH
        elif n == 2 and any_high:
            confidence = "HIGH"
        elif n == 2 or any_med:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        return ConsensusSignal(
            symbol=symbol, signal=direction,
            buy_votes=n_buy, sell_votes=n_sell,
            total_votes=len(votes), strategies=votes, contributors=contributors,
            entry=round(entry, 4), stop_loss=round(sl, 4),
            take_profit1=round(tp1, 4), take_profit2=round(tp2, 4),
            confidence=confidence, timestamp=df.index[-1],
        )

    def scan_all(self, data: dict) -> list[ConsensusSignal]:
        results = []
        for symbol, df in data.items():
            print(f"\n{Fore.CYAN}━━━ Scanning {symbol} ━━━")
            consensus = self.evaluate(df, symbol)
            if consensus.is_valid():
                print(f"\n{Fore.GREEN}✅ SIGNAL FIRED for {symbol}:")
                print(consensus.summary())
                results.append(consensus)
            else:
                print(f"{Fore.YELLOW}  → No consensus signal for {symbol} "
                      f"(B:{consensus.buy_votes} S:{consensus.sell_votes})")
        return results