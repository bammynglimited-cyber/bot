"""
signals/enricher.py
───────────────────
Signal Enricher

Adds market context to signals before they're sent to Telegram.
This gives you and your brother richer info to decide whether to take the trade.

What it adds:
  • Market regime   → TRENDING | RANGING | VOLATILE
  • Trend strength  → ADX value + interpretation
  • Volatility      → ATR% — how much the pair moves per candle
  • 24h price move  → is price already extended?
  • Higher timeframe bias → is the daily chart in agreement?
  • Signal score    → 0-100 composite quality score

None of this blocks a signal — it just adds context.
The final Telegram message becomes much more useful.
"""

from dataclasses import dataclass
from typing import Optional
import pandas as pd
import pandas_ta as ta
from colorama import Fore, init

from strategies.consensus import ConsensusSignal
from strategies.base import SignalType
from data.fetcher import fetch_ohlcv

init(autoreset=True)


@dataclass
class SignalContext:
    """Extra market context attached to a signal before sending."""
    regime:          str   = "UNKNOWN"    # TRENDING | RANGING | VOLATILE
    trend_strength:  str   = ""           # "Strong" | "Moderate" | "Weak"
    adx_value:       float = 0.0
    atr_pct:         float = 0.0          # ATR as % of price — volatility measure
    price_change_24h: float = 0.0         # % price move in last 24h
    htf_bias:        str   = "NEUTRAL"    # BULLISH | BEARISH | NEUTRAL (daily TF)
    htf_agrees:      bool  = False        # does daily TF agree with signal direction?
    score:           int   = 50           # 0-100 signal quality score
    score_label:     str   = "FAIR"       # STRONG | GOOD | FAIR | WEAK
    notes:           list  = None         # extra observations

    def __post_init__(self):
        if self.notes is None:
            self.notes = []


