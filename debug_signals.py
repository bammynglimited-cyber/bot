"""
debug_signals.py
Run this to see exactly why no signals are firing.
Place in your TRADEBOT folder and run: python debug_signals.py

python debug_signals.py
"""

import sys
sys.path.insert(0, '.')

from data.fetcher import fetch_all
from config.settings import WATCHLIST, TIMEFRAME, CANDLE_LIMIT, TREND, MOMENTUM, BREAKOUT, MEAN_REVERSION
import pandas as pd
import pandas_ta as ta

def debug_pair(symbol: str, df: pd.DataFrame):
    print(f"\n{'='*60}")
    print(f"  {symbol} — last candle: {df.index[-1]}")
    print(f"  Close: {df['close'].iloc[-1]:.4f}")
    print(f"{'='*60}")

    close = df['close']
    high  = df['high']
    low   = df['low']
    vol   = df['volume']

    # ── TREND ─────────────────────────────────────────────────
    ema_fast = ta.ema(close, length=TREND['ema_fast']).iloc[-1]
    ema_slow = ta.ema(close, length=TREND['ema_slow']).iloc[-1]
    adx_df   = ta.adx(high, low, close, length=TREND['adx_period'])
    adx_col  = [c for c in adx_df.columns if c.startswith('ADX_')]
    adx      = adx_df[adx_col[0]].iloc[-1] if adx_col else 0

    print(f"\n📊 TREND:")
    print(f"   EMA{TREND['ema_fast']}: {ema_fast:.4f} | EMA{TREND['ema_slow']}: {ema_slow:.4f}")
    print(f"   EMA fast {'>' if ema_fast > ema_slow else '<'} EMA slow → {'BUY' if ema_fast > ema_slow else 'SELL'} bias")
    print(f"   ADX: {adx:.2f} (min needed: {TREND['adx_min']}) → {'✅ TRENDING' if adx >= TREND['adx_min'] else '❌ NOT TRENDING'}")
    if ema_fast > ema_slow and adx >= TREND['adx_min']:
        print("   → Would fire: BUY")
    elif ema_fast < ema_slow and adx >= TREND['adx_min']:
        print("   → Would fire: SELL")
    else:
        print("   → No signal (ADX too low or EMA not crossed)")

    # ── MOMENTUM ──────────────────────────────────────────────
    rsi    = ta.rsi(close, length=MOMENTUM['rsi_period']).iloc[-1]
    macd_df = ta.macd(close, fast=MOMENTUM['macd_fast'], slow=MOMENTUM['macd_slow'], signal=MOMENTUM['macd_signal'])
    macd_col = [c for c in macd_df.columns if 'MACD_' in c and 'Signal' not in c and 'Hist' not in c]
    sig_col  = [c for c in macd_df.columns if 'MACDs_' in c]
    macd_val = macd_df[macd_col[0]].iloc[-1] if macd_col else 0
    macd_sig = macd_df[sig_col[0]].iloc[-1] if sig_col else 0

    print(f"\n📊 MOMENTUM:")
    print(f"   RSI: {rsi:.2f} (oversold<{MOMENTUM['rsi_oversold']} overbought>{MOMENTUM['rsi_overbought']})")
    print(f"   MACD: {macd_val:.4f} | Signal: {macd_sig:.4f} → {'MACD>Signal ✅' if macd_val > macd_sig else 'MACD<Signal ❌'}")
    if rsi < MOMENTUM['rsi_oversold'] and macd_val > macd_sig:
        print("   → Would fire: BUY")
    elif rsi > MOMENTUM['rsi_overbought'] and macd_val < macd_sig:
        print("   → Would fire: SELL")
    else:
        print(f"   → No signal. RSI={rsi:.1f} needs <{MOMENTUM['rsi_oversold']} for BUY or >{MOMENTUM['rsi_overbought']} for SELL")

    # ── BREAKOUT ──────────────────────────────────────────────
    bb_df  = ta.bbands(close, length=BREAKOUT['bb_period'], std=BREAKOUT['bb_std'])
    bb_upper = bb_df[[c for c in bb_df.columns if 'BBU' in c][0]].iloc[-1]
    bb_lower = bb_df[[c for c in bb_df.columns if 'BBL' in c][0]].iloc[-1]
    bb_mid   = bb_df[[c for c in bb_df.columns if 'BBM' in c][0]].iloc[-1]
    bb_width = (bb_upper - bb_lower) / bb_mid
    vol_avg  = vol.rolling(BREAKOUT['volume_lookback']).mean().iloc[-1]
    vol_now  = vol.iloc[-1]
    price    = close.iloc[-1]

    print(f"\n📊 BREAKOUT:")
    print(f"   BB Width: {bb_width:.4f} (squeeze<{BREAKOUT['squeeze_threshold']}) → {'SQUEEZE ✅' if bb_width < BREAKOUT['squeeze_threshold'] else 'No squeeze ❌'}")
    print(f"   Volume: {vol_now:.0f} | Avg: {vol_avg:.0f} | Ratio: {vol_now/vol_avg:.2f}x (need {BREAKOUT['volume_mult']}x)")
    print(f"   Price vs BB: Upper={bb_upper:.2f} Lower={bb_lower:.2f} Price={price:.2f}")
    squeeze = bb_width < BREAKOUT['squeeze_threshold']
    vol_ok  = vol_now > vol_avg * BREAKOUT['volume_mult']
    if squeeze and vol_ok and price > bb_upper:
        print("   → Would fire: BUY")
    elif squeeze and vol_ok and price < bb_lower:
        print("   → Would fire: SELL")
    else:
        print(f"   → No signal. Squeeze={squeeze} VolumeOK={vol_ok} Price above upper={price > bb_upper}")

    # ── MEAN REVERSION ────────────────────────────────────────
    rsi_mr  = ta.rsi(close, length=MEAN_REVERSION['rsi_period']).iloc[-1]
    ema_mr  = ta.ema(close, length=MEAN_REVERSION['ema_period']).iloc[-1]

    print(f"\n📊 MEAN REVERSION:")
    print(f"   RSI: {rsi_mr:.2f} (low={MEAN_REVERSION['rsi_low']} high={MEAN_REVERSION['rsi_high']})")
    print(f"   EMA{MEAN_REVERSION['ema_period']}: {ema_mr:.4f} | Price: {price:.4f} → {'above EMA' if price > ema_mr else 'below EMA'}")
    if rsi_mr < MEAN_REVERSION['rsi_low'] and price > ema_mr:
        print("   → Would fire: BUY")
    elif rsi_mr > MEAN_REVERSION['rsi_high'] and price < ema_mr:
        print("   → Would fire: SELL")
    else:
        print(f"   → No signal. RSI={rsi_mr:.1f}")

    print(f"\n💡 SUGGESTION: If all 4 show no signal, the market is consolidating.")
    print(f"   Try lowering MIN_STRATEGY_VOTES to 1 or adjusting RSI thresholds.")


