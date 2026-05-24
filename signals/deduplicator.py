"""
signals/deduplicator.py
───────────────────────
Signal Deduplicator

Prevents the bot from firing the same signal repeatedly.
Without this, every 4h scan could re-alert a signal already active —
flooding your Telegram and tempting you into double-entering.

How it works:
  Each signal key = symbol + direction (e.g. "BTC/USDT_BUY")
  Once fired, that key is locked for COOLDOWN hours.
  On 4h TF with SIGNAL_COOLDOWN_BARS=6 → 24h cooldown per pair/direction.

State persists to signals/signal_history.json across restarts.
"""

import json
import os
from datetime import datetime, timezone, timedelta
from signal import signal
from colorama import Fore, init

from config.settings import SIGNAL_COOLDOWN_BARS, TIMEFRAME
from strategies.consensus import ConsensusSignal

init(autoreset=True)

HISTORY_FILE = "signals/signal_history.json"

_TF_HOURS = {
    "1m": 1/60, "5m": 5/60, "15m": 0.25, "30m": 0.5,
    "1h": 1, "2h": 2, "4h": 4, "6h": 6, "8h": 8,
    "12h": 12, "1d": 24, "3d": 72, "1w": 168,
}


class SignalDeduplicator:

    def __init__(self, timeframe: str = TIMEFRAME, cooldown_bars: int = SIGNAL_COOLDOWN_BARS):
        tf_hours          = _TF_HOURS.get(timeframe, 4)
        self.cooldown_hrs = tf_hours * cooldown_bars    # 4h × 6 bars = 24h
        self.history: dict[str, str] = {}               # key → ISO timestamp
        self._load()

    def mark_sent(self, signal: ConsensusSignal) -> None:
         """Mark a signal as sent so it won't be re-sent."""
         self.record(signal)

    # ── Public ─────────────────────────────────────────────────────────────────

    def is_duplicate(self, signal: ConsensusSignal) -> bool:
        """
        True if this signal fired within the cooldown window.
        Call BEFORE sending Telegram alert or opening any trade.
        """
        key      = self._key(signal)
        last_str = self.history.get(key)
        if last_str is None:
            return False

        last    = datetime.fromisoformat(last_str)
        age_hrs = (datetime.now(timezone.utc) - last).total_seconds() / 3600

        if age_hrs < self.cooldown_hrs:
            print(f"{Fore.YELLOW}[DEDUP] Skipping {signal.symbol} "
                  f"{signal.signal.value} — fired {age_hrs:.1f}h ago "
                  f"(cooldown={self.cooldown_hrs:.0f}h)")
            return True
        return False

    def record(self, signal: ConsensusSignal):
        """Mark signal as fired. Call RIGHT AFTER sending alert."""
        self.history[self._key(signal)] = datetime.now(timezone.utc).isoformat()
        self._save()

    def clear(self, symbol: str = None):
        """Clear cooldown — optionally for one symbol."""
        if symbol:
            removed = [k for k in self.history if k.startswith(symbol)]
            for k in removed:
                del self.history[k]
            print(f"{Fore.CYAN}[DEDUP] Cleared {len(removed)} entries for {symbol}")
        else:
            self.history.clear()
            print(f"{Fore.CYAN}[DEDUP] Full history cleared")
        self._save()

    def recent_signals(self, hours: int = 48) -> list[dict]:
        """Signals fired in last N hours, newest first."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        out = []
        for key, ts_str in self.history.items():
            ts = datetime.fromisoformat(ts_str)
            if ts > cutoff:
                sym, direction = (key.rsplit("_", 1) + ["?"])[:2]
                out.append({
                    "symbol":    sym,
                    "direction": direction,
                    "fired_at":  ts.strftime("%Y-%m-%d %H:%M UTC"),
                    "hours_ago": round((datetime.now(timezone.utc) - ts).total_seconds() / 3600, 1),
                })
        return sorted(out, key=lambda x: x["fired_at"], reverse=True)

    # ── Internals ──────────────────────────────────────────────────────────────

    def _key(self, signal: ConsensusSignal) -> str:
        return f"{signal.symbol}_{signal.signal.value}"

    def _save(self):
        os.makedirs("signals", exist_ok=True)
        with open(HISTORY_FILE, "w") as f:
            json.dump(self.history, f, indent=2)

    def _load(self):
        if not os.path.exists(HISTORY_FILE):
            return
        try:
            with open(HISTORY_FILE) as f:
                self.history = json.load(f)
            # Auto-purge entries older than 7 days
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            self.history = {
                k: v for k, v in self.history.items()
                if datetime.fromisoformat(v) > cutoff
            }
            print(f"{Fore.CYAN}[DEDUP] Loaded {len(self.history)} history entries")
        except Exception as e:
            print(f"{Fore.YELLOW}[DEDUP] Could not load history: {e}")
