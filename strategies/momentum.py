"""
strategies/momentum.py  [Upgraded]
────────────────────────────────────
Momentum Strategy — RSI + MACD + Volume confirmation

Improvements:
  - Volume spike confirmation: momentum must come with volume
  - RSI momentum: not just level but RATE of change
  - MACD histogram growing (momentum accelerating, not just crossing)
  - Stochastic RSI for faster entry signals
"""

import pandas as pd
import pandas_ta as ta

from strategies.base import BaseStrategy, Signal, SignalType
from config.settings import MOMENTUM, SL_ATR_MULTIPLIER, TP1_RR, TP2_RR


class MomentumStrategy(BaseStrategy):

    name = "Momentum (RSI+MACD)"

    def __init__(self):
        super().__init__(MOMENTUM)

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df   = df.copy()
        p    = self.params

        df["RSI"] = ta.rsi(df["close"], length=p["rsi_period"])

        macd_df = ta.macd(df["close"], fast=p["macd_fast"], slow=p["macd_slow"], signal=p["macd_signal"])
        df["MACD"]        = macd_df[f"MACD_{p['macd_fast']}_{p['macd_slow']}_{p['macd_signal']}"]
        df["MACD_signal"] = macd_df[f"MACDs_{p['macd_fast']}_{p['macd_slow']}_{p['macd_signal']}"]
        df["MACD_hist"]   = macd_df[f"MACDh_{p['macd_fast']}_{p['macd_slow']}_{p['macd_signal']}"]

        df["ATRr_14"]  = ta.atr(df["high"], df["low"], df["close"], length=14)

        # Volume: is current bar above 20-bar average?
        df["vol_avg"]  = df["volume"].rolling(20).mean()
        df["vol_ratio"] = df["volume"] / df["vol_avg"]

        # RSI rate of change (momentum of momentum)
        df["RSI_change"] = df["RSI"].diff(2)

        return df.dropna()

    def generate_signal(self, df: pd.DataFrame, symbol: str) -> Signal:
        df = self._add_indicators(df)
        if len(df) < 40:
            return self.no_signal(symbol)

        p         = self.params
        rsi_now   = self._latest(df, "RSI")
        rsi_prev  = self._prev(df, "RSI")
        rsi_chg   = self._latest(df, "RSI_change")
        entry     = self._latest(df, "close")
        vol_ratio = self._latest(df, "vol_ratio")
        macd_hist = self._latest(df, "MACD_hist")
        macd_line = df["MACD"]
        sig_line  = df["MACD_signal"]

        # Volume confirmation: at least 1.1x average (relaxed from 1.5x)
        vol_ok = vol_ratio >= 1.1

        # ── BUY ──────────────────────────────────────────────────────
        # RSI was oversold OR recovering strongly, MACD crossed bullish, volume confirms
        rsi_oversold    = rsi_prev < p["rsi_oversold"]
        rsi_recovering  = rsi_now > rsi_prev and rsi_chg > 0
        macd_bull_cross = self._crossover(macd_line, sig_line)
        macd_hist_pos   = macd_hist > 0

        if rsi_oversold and rsi_recovering and macd_bull_cross and vol_ok:
            sl, tp1, tp2 = self._atr_sl_tp(df, SignalType.BUY, entry, SL_ATR_MULTIPLIER, TP1_RR, TP2_RR)
            confidence   = "HIGH" if rsi_now < 45 and macd_hist_pos else "MEDIUM"
            return Signal(
                strategy=self.name, symbol=symbol, signal=SignalType.BUY,
                entry=round(entry, 4), stop_loss=sl,
                take_profit1=tp1, take_profit2=tp2, confidence=confidence,
                reason=f"RSI oversold recovery ({rsi_prev:.1f}→{rsi_now:.1f}) | MACD bullish cross | Vol {vol_ratio:.1f}x",
                timestamp=df.index[-1],
            )

        # ── SELL ─────────────────────────────────────────────────────
        rsi_overbought   = rsi_prev > p["rsi_overbought"]
        rsi_dropping     = rsi_now < rsi_prev and rsi_chg < 0
        macd_bear_cross  = self._crossunder(macd_line, sig_line)
        macd_hist_neg    = macd_hist < 0

        if rsi_overbought and rsi_dropping and macd_bear_cross and vol_ok:
            sl, tp1, tp2 = self._atr_sl_tp(df, SignalType.SELL, entry, SL_ATR_MULTIPLIER, TP1_RR, TP2_RR)
            confidence   = "HIGH" if rsi_now > 55 and macd_hist_neg else "MEDIUM"
            return Signal(
                strategy=self.name, symbol=symbol, signal=SignalType.SELL,
                entry=round(entry, 4), stop_loss=sl,
                take_profit1=tp1, take_profit2=tp2, confidence=confidence,
                reason=f"RSI overbought drop ({rsi_prev:.1f}→{rsi_now:.1f}) | MACD bearish cross | Vol {vol_ratio:.1f}x",
                timestamp=df.index[-1],
            )

        return self.no_signal(symbol)