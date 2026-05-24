"""
strategies/mean_reversion.py
────────────────────────────
Strategy 4: Mean Reversion

Logic:
  When price moves too far from its average, it tends to snap back.

  BUY  → RSI deeply oversold (< 30) AND price below EMA50 (not in strong downtrend)
          AND the previous candle showed a bullish reversal (close > open)

  SELL → RSI deeply overbought (> 70) AND price above EMA50
          AND the previous candle showed a bearish reversal (close < open)

Note: This strategy works AGAINST the trend — so we only trade it
when the main trend conditions are absent. The EMA50 filter
ensures we're not fighting a runaway trend.
"""

import pandas as pd
import pandas_ta as ta

from strategies.base import BaseStrategy, Signal, SignalType
from config.settings import MEAN_REVERSION, SL_ATR_MULTIPLIER, TP1_RR, TP2_RR


class MeanReversionStrategy(BaseStrategy):

    name = "Mean Reversion (RSI)"

    def __init__(self):
        super().__init__(MEAN_REVERSION)

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        p = self.params

        df["RSI"]     = ta.rsi(df["close"], length=p["rsi_period"])
        df["EMA50"]   = ta.ema(df["close"], length=p["ema_period"])
        df["ATRr_14"] = ta.atr(df["high"], df["low"], df["close"], length=14)

        return df.dropna()

    def _bullish_candle(self, df: pd.DataFrame) -> bool:
        """Last closed candle was bullish (close > open)."""
        return df["close"].iloc[-2] > df["open"].iloc[-2]

    def _bearish_candle(self, df: pd.DataFrame) -> bool:
        """Last closed candle was bearish (close < open)."""
        return df["close"].iloc[-2] < df["open"].iloc[-2]

    def generate_signal(self, df: pd.DataFrame, symbol: str) -> Signal:
        df = self._add_indicators(df)

        if len(df) < 55:
            return self.no_signal(symbol)

        p = self.params
        rsi_now   = self._latest(df, "RSI")
        ema50     = self._latest(df, "EMA50")
        close_now = self._latest(df, "close")

        # ── BUY: deeply oversold, showing bullish reversal candle ─────
        if (
            rsi_now < p["rsi_low"]
            and close_now < ema50                   # below the trend line
            and self._bullish_candle(df)            # last candle flipped green
        ):
            sl, tp1, tp2 = self._atr_sl_tp(
                df, SignalType.BUY, close_now,
                SL_ATR_MULTIPLIER * 0.8,    # tighter SL for mean reversion
                tp1_rr=1.2,                 # smaller target — just a snap-back
                tp2_rr=2.0,
            )
            confidence = "HIGH" if rsi_now < 25 else "MEDIUM"
            return Signal(
                strategy     = self.name,
                symbol       = symbol,
                signal       = SignalType.BUY,
                entry        = round(close_now, 4),
                stop_loss    = sl,
                take_profit1 = tp1,
                take_profit2 = tp2,
                confidence   = confidence,
                reason       = f"RSI deeply oversold at {rsi_now:.1f} | "
                               f"Bullish reversal candle | Snap-back to EMA50",
                timestamp    = df.index[-1],
            )

        # ── SELL: deeply overbought, showing bearish reversal candle ──
        if (
            rsi_now > p["rsi_high"]
            and close_now > ema50
            and self._bearish_candle(df)
        ):
            sl, tp1, tp2 = self._atr_sl_tp(
                df, SignalType.SELL, close_now,
                SL_ATR_MULTIPLIER * 0.8,
                tp1_rr=1.2,
                tp2_rr=2.0,
            )
            confidence = "HIGH" if rsi_now > 75 else "MEDIUM"
            return Signal(
                strategy     = self.name,
                symbol       = symbol,
                signal       = SignalType.SELL,
                entry        = round(close_now, 4),
                stop_loss    = sl,
                take_profit1 = tp1,
                take_profit2 = tp2,
                confidence   = confidence,
                reason       = f"RSI deeply overbought at {rsi_now:.1f} | "
                               f"Bearish reversal candle | Snap-back to EMA50",
                timestamp    = df.index[-1],
            )

        return self.no_signal(symbol)