class SignalEnricher:
    """
    Enriches a ConsensusSignal with market context.
    Uses the same OHLCV data already fetched — no extra API calls
    unless HTF check is needed.
    """

    def enrich(
        self,
        signal: ConsensusSignal,
        df: pd.DataFrame,             # the 4h DataFrame used to generate the signal
        fetch_htf: bool = True,       # whether to fetch daily chart for HTF bias
    ) -> SignalContext:
        """
        Returns a SignalContext with all enrichment fields filled.
        Safe — never raises, returns defaults on any error.
        """
        ctx = SignalContext()
        try:
            ctx = self._compute_regime(ctx, df)
            ctx = self._compute_volatility(ctx, df)
            ctx = self._compute_price_change(ctx, df)
            if fetch_htf:
                ctx = self._compute_htf_bias(ctx, signal)
            ctx = self._compute_score(ctx, signal)
        except Exception as e:
            print(f"{Fore.YELLOW}[ENRICHER] Error enriching {signal.symbol}: {e}")
        return ctx

    # ── Regime ─────────────────────────────────────────────────────────────────

    def _compute_regime(self, ctx: SignalContext, df: pd.DataFrame) -> SignalContext:
        """
        Use ADX to classify market regime.
        ADX < 20  → RANGING  (low trend strength)
        ADX 20-35 → TRENDING (moderate)
        ADX > 35  → TRENDING STRONGLY
        Also check ATR expansion for VOLATILE classification.
        """
        adx_df = ta.adx(df["high"], df["low"], df["close"], length=14)
        adx    = adx_df["ADX_14"].iloc[-1]
        ctx.adx_value = round(adx, 1)

        if adx < 20:
            ctx.regime         = "RANGING"
            ctx.trend_strength = "Weak"
            ctx.notes.append("⚠️ Low ADX — market may be choppy")
        elif adx < 35:
            ctx.regime         = "TRENDING"
            ctx.trend_strength = "Moderate"
        else:
            ctx.regime         = "TRENDING"
            ctx.trend_strength = "Strong"
            ctx.notes.append("💪 Strong trend — momentum is clear")

        return ctx

    # ── Volatility ─────────────────────────────────────────────────────────────

    def _compute_volatility(self, ctx: SignalContext, df: pd.DataFrame) -> SignalContext:
        """
        ATR as % of close price tells us how much the pair moves per candle.
        High ATR% = wider SL needed = lower position size.
        """
        atr   = ta.atr(df["high"], df["low"], df["close"], length=14).iloc[-1]
        close = df["close"].iloc[-1]
        atr_pct = (atr / close) * 100
        ctx.atr_pct = round(atr_pct, 2)

        if atr_pct > 3.0:
            ctx.regime = "VOLATILE"
            ctx.notes.append(f"🔥 High volatility (ATR={atr_pct:.1f}%) — size down")
        elif atr_pct < 0.5:
            ctx.notes.append(f"😴 Low volatility (ATR={atr_pct:.1f}%) — breakout may be weak")

        return ctx

    # ── 24h price change ───────────────────────────────────────────────────────

    def _compute_price_change(self, ctx: SignalContext, df: pd.DataFrame) -> SignalContext:
        """
        How much has price moved in the last 24h (6 × 4h candles)?
        Extreme moves can mean the signal is late — be cautious.
        """
        if len(df) < 7:
            return ctx
        close_now  = df["close"].iloc[-1]
        close_24h  = df["close"].iloc[-7]
        chg        = ((close_now - close_24h) / close_24h) * 100
        ctx.price_change_24h = round(chg, 2)

        if abs(chg) > 8:
            ctx.notes.append(
                f"⚡ Price already moved {chg:+.1f}% in 24h — may be extended"
            )
        return ctx

    # ── Higher timeframe bias ──────────────────────────────────────────────────

    def _compute_htf_bias(self, ctx: SignalContext, signal: ConsensusSignal) -> SignalContext:
        """
        Check the daily (1d) chart to see if the higher timeframe agrees.
        A signal that aligns with the daily trend is much stronger.
        """
        try:
            daily = fetch_ohlcv(signal.symbol, timeframe="1d", limit=60)
            if daily.empty or len(daily) < 30:
                return ctx

            ema20_d = ta.ema(daily["close"], length=20).iloc[-1]
            ema50_d = ta.ema(daily["close"], length=50).iloc[-1]
            price_d = daily["close"].iloc[-1]

            bullish = price_d > ema20_d > ema50_d
            bearish = price_d < ema20_d < ema50_d

            if bullish:
                ctx.htf_bias = "BULLISH"
            elif bearish:
                ctx.htf_bias = "BEARISH"
            else:
                ctx.htf_bias = "NEUTRAL"

            is_buy = signal.signal == SignalType.BUY
            ctx.htf_agrees = (is_buy and bullish) or (not is_buy and bearish)

            if ctx.htf_agrees:
                ctx.notes.append("✅ Daily chart confirms signal direction")
            elif ctx.htf_bias != "NEUTRAL":
                ctx.notes.append(f"⚠️ Daily chart is {ctx.htf_bias} — signal goes against HTF")

        except Exception:
            pass  # HTF check is bonus — never block if it fails

        return ctx

    # ── Score ──────────────────────────────────────────────────────────────────

    def _compute_score(self, ctx: SignalContext, signal: ConsensusSignal) -> SignalContext:
        """
        Composite 0-100 quality score.
        Higher = more confluence = more confidence to take the trade.
        """
        score = 40  # base

        # Votes (max +20)
        total_votes = signal.buy_votes + signal.sell_votes
        score += min(signal.buy_votes if signal.signal == SignalType.BUY
                     else signal.sell_votes, 4) * 5

        # Confidence label
        if signal.confidence == "HIGH":   score += 15
        elif signal.confidence == "MEDIUM": score += 7

        # R:R ratio
        rr = signal.risk_reward()
        if rr >= 3.0:   score += 15
        elif rr >= 2.0: score += 10
        elif rr >= 1.5: score += 5

        # HTF alignment
        if ctx.htf_agrees:     score += 10
        elif ctx.htf_bias != "NEUTRAL": score -= 10

        # Regime
        if ctx.regime == "VOLATILE": score -= 10
        elif ctx.regime == "RANGING" and signal.confidence != "HIGH": score -= 5

        # Cap
        score = max(0, min(100, score))
        ctx.score = score

        if score >= 80:   ctx.score_label = "STRONG"
        elif score >= 65: ctx.score_label = "GOOD"
        elif score >= 45: ctx.score_label = "FAIR"
        else:             ctx.score_label = "WEAK"

        return ctx

    # ── Format for Telegram ────────────────────────────────────────────────────

    def format_context_block(self, ctx: SignalContext) -> str:
        """Returns a formatted string block to append to Telegram signal messages."""
        score_bar = _progress_bar(ctx.score)
        htf_icon  = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪"}.get(ctx.htf_bias, "⚪")
        agrees    = "✅ Agrees" if ctx.htf_agrees else ("❌ Disagrees" if ctx.htf_bias != "NEUTRAL" else "—")

        lines = [
            f"\n📊 <b>Market Context</b>",
            f"Regime:    <b>{ctx.regime}</b> (ADX {ctx.adx_value})",
            f"Trend:     <b>{ctx.trend_strength}</b>",
            f"Volatility: ATR <b>{ctx.atr_pct}%</b> per candle",
            f"24h Move:  <b>{ctx.price_change_24h:+.2f}%</b>",
            f"Daily TF:  {htf_icon} <b>{ctx.htf_bias}</b> — {agrees}",
            f"\n🎯 Signal Score: <b>{ctx.score}/100 ({ctx.score_label})</b>",
            f"{score_bar}",
        ]

        if ctx.notes:
            lines.append("\n💬 <b>Notes:</b>")
            for note in ctx.notes:
                lines.append(f"  {note}")

        return "\n".join(lines)


def _progress_bar(value: int, width: int = 10) -> str:
    filled = round(value / 100 * width)
    bar    = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {value}%"
