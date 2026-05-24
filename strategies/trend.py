"""
strategies/trend.py  [Upgraded]
────────────────────────────────
Trend Strategy — EMA crossover + ADX + VWAP filter

Improvements:
  - VWAP filter: only BUY above VWAP, only SELL below VWAP
  - +DI/-DI confirmation: directional index must agree
  - EMA slope: EMA must be moving in signal direction
  - Candle body confirmation
"""

import pandas as pd
import pandas_ta as ta

from strategies.base import BaseStrategy, Signal, SignalType
from config.settings import TREND, SL_ATR_MULTIPLIER, TP1_RR, TP2_RR


class TrendStrategy(BaseStrategy):

    name = "Trend (EMA+ADX)"

    def __init__(self):
        super().__init__(TREND)

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        fast       = self.params["ema_fast"]
        slow       = self.params["ema_slow"]
        adx_period = self.params["adx_period"]

        df[f"EMA{fast}"] = ta.ema(df["close"], length=fast)
        df[f"EMA{slow}"] = ta.ema(df["close"], length=slow)

        adx_df    = ta.adx(df["high"], df["low"], df["close"], length=adx_period)
        df["ADX"] = adx_df[f"ADX_{adx_period}"]
        df["+DI"] = adx_df[f"DMP_{adx_period}"]
        df["-DI"] = adx_df[f"DMN_{adx_period}"]

        df["ATRr_14"] = ta.atr(df["high"], df["low"], df["close"], length=14)

        # VWAP — 20-bar rolling VWAP
        typical   = (df["high"] + df["low"] + df["close"]) / 3
        df["VWAP"] = (typical * df["volume"]).rolling(20).sum() / df["volume"].rolling(20).sum()

        return df.dropna()

    def generate_signal(self, df: pd.DataFrame, symbol: str) -> Signal:
        df = self._add_indicators(df)
        if len(df) < 30:
            return self.no_signal(symbol)

        fast_col = f"EMA{self.params['ema_fast']}"
        slow_col = f"EMA{self.params['ema_slow']}"
        adx_min  = self.params["adx_min"]

        ema_fast = df[fast_col]
        ema_slow = df[slow_col]
        adx_now  = self._latest(df, "ADX")
        di_plus  = self._latest(df, "+DI")
        di_minus = self._latest(df, "-DI")
        entry    = self._latest(df, "close")
        vwap     = self._latest(df, "VWAP")

        # EMA slope over last 3 bars
        ema_slope      = ema_fast.iloc[-1] - ema_fast.iloc[-3]
        bullish_candle = df["close"].iloc[-1] > df["open"].iloc[-1]
        bearish_candle = df["close"].iloc[-1] < df["open"].iloc[-1]

        if adx_now < adx_min:
            return self.no_signal(symbol)

        # ── BUY ──────────────────────────────────────────────────────
        if (ema_fast.iloc[-1] > ema_slow.iloc[-1]
                and di_plus > di_minus
                and ema_slope > 0
                and entry > vwap
                and bullish_candle):
            sl, tp1, tp2 = self._atr_sl_tp(df, SignalType.BUY, entry, SL_ATR_MULTIPLIER, TP1_RR, TP2_RR)
            confidence   = "HIGH" if adx_now > 25 and di_plus > di_minus + 5 else "MEDIUM"
            return Signal(
                strategy=self.name, symbol=symbol, signal=SignalType.BUY,
                entry=round(entry, 4), stop_loss=sl,
                take_profit1=tp1, take_profit2=tp2, confidence=confidence,
                reason=f"Uptrend EMA{self.params['ema_fast']}>EMA{self.params['ema_slow']} | ADX={adx_now:.1f} | Above VWAP | +DI>{di_minus:.1f}",
                timestamp=df.index[-1],
            )

        # ── SELL ─────────────────────────────────────────────────────
        if (ema_fast.iloc[-1] < ema_slow.iloc[-1]
                and di_minus > di_plus
                and ema_slope < 0
                and entry < vwap
                and bearish_candle):
            sl, tp1, tp2 = self._atr_sl_tp(df, SignalType.SELL, entry, SL_ATR_MULTIPLIER, TP1_RR, TP2_RR)
            confidence   = "HIGH" if adx_now > 25 and di_minus > di_plus + 5 else "MEDIUM"
            return Signal(
                strategy=self.name, symbol=symbol, signal=SignalType.SELL,
                entry=round(entry, 4), stop_loss=sl,
                take_profit1=tp1, take_profit2=tp2, confidence=confidence,
                reason=f"Downtrend EMA{self.params['ema_fast']}<EMA{self.params['ema_slow']} | ADX={adx_now:.1f} | Below VWAP | -DI>{di_plus:.1f}",
                timestamp=df.index[-1],
            )

        return self.no_signal(symbol)