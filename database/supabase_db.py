"""
database/supabase_db.py  [Merged - Final]
──────────────────────────────────────────
Single unified Supabase layer.
Works with both old callers (get_client, log_signal with ConsensusSignal)
and new callers (log_trade_opened, _uid sanitiser).
"""

from __future__ import annotations
import traceback
from datetime import datetime, date as _date_cls, timezone
from colorama import Fore, init
from config.settings import SUPABASE_URL, SUPABASE_KEY

init(autoreset=True)

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        from supabase import create_client
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
        print(f"{Fore.CYAN}[DB] Supabase connected.")
        return _client
    except Exception as e:
        print(f"{Fore.YELLOW}[DB] Supabase not available: {e}")
        return None


# Public alias — used by bot_heartbeat.py and any other module
def get_client():
    return _get_client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid(user_id) -> str | None:
    """Sanitise user_id — never pass '0', '', or 'None' as a UUID."""
    if not user_id or str(user_id) in ("0", "", "None", "none"):
        return None
    return str(user_id)


def _safe_insert(table: str, data: dict) -> bool:
    client = _get_client()
    if not client:
        return False
    try:
        client.table(table).insert(data).execute()
        return True
    except Exception as e:
        print(f"{Fore.RED}[DB] Insert error ({table}): {e}")
        return False


# ── Watchlist ──────────────────────────────────────────────────────────────────

def get_global_watchlist() -> list[str]:
    """Pull active watchlist from Supabase. Falls back to [] so scheduler uses settings.py."""
    client = _get_client()
    if not client:
        return []
    try:
        # Try new table name first
        result = client.table("watchlist").select("symbol").eq("is_active", True).execute()
        symbols = [row["symbol"] for row in (result.data or [])]
        if symbols:
            return list(dict.fromkeys(symbols))   # deduplicate, preserve order
        # Fall back to old table name
        result2 = client.table("watchlists").select("symbol").eq("active", True).execute()
        return [row["symbol"] for row in (result2.data or [])]
    except Exception:
        return []


def get_all_watchlists() -> dict[str, list[str]]:
    client = _get_client()
    if not client:
        return {}
    try:
        result = client.table("watchlist").select("user_id,symbol").eq("is_active", True).execute()
        out: dict[str, list[str]] = {}
        for row in result.data or []:
            out.setdefault(row["user_id"], []).append(row["symbol"])
        return out
    except Exception:
        return {}


# ── Commands ───────────────────────────────────────────────────────────────────

def get_pending_commands() -> list[dict]:
    client = _get_client()
    if not client:
        return []
    try:
        # Try new commands table
        result = client.table("commands").select("*").eq("status", "pending").execute()
        return result.data or []
    except Exception:
        try:
            # Fall back to old bot_commands table
            result = client.table("bot_commands").select("*").eq("executed", False).execute()
            return result.data or []
        except Exception:
            return []


def mark_command_done(cmd_id, status: str = "done"):
    client = _get_client()
    if not client:
        return
    try:
        client.table("commands").update({
            "status": status, "executed_at": _now()
        }).eq("id", cmd_id).execute()
    except Exception:
        try:
            client.table("bot_commands").update({
                "executed": True, "executed_at": _now(), "status": status
            }).eq("id", cmd_id).execute()
        except Exception:
            pass


# ── Signal logging ─────────────────────────────────────────────────────────────

def log_signal(signal, user_id=None) -> bool:
    """
    Accepts both ConsensusSignal (new) and old-style signal objects.
    Works with either .vote_count or .buy_votes/.sell_votes attributes.
    """
    client = _get_client()
    if not client:
        return False
    try:
        # Handle both old and new signal shapes
        strategies = ", ".join(s.strategy for s in signal.contributors) if hasattr(signal, 'contributors') else ""
        votes      = getattr(signal, 'vote_count', None) or max(
            getattr(signal, 'buy_votes', 0), getattr(signal, 'sell_votes', 0)
        )
        rr = round(signal.risk_reward(), 2) if callable(getattr(signal, 'risk_reward', None)) else 0

        data = {
            "timestamp":    _now(),
            "symbol":       signal.symbol,
            "direction":    signal.signal.value,
            "entry":        signal.entry,
            "stop_loss":    signal.stop_loss,
            "take_profit1": signal.take_profit1,
            "take_profit2": signal.take_profit2,
            "votes":        votes,
            "strategies":   strategies,
            "risk_reward":  rr,
            "confidence":   signal.confidence,
            "user_id":      _uid(user_id),
        }
        client.table("signals").insert(data).execute()
        print(f"{Fore.GREEN}[DB] Signal logged → {signal.symbol} {signal.signal.value}")
        return True
    except Exception as e:
        print(f"{Fore.RED}[DB] Insert error (signals): {e}")
        return False


# ── Trade logging ──────────────────────────────────────────────────────────────

