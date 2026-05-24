"""
strategies/rsi_divergence.py
RSI Divergence + EMA bounce strategy
Fires when price makes new low but RSI makes higher low (bullish divergence)
or price makes new high but RSI makes lower high (bearish divergence)
"""
import pandas as pd
import pandas_ta as ta
from strategies.base import BaseStrategy, Signal, SignalType
from config.settings import SL_ATR_MULTIPLIER, TP1_RR, TP2_RR

class RSIDivergenceStrategy(BaseStrategy):
    name = "RSI Divergence"

    def __init__(self):
        super().__init__({})

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["RSI"]     = ta.rsi(df["close"], length=14)
        df["EMA50"]   = ta.ema(df["close"], length=50)
        df["EMA200"]  = ta.ema(df["close"], length=200)
        df["ATRr_14"] = ta.atr(df["high"], df["low"], df["close"], length=14)
        return df.dropna()

    def generate_signal(self, df: pd.DataFrame, symbol: str) -> Signal:
        df = self._add_indicators(df)
        if len(df) < 50:
            return self.no_signal(symbol)

        close = df["close"]
        rsi   = df["RSI"]
        entry = close.iloc[-1]
        ema50 = df["EMA50"].iloc[-1]

        # Look at last 10 candles for divergence
        window = 10

        # ── Bullish divergence: price lower low, RSI higher low ──
        price_low_now  = close.iloc[-1]
        price_low_prev = close.iloc[-window:-1].min()
        rsi_now        = rsi.iloc[-1]
        rsi_prev_low   = rsi.iloc[-window:-1].min()

        bullish_div = (
            price_low_now < price_low_prev and   # price made lower low
            rsi_now > rsi_prev_low and            # RSI made higher low
            rsi_now < 50 and                      # RSI still in lower half
            entry > ema50 * 0.97                  # not too far below EMA50
        )

        if bullish_div:
            sl, tp1, tp2 = self._atr_sl_tp(df, SignalType.BUY, entry, SL_ATR_MULTIPLIER, TP1_RR, TP2_RR)
            return Signal(
                strategy=self.name, symbol=symbol, signal=SignalType.BUY,
                entry=round(entry, 4), stop_loss=sl,
                take_profit1=tp1, take_profit2=tp2, confidence="HIGH",
                reason=f"Bullish RSI divergence | RSI={rsi_now:.1f} price lower but RSI higher",
                timestamp=df.index[-1],
            )

        # ── Bearish divergence: price higher high, RSI lower high ──
        price_high_now  = close.iloc[-1]
        price_high_prev = close.iloc[-window:-1].max()
        rsi_prev_high   = rsi.iloc[-window:-1].max()

        bearish_div = (
            price_high_now > price_high_prev and  # price made higher high
            rsi_now < rsi_prev_high and            # RSI made lower high
            rsi_now > 50 and                       # RSI still in upper half
            entry < ema50 * 1.03                   # not too far above EMA50
        )

        if bearish_div:
            sl, tp1, tp2 = self._atr_sl_tp(df, SignalType.SELL, entry, SL_ATR_MULTIPLIER, TP1_RR, TP2_RR)
            return Signal(
                strategy=self.name, symbol=symbol, signal=SignalType.SELL,
                entry=round(entry, 4), stop_loss=sl,
                take_profit1=tp1, take_profit2=tp2, confidence="HIGH",
                reason=f"Bearish RSI divergence | RSI={rsi_now:.1f} price higher but RSI lower",
                timestamp=df.index[-1],
            )

        return self.no_signal(symbol)