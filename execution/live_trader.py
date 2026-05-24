"""
execution/live_trader.py
────────────────────────
Live Trader — CEX Mode

Places REAL orders on Binance (or any ccxt exchange).
Only activated when SIGNAL_MODE = "live" in settings.

Flow:
  Signal fires → risk manager approves → place market BUY/SELL order
  → store order ID → monitor via exchange API → close at SL/TP

Safety checks before every order:
  1. Exchange connection confirmed
  2. Sufficient USDT balance
  3. Risk manager gate passed
  4. Daily loss limit not hit
  5. Max open trades not exceeded

IMPORTANT: Test with TRADE_MODE=paper first!
           Only switch to live after 2+ weeks of profitable paper trading.
"""

import time
from datetime import datetime, timezone
from typing import Optional
from colorama import Fore, init

import ccxt

from config.settings import BINANCE_API_KEY, BINANCE_SECRET, EXCHANGE_ID, TRADE_MODE
from strategies.consensus import ConsensusSignal
from strategies.base import SignalType
from risk.manager import RiskManager, OpenTrade
import tg_bot.bot as tg
import database.supabase_db as db

init(autoreset=True)


class LiveTrader:
    """
    Executes real orders on a CEX exchange.
    Only used when TRADE_MODE = 'live'.
    """

    def __init__(self, risk: RiskManager):
        self.risk     = risk
        self.exchange = self._connect()

    # ── Connection ─────────────────────────────────────────────────────────────

    def _connect(self) -> Optional[ccxt.Exchange]:
        if TRADE_MODE != "live":
            print(f"{Fore.YELLOW}[LIVE] Not in live mode — exchange not connected.")
            return None
        try:
            ExClass  = getattr(ccxt, EXCHANGE_ID)
            exchange = ExClass({
                "apiKey":          BINANCE_API_KEY,
                "secret":          BINANCE_SECRET,
                "enableRateLimit": True,
                "options":         {"defaultType": "spot"},
            })
            # Verify connection
            balance  = exchange.fetch_balance()
            usdt_bal = balance.get("USDT", {}).get("free", 0)
            self.risk.update_balance(float(usdt_bal))
            print(f"{Fore.GREEN}[LIVE] Connected to {EXCHANGE_ID.upper()} — "
                  f"Balance: ${usdt_bal:.2f} USDT")
            return exchange
        except Exception as e:
            print(f"{Fore.RED}[LIVE] Connection failed: {e}")
            tg.send_message(f"🚨 <b>Live trader connection failed:</b> <code>{e}</code>")
            return None

    def is_connected(self) -> bool:
        return self.exchange is not None

    # ── Open trade ─────────────────────────────────────────────────────────────

    def open_trade(self, signal: ConsensusSignal) -> bool:
        """
        Place a real market order on the exchange.
        Returns True if order was placed successfully.
        """
        if not self.is_connected():
            print(f"{Fore.RED}[LIVE] Not connected — cannot place order.")
            return False

        # Gate through risk manager
        trade = self.risk.open_trade(signal)
        if not trade:
            return False

        symbol  = signal.symbol.replace("/", "")  # BTC/USDT → BTCUSDT
        is_buy  = signal.signal == SignalType.BUY
        side    = "buy" if is_buy else "sell"
        qty     = trade.quantity

        try:
            print(f"{Fore.CYAN}[LIVE] Placing {side.upper()} {qty} {symbol} @ market...")

            order = self.exchange.create_order(
                symbol=signal.symbol,
                type="market",
                side=side,
                amount=qty,
            )

            order_id    = order.get("id", "")
            fill_price  = order.get("average") or order.get("price") or signal.entry
            trade.trade_id = order_id

            # Also set stop-loss order
            self._place_stop_loss(signal, trade, fill_price)

            print(f"{Fore.GREEN}[LIVE] ✅ Order placed: {order_id} | "
                  f"Fill: ${fill_price:.4f} | Qty: {qty}")

            # Notifications
            tg.send_trade_opened(
                symbol        = signal.symbol,
                direction     = trade.direction,
                entry         = fill_price,
                stop_loss     = trade.stop_loss,
                take_profit1  = trade.take_profit1,
                take_profit2  = trade.take_profit2,
                position_size = trade.position_size,
                mode          = "live",
            )
            db.log_signal(signal)
            return True

        except ccxt.InsufficientFunds:
            print(f"{Fore.RED}[LIVE] Insufficient funds for {signal.symbol}")
            self.risk.open_trades = [t for t in self.risk.open_trades
                                     if t.symbol != signal.symbol]
            tg.send_message(f"⚠️ Insufficient funds for {signal.symbol}")
            return False

        except ccxt.NetworkError as e:
            print(f"{Fore.RED}[LIVE] Network error: {e}")
            return False

        except Exception as e:
            print(f"{Fore.RED}[LIVE] Order failed: {e}")
            tg.send_message(f"🚨 <b>Order failed ({signal.symbol}):</b> <code>{e}</code>")
            return False

    # ── Close trade ────────────────────────────────────────────────────────────

    def close_trade(self, trade: OpenTrade, reason: str) -> bool:
        """
        Place a market order to close an open position.
        """
        if not self.is_connected():
            return False

        is_buy = trade.direction == "BUY"
        side   = "sell" if is_buy else "buy"   # close direction is opposite

        try:
            order = self.exchange.create_order(
                symbol=trade.symbol,
                type="market",
                side=side,
                amount=trade.quantity,
            )

            fill_price = order.get("average") or order.get("price") or trade.entry
            pnl_usd    = self.risk.close_trade(trade.symbol, fill_price, reason)

            print(f"{Fore.GREEN}[LIVE] Position closed: {trade.symbol} @ {fill_price} | {reason}")

            is_buy      = trade.direction == "BUY"
            pnl_pct     = ((fill_price - trade.entry) / trade.entry * 100) if is_buy \
                          else ((trade.entry - fill_price) / trade.entry * 100)

            tg.send_trade_closed(
                symbol      = trade.symbol,
                direction   = trade.direction,
                entry       = trade.entry,
                exit_price  = fill_price,
                pnl_usd     = pnl_usd or 0,
                pnl_pct     = pnl_pct,
                reason      = reason,
                balance     = self.risk.balance,
                mode        = "live",
            )
            db.log_trade_closed(
                symbol        = trade.symbol,
                direction     = trade.direction,
                entry         = trade.entry,
                exit_price    = fill_price,
                stop_loss     = trade.stop_loss,
                take_profit1  = trade.take_profit1,
                take_profit2  = trade.take_profit2,
                position_size = trade.position_size,
                pnl_usd       = pnl_usd or 0,
                pnl_pct       = pnl_pct,
                exit_reason   = reason,
                strategy      = trade.strategy,
                opened_at     = trade.opened_at,
                mode          = "live",
            )
            return True

        except Exception as e:
            print(f"{Fore.RED}[LIVE] Close order failed: {e}")
            tg.send_message(f"🚨 <b>Failed to close {trade.symbol}:</b> <code>{e}</code>\n"
                            f"Close it manually on your exchange!")
            return False

    # ── Monitor positions ──────────────────────────────────────────────────────

    def monitor_open_trades(self):
        """Check live prices and close any trade that hit SL/TP."""
        if not self.risk.open_trades:
            return

        print(f"\n{Fore.CYAN}[LIVE] Monitoring {len(self.risk.open_trades)} live position(s)...")

        prices = {}
        for trade in self.risk.open_trades:
            try:
                ticker = self.exchange.fetch_ticker(trade.symbol)
                prices[trade.symbol] = float(ticker["last"])
                time.sleep(0.2)
            except Exception as e:
                print(f"{Fore.RED}[LIVE] Price fetch error ({trade.symbol}): {e}")

        to_close = self.risk.check_open_trades(prices)
        for item in to_close:
            self.close_trade(item["trade"], item["reason"])

        # Log unrealized P&L
        for trade in self.risk.open_trades:
            price = prices.get(trade.symbol)
            if not price:
                continue
            is_buy   = trade.direction == "BUY"
            unreal   = ((price - trade.entry) / trade.entry * 100) if is_buy \
                       else ((trade.entry - price) / trade.entry * 100)
            color    = Fore.GREEN if unreal >= 0 else Fore.RED
            print(f"  {color}[LIVE] {trade.direction} {trade.symbol}: "
                  f"entry={trade.entry} now={price:.4f} "
                  f"unrealized={unreal:+.2f}%")

    # ── Sync balance ───────────────────────────────────────────────────────────

    def sync_balance(self):
        """Pull real balance from exchange and update risk manager."""
        if not self.is_connected():
            return
        try:
            balance      = self.exchange.fetch_balance()
            usdt_balance = float(balance.get("USDT", {}).get("free", 0))
            self.risk.update_balance(usdt_balance)
            print(f"{Fore.CYAN}[LIVE] Balance synced: ${usdt_balance:.2f} USDT")
        except Exception as e:
            print(f"{Fore.YELLOW}[LIVE] Balance sync failed: {e}")

    # ── Stop loss order ────────────────────────────────────────────────────────

    def _place_stop_loss(self, signal: ConsensusSignal, trade: OpenTrade, fill_price: float):
        """Place a stop-loss limit order on the exchange after entry."""
        try:
            is_buy = signal.signal == SignalType.BUY
            side   = "sell" if is_buy else "buy"
            self.exchange.create_order(
                symbol     = signal.symbol,
                type       = "stop_loss_limit",
                side       = side,
                amount     = trade.quantity,
                price      = trade.stop_loss * (0.999 if is_buy else 1.001),
                params     = {"stopPrice": trade.stop_loss},
            )
            print(f"{Fore.CYAN}[LIVE] Stop-loss order placed @ {trade.stop_loss}")
        except Exception as e:
            # Stop loss order failed — log warning but don't abort
            # The hourly monitor will catch SL hits via price check
            print(f"{Fore.YELLOW}[LIVE] SL order failed (will monitor manually): {e}")