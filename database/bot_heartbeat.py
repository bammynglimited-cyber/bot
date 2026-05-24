"""
database/bot_heartbeat.py
──────────────────────────
Bot Heartbeat Writer

Writes the bot's real live status to Supabase `bot_status` table
so the dashboard TopBar shows accurate data instead of hardcoded values.

Call `write_status()` from scheduler.py:
  - At startup
  - After every scan
  - After every trade open/close

The TopBar subscribes to this table via Supabase Realtime,
so the dashboard updates within 1-2 seconds of the bot writing.
"""

from datetime import datetime, timezone, timedelta
from colorama import Fore, init

init(autoreset=True)

# ── Calculate next scan time ───────────────────────────────────────────────────
def _next_scan_utc() -> str:
    """
    Returns the ISO timestamp of the next 4h scan.
    Scans run at 00:05, 04:05, 08:05, 12:05, 16:05, 20:05 UTC.
    """
    now        = datetime.now(timezone.utc)
    scan_hours = [0, 4, 8, 12, 16, 20]

    for h in scan_hours:
        candidate = now.replace(hour=h, minute=5, second=0, microsecond=0)
        if candidate > now:
            return candidate.isoformat()

    # Wrap to next day 00:05
    next_day = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return next_day.isoformat()


# ── Write status to Supabase ───────────────────────────────────────────────────
def write_status(
    mode:        str,
    balance:     float,
    open_trades: int,
    daily_pnl:   float,
    is_halted:   bool,
    client=None,          # pass supabase client to avoid re-import
):
    """
    Upserts bot status to Supabase.
    Call this after every scan cycle and trade event.
    """
    if client is None:
        try:
            from database.supabase_db import get_client
            client = get_client()
        except Exception as e:
            print(f"{Fore.YELLOW}[HEARTBEAT] Supabase not available: {e}")
            return

    if client is None:
        return

    try:
        payload = {
            "is_running":  not is_halted,
            "mode":        mode,
            "last_seen":   datetime.now(timezone.utc).isoformat(),
            "next_scan":   _next_scan_utc(),
            "open_trades": open_trades,
            "balance":     round(balance, 2),
            "daily_pnl":   round(daily_pnl, 2),
            "is_halted":   is_halted,
            "updated_at":  datetime.now(timezone.utc).isoformat(),
        }

        # Upsert the single status row
        # UPDATE not upsert — avoids RLS insert permission error
        client.table("bot_status").update(payload).gte("balance", -999999).execute()
        print(f"{Fore.GREEN}[HEARTBEAT] Status written — "
              f"mode={mode} balance=${balance:.2f} "
              f"open={open_trades} pnl=${daily_pnl:+.2f}")

    except Exception as e:
        print(f"{Fore.YELLOW}[HEARTBEAT] Write failed: {e}")


# ── Write offline status on shutdown ──────────────────────────────────────────
def write_offline(client=None):
    if client is None:
        try:
            from database.supabase_db import get_client
            client = get_client()
        except Exception:
            return
    if client is None:
        return

    try:
        client.table("bot_status").upsert({
            "is_running": False,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception:
        pass