def log_trade_opened(
    symbol: str, direction: str, entry: float,
    stop_loss: float, take_profit1: float, take_profit2: float,
    position_size: float, quantity: float,
    strategy: str, mode: str, trade_id: str = "",
    user_id=None,
) -> bool:
    client = _get_client()
    if not client:
        return False
    try:
        client.table("trades").insert({
            "opened_at":     _now(),
            "symbol":        symbol,
            "direction":     direction,
            "entry":         entry,
            "stop_loss":     stop_loss,
            "take_profit1":  take_profit1,
            "take_profit2":  take_profit2,
            "position_size": position_size,
            "quantity":      quantity,
            "strategy":      strategy,
            "mode":          mode,
            "trade_id":      trade_id,
            "status":        "open",
            "user_id":       _uid(user_id),
        }).execute()
        print(f"{Fore.CYAN}[DB] Trade opened logged: {symbol}")
        return True
    except Exception as e:
        print(f"{Fore.RED}[DB] Trade open log failed: {e}")
        return False


def log_trade_closed(
    symbol: str, direction: str,
    entry: float, exit_price: float,
    stop_loss: float, take_profit1: float, take_profit2: float,
    position_size: float, pnl_usd: float, pnl_pct: float,
    exit_reason: str, strategy: str, opened_at: str, mode: str = "paper",
    user_id=None,
) -> bool:
    client = _get_client()
    if not client:
        return False
    try:
        data = {
            "opened_at":     opened_at,
            "closed_at":     _now(),
            "symbol":        symbol,
            "direction":     direction,
            "entry":         entry,
            "exit_price":    exit_price,
            "stop_loss":     stop_loss,
            "take_profit1":  take_profit1,
            "take_profit2":  take_profit2,
            "position_size": position_size,
            "pnl_usd":       round(pnl_usd, 2),
            "pnl_pct":       round(pnl_pct, 4),
            "exit_reason":   exit_reason,
            "strategy":      strategy,
            "mode":          mode,
            "status":        "closed",
            "user_id":       _uid(user_id),
        }
        # Try to update existing open row first
        result = (
            client.table("trades").update(data)
            .eq("symbol", symbol).eq("opened_at", opened_at).eq("status", "open")
            .execute()
        )
        if not result.data:
            client.table("trades").insert(data).execute()

        color = Fore.GREEN if pnl_usd >= 0 else Fore.RED
        print(f"{color}[DB] Trade closed: {symbol} {exit_reason} ${pnl_usd:+.2f}")
        return True
    except Exception as e:
        print(f"{Fore.RED}[DB] Trade close log failed: {e}")
        return False


# ── Heartbeat ──────────────────────────────────────────────────────────────────

def log_heartbeat(mode: str, open_trades: int, balance: float, status: str = "ok"):
    """
    Write heartbeat. Tries bot_health (new) then bot_status (old).
    """
    client = _get_client()
    if not client:
        return
    try:
        # New table: bot_health (INSERT each ping)
        client.table("bot_health").insert({
            "timestamp":   _now(),
            "mode":        mode,
            "open_trades": open_trades,
            "balance":     round(balance, 2),
            "status":      status,
        }).execute()
        return
    except Exception:
        pass
    try:
        # Old table: bot_status (UPDATE single row)
        client.table("bot_status").update({
            "is_running":  status != "error",
            "mode":        mode,
            "last_seen":   _now(),
            "open_trades": open_trades,
            "balance":     round(balance, 2),
            "is_halted":   status == "halted",
            "updated_at":  _now(),
        }).gte("balance", -999999).execute()
    except Exception as e:
        print(f"{Fore.YELLOW}[DB] Heartbeat error: {e}")


# ── Daily stats ────────────────────────────────────────────────────────────────

def log_daily_stats(
    date=None, balance=0, daily_pnl=0,
    trades=0, wins=0, losses=0,
    open_trades=0, mode="paper", user_id=None,
    # legacy kwargs
    total_trades=None, **kwargs
) -> bool:
    client = _get_client()
    if not client:
        return False
    try:
        today        = date or str(_date_cls.today())
        trade_count  = trades or total_trades or 0
        data = {
            "date":        today,
            "balance":     round(balance, 2),
            "daily_pnl":   round(daily_pnl, 2),
            "trades":      trade_count,
            "wins":        wins,
            "losses":      losses,
            "win_rate":    round(wins / trade_count * 100, 1) if trade_count > 0 else 0,
            "open_trades": open_trades,
            "mode":        mode,
            "logged_at":   _now(),
            "user_id":     _uid(user_id),
        }
        try:
            client.table("daily_stats").upsert(data, on_conflict="date,mode").execute()
        except Exception:
            client.table("daily_stats").insert(data).execute()
        print(f"{Fore.CYAN}[DB] Daily stats logged for {today}")
        return True
    except Exception as e:
        print(f"{Fore.RED}[DB] Daily stats error: {e}")
        return False


# ── Fetch helpers ──────────────────────────────────────────────────────────────

def get_recent_trades(limit=50):
    client = _get_client()
    if not client: return []
    try:
        return client.table("trades").select("*").eq("status","closed").order("closed_at",desc=True).limit(limit).execute().data or []
    except: return []

def get_open_trades():
    client = _get_client()
    if not client: return []
    try:
        return client.table("trades").select("*").eq("status","open").execute().data or []
    except: return []

def get_daily_stats(days=30):
    client = _get_client()
    if not client: return []
    try:
        return client.table("daily_stats").select("*").order("date",desc=True).limit(days).execute().data or []
    except: return []

def get_user_profiles():
    client = _get_client()
    if not client: return []
    try:
        return client.table("user_profiles").select("*").execute().data or []
    except: return []