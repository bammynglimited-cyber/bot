"""
config/settings.py
──────────────────
Central configuration — ONLY constants and env vars here.
No imports from strategies, database, or other bot modules.
"""

import os
from dotenv import load_dotenv
load_dotenv()

# ── Exchange ───────────────────────────────────────────────────
EXCHANGE_ID      = "bybit"
BINANCE_API_KEY  = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET   = os.getenv("BINANCE_SECRET", "")

# ── Telegram ───────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Supabase ───────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# ── Encryption ─────────────────────────────────────────────────
ENCRYPTION_SECRET = os.getenv("ENCRYPTION_SECRET", "")

# ── Trade Mode ─────────────────────────────────────────────────
TRADE_MODE  = os.getenv("TRADE_MODE",  "paper")
SIGNAL_MODE = os.getenv("SIGNAL_MODE", "paper")

# ── Watchlist ──────────────────────────────────────────────────
WATCHLIST = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
]

# ── Timeframes ─────────────────────────────────────────────────
TIMEFRAME      = "4h"
FAST_TIMEFRAME = "1h"
CANDLE_LIMIT   = 200

# ── Risk Management ────────────────────────────────────────────
RISK_PER_TRADE   = float(os.getenv("RISK_PER_TRADE",   1.5))
MAX_OPEN_TRADES  = int(os.getenv("MAX_OPEN_TRADES",    3))
DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", 5.0))
MIN_RR_RATIO     = 1.5

# ── Signal Consensus ───────────────────────────────────────────
MIN_STRATEGY_VOTES = int(os.getenv("MIN_STRATEGY_VOTES", 1))

# ── Allowed Telegram user IDs ──────────────────────────────────
ALLOWED_USER_IDS = [
    int(x.strip())
    for x in os.getenv("ALLOWED_USER_IDS", "0").split(",")
    if x.strip().isdigit()
]

# ── Strategy Parameters ────────────────────────────────────────
TREND = {
    "ema_fast":   9,
    "ema_slow":   21,
    "adx_period": 14,
    "adx_min":    15,
}

MOMENTUM = {
    "rsi_period":     14,
    "rsi_oversold":   42,
    "rsi_overbought": 58,
    "macd_fast":      12,
    "macd_slow":      26,
    "macd_signal":    9,
}

BREAKOUT = {
    "bb_period":         20,
    "bb_std":            2.0,
    "volume_lookback":   20,
    "volume_mult":       0.8,
    "squeeze_threshold": 0.08,
}

MEAN_REVERSION = {
    "rsi_period": 14,
    "rsi_low":    42,
    "rsi_high":   58,
    "ema_period": 50,
}

# ── Stop Loss / Take Profit ────────────────────────────────────
SL_ATR_MULTIPLIER = 1.5
ATR_PERIOD        = 14
TP1_RR            = 1.5
TP2_RR            = 3.0

# ── Signal Options ─────────────────────────────────────────────
SIGNAL_COOLDOWN_BARS = 6
ENRICH_SIGNALS       = True

# ── Backtesting ────────────────────────────────────────────────
BACKTEST_SINCE        = "2023-01-01 00:00:00"
BACKTEST_INITIAL_CASH = 1000