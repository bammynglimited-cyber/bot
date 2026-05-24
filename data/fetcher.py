"""
data/fetcher.py
───────────────
Pulls OHLCV (Open, High, Low, Close, Volume) candle data from Binance
using the ccxt library. Returns clean pandas DataFrames ready for
indicator calculation.
"""

import time
import ccxt
import pandas as pd
from datetime import datetime, timezone
from colorama import Fore, Style, init

from config.settings import (
    BINANCE_API_KEY, BINANCE_SECRET,
    TIMEFRAME, CANDLE_LIMIT, BACKTEST_SINCE, EXCHANGE_ID
)

init(autoreset=True)


# ── Exchange singleton ─────────────────────────────────────────────────────────

def get_exchange(use_auth: bool = False) -> ccxt.Exchange:
    """
    Return a configured Binance exchange instance.
    use_auth=False for public data (backtesting, signal gen).
    use_auth=True  for placing orders (live trading).
    """
    params = {
        "enableRateLimit": True,        # respect Binance rate limits
        "options": {"defaultType": "spot"},
    }
    if use_auth:
        params["apiKey"] = BINANCE_API_KEY
        params["secret"] = BINANCE_SECRET

    ExchangeClass = getattr(ccxt, EXCHANGE_ID)
    exchange = ExchangeClass(params)
    return exchange


# ── Main fetch function ────────────────────────────────────────────────────────

def fetch_ohlcv(
    symbol: str,
    timeframe: str = TIMEFRAME,
    limit: int = CANDLE_LIMIT,
    exchange: ccxt.Exchange = None,
) -> pd.DataFrame:
    """
    Fetch OHLCV candles for a symbol.

    Args:
        symbol:    e.g. "BTC/USDT"
        timeframe: e.g. "4h", "1h", "1d"
        limit:     number of candles to fetch
        exchange:  pass an existing instance to avoid re-creating

    Returns:
        DataFrame with columns: timestamp, open, high, low, close, volume
        Index is DatetimeIndex in UTC.
    """
    if exchange is None:
        exchange = get_exchange()

    try:
        raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    except ccxt.NetworkError as e:
        print(f"{Fore.RED}[FETCH] Network error for {symbol}: {e}")
        return pd.DataFrame()
    except ccxt.ExchangeError as e:
        print(f"{Fore.RED}[FETCH] Exchange error for {symbol}: {e}")
        return pd.DataFrame()

    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df = df.astype(float)

    print(f"{Fore.GREEN}[FETCH] {symbol} {timeframe} → {len(df)} candles "
          f"({df.index[0].strftime('%Y-%m-%d')} → {df.index[-1].strftime('%Y-%m-%d')})")
    return df


# ── Bulk fetch for all watchlist pairs ────────────────────────────────────────

def fetch_all(
    symbols: list[str],
    timeframe: str = TIMEFRAME,
    limit: int = CANDLE_LIMIT,
    delay: float = 0.3,         # seconds between requests (rate limit safety)
) -> dict[str, pd.DataFrame]:
    """
    Fetch OHLCV for multiple symbols, returns a dict: {symbol: DataFrame}
    """
    exchange = get_exchange()
    results = {}

    for symbol in symbols:
        df = fetch_ohlcv(symbol, timeframe=timeframe, limit=limit, exchange=exchange)
        if not df.empty:
            results[symbol] = df
        time.sleep(delay)

    print(f"\n{Fore.CYAN}[FETCH] Loaded {len(results)}/{len(symbols)} pairs successfully.")
    return results


# ── Historical data for backtesting ───────────────────────────────────────────

def fetch_historical(
    symbol: str,
    timeframe: str = TIMEFRAME,
    since: str = BACKTEST_SINCE,
    exchange: ccxt.Exchange = None,
) -> pd.DataFrame:
    """
    Fetch ALL historical OHLCV data since a start date.
    Handles pagination automatically (Binance caps at 1000 candles per request).

    Args:
        symbol:    e.g. "BTC/USDT"
        timeframe: e.g. "4h"
        since:     ISO date string e.g. "2023-01-01 00:00:00"

    Returns:
        Full historical DataFrame
    """
    if exchange is None:
        exchange = get_exchange()

    since_ms = exchange.parse8601(since)
    all_candles = []
    max_per_req  = 1000

    print(f"{Fore.YELLOW}[HISTORY] Fetching {symbol} {timeframe} since {since} ...")

    while True:
        try:
            candles = exchange.fetch_ohlcv(
                symbol,
                timeframe=timeframe,
                since=since_ms,
                limit=max_per_req,
            )
        except Exception as e:
            print(f"{Fore.RED}[HISTORY] Error: {e}")
            break

        if not candles:
            break

        all_candles.extend(candles)
        last_ts = candles[-1][0]

        # If we got fewer candles than the limit, we've reached the end
        if len(candles) < max_per_req:
            break

        since_ms = last_ts + 1
        time.sleep(0.3)

    if not all_candles:
        print(f"{Fore.RED}[HISTORY] No data returned for {symbol}")
        return pd.DataFrame()

    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df = df.astype(float)
    df = df[~df.index.duplicated(keep="last")]
    df.sort_index(inplace=True)

    print(f"{Fore.GREEN}[HISTORY] {symbol} → {len(df)} candles fetched "
          f"({df.index[0].date()} → {df.index[-1].date()})")
    return df


# ── Utility ────────────────────────────────────────────────────────────────────

def get_current_price(symbol: str, exchange: ccxt.Exchange = None) -> float:
    """Get the latest ticker price for a symbol."""
    if exchange is None:
        exchange = get_exchange()
    ticker = exchange.fetch_ticker(symbol)
    return float(ticker["last"])


if __name__ == "__main__":
    # Quick test
    df = fetch_ohlcv("BTC/USDT", timeframe="4h", limit=50)
    print(df.tail())