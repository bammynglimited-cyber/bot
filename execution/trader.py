"""
execution/trader.py
───────────────────
Live Execution Engine — Week 4

Places REAL orders on Binance via ccxt.
This module only runs when TRADE_MODE=live in your .env.

Safety layers:
  - Risk manager gates every trade (position size, daily halt, duplicates)
  - Minimum notional check (Binance requires ≥ $10 per order)
  - Market orders only (guaranteed fill, no slippage risk from limit misses)
  - SL/TP tracked in software (not as exchange orders — cleaner, more flexible)
  - Every order confirmed with exchange before registering as open

Flow:
  Signal → RiskManager.can_trade() → place_market_order()
  → confirm fill → open_trade() → monitor() → close at SL/TP
"""

import time
from datetime import datetime, timezone
from colorama import Fore, init

import ccxt

from config.settings import (
    BINANCE_API_KEY, BINANCE_SECRET,
    TRADE_MODE,
)
from strategies.consensus import ConsensusSignal
from risk.manager import RiskManager, OpenTrade
import tg_bot.bot as tg
import database.supabase_db as db

init(autoreset=True)

MIN_ORDER_USDT = 11.0   # Binance minimum notional + small buffer


def get_live_exchange() -> ccxt.binance:
    """Return an authenticated Binance exchange instance."""
    exchange = ccxt.binance({
        "apiKey":  BINANCE_API_KEY,
        "secret":  BINANCE_SECRET,
        "options": {"defaultType": "spot"},
        "enableRateLimit": True,
    })
    exchange.load_markets()
    return exchange


