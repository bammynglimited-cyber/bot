"""
strategies/breakout.py
──────────────────────
Strategy 3: Breakout

Logic:
  A "squeeze" happens when Bollinger Bands narrow (low volatility / coiling).
  When price finally breaks out with HIGH volume, it's a strong directional move.

  BUY  → BB was in squeeze AND price closes ABOVE upper band AND volume spike
  SELL → BB was in squeeze AND price closes BELOW lower band AND volume spike

Volume confirmation is key — breakouts on low volume often fail (fakeouts).
"""

import pandas as pd
import pandas_ta as ta

from strategies.base import BaseStrategy, Signal, SignalType
from config.settings import BREAKOUT, SL_ATR_MULTIPLIER, TP1_RR, TP2_RR


class BreakoutStrategy(BaseStrategy):

    name = "Breakout (BB+Vol)"

    def __init__(self):
        super().__init__(BREAKOUT)

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        p = self.params

        bb = ta.bbands(df["close"], length=p["bb_period"], std=p["bb_std"])

        # Find actual column names dynamically (pandas_ta naming can vary)
        upper_col = [c for c in bb.columns if c.startswith("BBU")][0]
        mid_col   = [c for c in bb.columns if c.startswith("BBM")][0]
        lower_col = [c for c in bb.columns if c.startswith("BBL")][0]

        df["BB_upper"] = bb[upper_col]
        df["BB_mid"]   = bb[mid_col]
        df["BB_lower"] = bb[lower_col]
        df["BB_width"] = (df["BB_upper"] - df["BB_lower"]) / df["BB_mid"]


        # Volume average (rolling)
        df["vol_avg"] = df["volume"].rolling(window=p["volume_lookback"]).mean()

        # ATR for SL calculation
        df["ATRr_14"] = ta.atr(df["high"], df["low"], df["close"], length=14)

        return df.dropna()

    def _was_squeezing(self, df: pd.DataFrame, lookback: int = 5) -> bool:
        """
        Check if the BB was in a squeeze (narrow) in recent candles.
        We look back a few bars because the breakout candle itself
        will have expanded bands.
        """
        threshold = self.params["squeeze_threshold"]
        recent_widths = df["BB_width"].iloc[-(lookback + 1):-1]  # exclude last candle
        return bool((recent_widths < threshold).any())

    def generate_signal(self, df: pd.DataFrame, symbol: str) -> Signal:
        df = self._add_indicators(df)

        if len(df) < 50:
            return self.no_signal(symbol)

        p = self.params

        close_now  = self._latest(df, "close")
        bb_upper   = self._latest(df, "BB_upper")
        bb_lower   = self._latest(df, "BB_lower")
        vol_now    = self._latest(df, "volume")
        vol_avg    = self._latest(df, "vol_avg")

        # ── Volume must confirm the breakout ──────────────────────────
        volume_spike = vol_now >= (vol_avg * p["volume_mult"])
        if not volume_spike:
            return self.no_signal(symbol)

        # ── Must have been squeezing recently ─────────────────────────
        was_squeeze = self._was_squeezing(df)
        if not was_squeeze:
            return self.no_signal(symbol)

        # ── BUY: price breaks above upper BB ─────────────────────────
        if close_now > bb_upper:
            sl, tp1, tp2 = self._atr_sl_tp(
                df, SignalType.BUY, close_now, SL_ATR_MULTIPLIER, TP1_RR, TP2_RR
            )
            vol_ratio  = vol_now / vol_avg
            confidence = "HIGH" if vol_ratio > 2.0 else "MEDIUM"
            return Signal(
                strategy     = self.name,
                symbol       = symbol,
                signal       = SignalType.BUY,
                entry        = round(close_now, 4),
                stop_loss    = sl,
                take_profit1 = tp1,
                take_profit2 = tp2,
                confidence   = confidence,
                reason       = f"BB squeeze breakout ABOVE upper band | "
                               f"Volume {vol_ratio:.1f}× average",
                timestamp    = df.index[-1],
            )

        # ── SELL: price breaks below lower BB ────────────────────────
        if close_now < bb_lower:
            sl, tp1, tp2 = self._atr_sl_tp(
                df, SignalType.SELL, close_now, SL_ATR_MULTIPLIER, TP1_RR, TP2_RR
            )
            vol_ratio  = vol_now / vol_avg
            confidence = "HIGH" if vol_ratio > 2.0 else "MEDIUM"
            return Signal(
                strategy     = self.name,
                symbol       = symbol,
                signal       = SignalType.SELL,
                entry        = round(close_now, 4),
                stop_loss    = sl,
                take_profit1 = tp1,
                take_profit2 = tp2,
                confidence   = confidence,
                reason       = f"BB squeeze breakdown BELOW lower band | "
                               f"Volume {vol_ratio:.1f}× average",
                timestamp    = df.index[-1],
            )

        return self.no_signal(symbol)