if __name__ == "__main__":
    print("Loading candles from Binance...")
    data = fetch_all(WATCHLIST, timeframe=TIMEFRAME, limit=CANDLE_LIMIT)
    for symbol, df in data.items():
        debug_pair(symbol, df)

    print(f"\n\n{'='*60}")
    print("CURRENT SETTINGS:")
    print(f"  MIN_STRATEGY_VOTES: {__import__('config.settings', fromlist=['MIN_STRATEGY_VOTES']).MIN_STRATEGY_VOTES}")
    print(f"  RSI oversold: {MOMENTUM['rsi_oversold']} | overbought: {MOMENTUM['rsi_overbought']}")
    print(f"  ADX min: {TREND['adx_min']}")
    print(f"  BB squeeze threshold: {BREAKOUT['squeeze_threshold']}")
    print(f"\nSUGGESTED RELAXED SETTINGS (add to settings.py):")
    print(f"  MIN_STRATEGY_VOTES = 1  (was 2)")
    print(f"  rsi_oversold: 40  (was 35)")
    print(f"  rsi_overbought: 60  (was 65)")
    print(f"  adx_min: 15  (was 20)")
    print(f"  rsi_low: 35  (was 30)")
    print(f"  rsi_high: 65  (was 70)")
    print('='*60)