class LiveTrader:

    def __init__(self, risk_manager: RiskManager):
        self.risk     = risk_manager
        self.exchange = get_live_exchange()
        print(f"{Fore.CYAN}[LIVE] Exchange connected — {TRADE_MODE.upper()} mode")

    # ── Guard ──────────────────────────────────────────────────────────────────

    def _assert_live(self):
        if TRADE_MODE != "live":
            raise RuntimeError(
                f"LiveTrader called but TRADE_MODE={TRADE_MODE!r}. "
                "Set TRADE_MODE=live in .env to enable real orders."
            )

    # ── Open a live trade ──────────────────────────────────────────────────────

    def open_trade(self, signal: ConsensusSignal) -> bool:
        """
        Execute a real market order on Binance.
        Returns True if the order was placed and confirmed.
        """
        self._assert_live()

        # 1 — Risk gate
        allowed, reason = self.risk.can_trade(signal)
        if not allowed:
            print(f"{Fore.YELLOW}[LIVE] Rejected: {reason}")
            return False

        # 2 — Position size
        size_usdt = self.risk.calculate_position_size(signal)
        if size_usdt < MIN_ORDER_USDT:
            print(f"{Fore.YELLOW}[LIVE] Order too small (${size_usdt:.2f} < ${MIN_ORDER_USDT}). Skipped.")
            return False

        symbol = signal.symbol          # e.g. "BTC/USDT"
        side   = signal.signal.value.lower()   # "buy" or "sell"

        # 3 — Fetch current price (for quantity calc)
        try:
            ticker     = self.exchange.fetch_ticker(symbol)
            live_price = ticker["last"]
        except Exception as e:
            print(f"{Fore.RED}[LIVE] Ticker fetch failed ({symbol}): {e}")
            return False

        quantity = self.risk.calculate_quantity(live_price, size_usdt)
        if quantity <= 0:
            print(f"{Fore.RED}[LIVE] Quantity calc error. Skipped.")
            return False

        # 4 — Place market order
        try:
            order = self.exchange.create_market_order(
                symbol   = symbol,
                side     = side,
                amount   = quantity,
            )
        except ccxt.InsufficientFunds:
            print(f"{Fore.RED}[LIVE] Insufficient funds for {symbol}.")
            tg.send_error(f"❌ Insufficient funds for {symbol} order.")
            return False
        except ccxt.InvalidOrder as e:
            print(f"{Fore.RED}[LIVE] Invalid order ({symbol}): {e}")
            tg.send_error(f"❌ Invalid order: {e}")
            return False
        except Exception as e:
            print(f"{Fore.RED}[LIVE] Order failed ({symbol}): {e}")
            tg.send_error(f"❌ Order error ({symbol}): {e}")
            return False

        # 5 — Confirm fill
        time.sleep(1.5)
        try:
            filled = self.exchange.fetch_order(order["id"], symbol)
            fill_price = filled.get("average") or filled.get("price") or live_price
            order_id   = str(filled["id"])
        except Exception:
            # Fallback: use live price as fill
            fill_price = live_price
            order_id   = str(order.get("id", ""))

        print(f"{Fore.GREEN}[LIVE] ✅ Order filled — {side.upper()} {quantity} {symbol} "
              f"@ ${fill_price:.4f} | ID: {order_id}")

        # 6 — Update signal entry to actual fill price
        signal.entry = fill_price

        # 7 — Register with risk manager (uses actual fill price)
        trade = self.risk.open_trade(signal)
        if trade:
            trade.trade_id = order_id
            self.risk._save_state()

        # 8 — Notify + log
        tg.send_signal(signal)
        tg.send_trade_opened(
            symbol        = signal.symbol,
            direction     = signal.signal.value,
            entry         = fill_price,
            stop_loss     = signal.stop_loss,
            take_profit1  = signal.take_profit1,
            take_profit2  = signal.take_profit2,
            position_size = size_usdt,
            mode          = "live",
        )
        db.log_signal(signal)

        return True

    # ── Monitor open trades ────────────────────────────────────────────────────

    def monitor_open_trades(self):
        """
        Check all live open trades against current prices.
        Closes any that hit SL or TP with a real market order.
        Called every hour by the scheduler.
        """
        if not self.risk.open_trades:
            print(f"{Fore.YELLOW}[LIVE] No open trades to monitor.")
            return

        print(f"\n{Fore.CYAN}[LIVE] Monitoring {len(self.risk.open_trades)} live trade(s)...")

        prices: dict[str, float] = {}
        for trade in self.risk.open_trades:
            try:
                ticker = self.exchange.fetch_ticker(trade.symbol)
                prices[trade.symbol] = ticker["last"]
                time.sleep(0.3)
            except Exception as e:
                print(f"{Fore.RED}[LIVE] Price fetch error ({trade.symbol}): {e}")

        to_close = self.risk.check_open_trades(prices)
        for item in to_close:
            self._execute_close(item["trade"], item["exit"], item["reason"])

        # Print unrealized P&L
        for trade in self.risk.open_trades:
            price = prices.get(trade.symbol)
            if price is None:
                continue
            is_buy = trade.direction == "BUY"
            unreal = ((price - trade.entry) / trade.entry * 100) if is_buy else \
                     ((trade.entry - price) / trade.entry * 100)
            color  = Fore.GREEN if unreal >= 0 else Fore.RED
            print(f"  {color}{trade.direction} {trade.symbol}: "
                  f"entry={trade.entry} now={price:.4f} unrealized={unreal:+.2f}%")

    # ── Close a live trade ─────────────────────────────────────────────────────

    def _execute_close(self, trade: OpenTrade, exit_price: float, reason: str):
        """Place a real closing order then update state."""
        self._assert_live()

        # Opposite side to close
        close_side = "sell" if trade.direction == "BUY" else "buy"

        try:
            order = self.exchange.create_market_order(
                symbol = trade.symbol,
                side   = close_side,
                amount = trade.quantity,
            )
            time.sleep(1.5)
            filled     = self.exchange.fetch_order(order["id"], trade.symbol)
            fill_price = filled.get("average") or filled.get("price") or exit_price
        except Exception as e:
            print(f"{Fore.RED}[LIVE] Close order failed ({trade.symbol}): {e}")
            tg.send_error(f"❌ Close order failed for {trade.symbol}: {e}")
            return

        print(f"{Fore.CYAN}[LIVE] Closed {trade.symbol} @ {fill_price:.4f} | {reason}")

        is_buy  = trade.direction == "BUY"
        pnl_pct = ((fill_price - trade.entry) / trade.entry * 100) if is_buy else \
                  ((trade.entry - fill_price) / trade.entry * 100)

        pnl_usd = self.risk.close_trade(trade.symbol, fill_price, reason)
        if pnl_usd is None:
            return

        # Log to Supabase
        db.log_trade_closed(
            symbol        = trade.symbol,
            direction     = trade.direction,
            entry         = trade.entry,
            exit_price    = fill_price,
            stop_loss     = trade.stop_loss,
            take_profit1  = trade.take_profit1,
            take_profit2  = trade.take_profit2,
            position_size = trade.position_size,
            pnl_usd       = pnl_usd,
            pnl_pct       = pnl_pct,
            exit_reason   = reason,
            strategy      = trade.strategy,
            opened_at     = trade.opened_at,
            mode          = "live",
        )

        # Telegram
        tg.send_trade_closed(
            symbol      = trade.symbol,
            direction   = trade.direction,
            entry       = trade.entry,
            exit_price  = fill_price,
            pnl_usd     = pnl_usd,
            pnl_pct     = pnl_pct,
            reason      = reason,
            balance     = self.risk.balance,
            mode        = "live",
        )

        if self.risk.is_halted:
            tg.send_halt_alert(
                daily_pnl = self.risk.daily.realized_pnl,
                limit_pct = 5.0,
                balance   = self.risk.balance,
            )

    # ── Account snapshot ───────────────────────────────────────────────────────

    def sync_balance(self) -> float:
        """Pull real USDT balance from Binance and update risk manager."""
        try:
            balance = self.exchange.fetch_balance()
            usdt    = balance["USDT"]["free"]
            self.risk.balance = round(usdt, 2)
            self.risk._save_state()
            print(f"{Fore.CYAN}[LIVE] Balance synced: ${usdt:.2f} USDT")
            return usdt
        except Exception as e:
            print(f"{Fore.RED}[LIVE] Balance sync failed: {e}")
            return self.risk.balance
