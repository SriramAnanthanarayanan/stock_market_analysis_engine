"""Normalized signal evidence (spec 8.2 / 12): one shape for every scanner.

Each scanner keeps its own detection logic and configuration; after it ran, an
adapter here describes what it found as evidence rows (``signal_evidence``).
Adapters only re-label results that were already computed in the scan - they
never detect anything themselves, so a signal is never detected twice.

Fields: evidence ID (stable for the same signal, source config and candle),
symbol, timeframe, source scanner, signal type, category, kind (event / state /
formation), direction, signal candle time and age, detector status, lifecycle
(active / invalidated / expired at the scan), valid-until + expiry rule,
quality and the source's primary strength measure, the underlying detection /
event ID, config version / hash, metrics and an explanation.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

# Conventional bias of the price-action patterns (Bulkowski's expected breakout
# direction). Channels, ranges and symmetrical triangles have no bias: their
# direction comes from a confirmed break.
BULLISH_PATTERNS = frozenset({"ascending_triangle", "falling_wedge", "bullish_flag", "bullish_pennant", "double_bottom", "inverse_head_shoulders"})
BEARISH_PATTERNS = frozenset({"descending_triangle", "rising_wedge", "bearish_flag", "bearish_pennant", "double_top", "head_shoulders"})

CATEGORIES = ("trend", "setup", "trigger", "volume_candle", "relative_strength")


def evidence_id(source: str, symbol: str, timeframe: str, signal_type: str, signal_t: int | None, underlying_id: str | None, config_hash: str) -> str:
    key = f"{source}|{symbol}|{timeframe}|{signal_type}|{signal_t}|{underlying_id}|{config_hash}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def make(
    *,
    symbol: str,
    timeframe: str,
    source: str,
    signal_type: str,
    category: str,
    kind: str,
    direction: str,
    signal_time: str | None,
    signal_t: int | None,
    age_bars: int | None,
    status: str,
    lifecycle: str,
    config_version: str,
    config_hash: str,
    metrics: dict[str, Any],
    quality: float | None = None,
    strength: float | None = None,
    underlying_id: str | None = None,
    valid_until: str | None = None,
    expiry_rule: str | None = None,
    explanation: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id(source, symbol, timeframe, signal_type, signal_t, underlying_id, config_hash),
        "symbol": symbol,
        "timeframe": timeframe,
        "source": source,
        "signal_type": signal_type,
        "category": category,
        "kind": kind,
        "direction": direction,
        "signal_time": signal_time,
        "signal_t": signal_t,
        "age_bars": age_bars,
        "status": status,
        "lifecycle": lifecycle,
        "valid_until": valid_until,
        "expiry_rule": expiry_rule,
        "quality": quality,
        "strength": strength,
        "underlying_id": underlying_id,
        "metrics": metrics,
        "explanation": explanation or [],
        "config_version": config_version,
        "config_hash": config_hash,
    }


ValidUntil = Callable[[int, int], str | None]  # (signal candle index, fresh sessions) -> ISO time


class Publisher:
    """Builds the evidence rows of one symbol in one scan."""

    def __init__(self, symbol: str, timeframe: str, n_bars: int, valid_until: ValidUntil, freshness: dict[str, int]):
        self.symbol = symbol
        self.timeframe = timeframe
        self.n = n_bars
        self.valid_until = valid_until
        self.freshness = freshness
        self.rows: list[dict[str, Any]] = []

    def _event_expiry(self, key: str, index: int) -> tuple[str | None, str]:
        k = int(self.freshness.get(key, 0))
        return self.valid_until(index, k + 1), f"event: fresh for {k} completed {self.timeframe} candles after the signal candle (ConfluenceConfig.freshness_sessions['{key}'])"

    def add(self, **kw: Any) -> None:
        self.rows.append(make(symbol=self.symbol, timeframe=self.timeframe, **kw))

    # ------------------------------------------------------------ new scanners
    def fresh_breakouts(self, events: list[dict[str, Any]], touches: list[dict[str, Any]], version: str, chash: str) -> None:
        for e in events:
            vu, rule = self._event_expiry("fresh_breakout", e["index"])
            failed = e["episode_state"] == "reset" or e["confirm_2"] == "failed"
            self.add(
                source="fresh_breakout", signal_type=e["kind"], category="trigger", kind="event", direction=e["direction"],
                signal_time=e["time"], signal_t=e["t"], age_bars=e["age_bars"], status="confirmed",
                lifecycle="invalidated" if failed else "active", valid_until=vu, expiry_rule=rule,
                strength=e["distance_atr"], underlying_id=e["event_id"], metrics=e, config_version=version, config_hash=chash,
                explanation=[
                    f"Close {e['close']} beyond the previous {e['lookback']}-session {'high' if e['direction'] == 'bullish' else 'low'} {e['level']} (+/- {e['buffer']} buffer) - a fresh crossing ({e['trigger']}).",
                    f"First breakout in the last {e['first_lookback_sessions']} sessions: {e['first_in_lookback']}; episode {e['episode_state']}.",
                ],
            )
        for t in touches:
            self.add(
                source="fresh_breakout", signal_type=t["kind"], category="context", kind="event", direction=t["direction"],
                signal_time=t["time"], signal_t=t["t"], age_bars=t["age_bars"], status=t["close_status"], lifecycle="active",
                strength=None, underlying_id=None, metrics=t, config_version=version, config_hash=chash,
                expiry_rule="provisional touch: shown for the candle only",
                explanation=[f"Intraday touch of the level {t['level']} (provisional); the close was {t['close_status'].replace('_', ' ')}."],
            )

    def week52(self, events: list[dict[str, Any]], touches: list[dict[str, Any]], state: dict[str, Any], version: str, chash: str) -> None:
        for e in events:
            vu, rule = self._event_expiry("week52", e["index"])
            failed = e["episode_state"] == "reset" or e["confirm_2"] == "failed"
            self.add(
                source="week52", signal_type=e["kind"], category="trigger", kind="event", direction=e["direction"],
                signal_time=e["time"], signal_t=e["t"], age_bars=e["age_bars"], status="confirmed",
                lifecycle="invalidated" if failed else "active", valid_until=vu, expiry_rule=rule,
                strength=e["distance_atr"], underlying_id=e["event_id"], metrics=e, config_version=version, config_hash=chash,
                explanation=[f"Close {e['close']} beyond the previous 52-week {'high' if e['direction'] == 'bullish' else 'low'} {e['level']} ({e['reference_start'][:10]} to {e['reference_end'][:10]}), fresh crossing ({e['trigger']})."],
            )
        for t in touches:
            self.add(
                source="week52", signal_type=t["kind"], category="context", kind="event", direction=t["direction"],
                signal_time=t["time"], signal_t=t["t"], age_bars=t["age_bars"], status=t["close_status"], lifecycle="active",
                metrics=t, config_version=version, config_hash=chash, expiry_rule="provisional touch: shown for the candle only",
                explanation=[f"Intraday touch of the 52-week level {t['level']} (provisional); the close was {t['close_status'].replace('_', ' ')}."],
            )
        if state.get("time"):
            self.add(
                source="week52", signal_type="week52_state", category="context", kind="state", direction="neutral",
                signal_time=state["time"], signal_t=state["t"], age_bars=0, status=state["status"], lifecycle="active",
                metrics=state, config_version=version, config_hash=chash, expiry_rule="state: valid until the next scan",
                explanation=[state.get("note") or f"52-week high {state.get('high_52w')} / low {state.get('low_52w')}; close {state.get('close')}."],
            )

    def volume_shocks(self, shocks: list[dict[str, Any]], version: str, chash: str) -> None:
        for v in shocks:
            vu, rule = self._event_expiry("volume_shock", v["index"])
            self.add(
                source="volume_shock", signal_type=v["kind"], category="volume_candle", kind="event", direction=v["direction"],
                signal_time=v["time"], signal_t=v["t"], age_bars=v["age_bars"], status=v["status"], lifecycle="active",
                valid_until=vu, expiry_rule=rule, strength=v["rvol"], metrics=v, config_version=version, config_hash=chash,
                explanation=[f"Volume {v['volume']:,.0f} = {v['rvol']}x the mean of the {v['reference_bars']} reference candles ({v['reference']}); {v['direction']} candle."],
            )

    # ------------------------------------------------------------ existing scanners (adapters)
    def marubozu(self, signals: list[dict[str, Any]], version: str, chash: str) -> None:
        for s in signals:
            if s.get("category") != "candlestick":
                continue
            vu, rule = self._event_expiry("marubozu", s["index"])
            self.add(
                source="marubozu", signal_type=s["kind"], category="volume_candle", kind="event", direction=s["direction"],
                signal_time=s["time"], signal_t=s["t"], age_bars=s["age_bars"], status="confirmed", lifecycle="active",
                valid_until=vu, expiry_rule=rule, strength=s.get("range_atr"), metrics=s, config_version=version, config_hash=chash,
                explanation=[f"{s['kind'].replace('_', ' ').title()}: body {s['body_pct']}% of the range, wicks {s['upper_wick_pct']}% / {s['lower_wick_pct']}%."],
            )

    def divergences(self, signals: list[dict[str, Any]], version: str, chash: str) -> None:
        for s in signals:
            if s.get("category") != "rsi_divergence":
                continue
            self.add(
                source="rsi_divergence", signal_type=f"rsi_divergence_{s['direction']}", category="context", kind="event", direction=s["direction"],
                signal_time=s["time"], signal_t=s["t"], age_bars=s["age_bars"], status=s["status"],
                lifecycle="invalidated" if s["status"] == "invalidated" else "active", metrics=s, config_version=version, config_hash=chash,
                expiry_rule="informational: listed, not scored", explanation=[f"Regular RSI divergence ({s['status']})."],
            )

    def ema_alignment(self, snapshot: dict[str, Any], status: str, version: str, chash: str) -> None:
        al = snapshot.get("alignment") or "not_available"
        direction = "bullish" if al.startswith("bullish") else "bearish" if al.startswith("bearish") else "neutral"
        self.add(
            source="ema_alignment", signal_type=f"ema_{al}", category="trend", kind="state", direction=direction,
            signal_time=snapshot.get("last_candle_time"), signal_t=None, age_bars=0, status=status, lifecycle="active",
            metrics={k: snapshot.get(k) for k in ("alignment", "ema", "ema_distance_pct", "price_vs_ema", "close", "ema_missing", "rsi", "atr_pct")},
            config_version=version, config_hash=chash, expiry_rule="state: valid until the next scan",
            explanation=[f"EMA alignment {al.replace('_', ' ')}; price vs EMA 20/50/200: {snapshot.get('price_vs_ema')}."],
        )

    def patterns(self, dets: list[Any], times, version: str, chash: str) -> None:
        for d in dets:
            if d.status == "developing":
                continue
            slug = d.pattern
            brk = next((e for e in reversed(d.events) if e.get("event") in ("broke_above", "broke_below")), None)
            if d.status in ("broke_above", "broke_below"):
                direction = "bullish" if d.status == "broke_above" else "bearish"
            elif slug in BULLISH_PATTERNS:
                direction = "bullish"
            elif slug in BEARISH_PATTERNS:
                direction = "bearish"
            else:
                direction = "neutral"
            metrics = {
                "pattern": slug, "pattern_name": d.pattern_name, "family": d.family, "category": d.category,
                "status": d.status, "status_reason": d.status_reason, "quality": d.quality, "quality_label": d.quality_label,
                "formation_start": d.formation_start, "formation_end": d.formation_end, "formation_length": d.formation_length,
                "track_id": d.extra.get("track_id"), "bias": "bullish" if slug in BULLISH_PATTERNS else "bearish" if slug in BEARISH_PATTERNS else "none",
                "last_close": d.last_close,
            }
            if brk is not None:
                metrics.update(breakout_index=brk["index"], breakout_time=str(times[brk["index"]].isoformat()), breakout_age=self.n - 1 - brk["index"], breakout_event=brk["event"])
            self.add(
                source="pattern", signal_type=f"pattern_{slug}", category="setup", kind="formation", direction=direction,
                signal_time=d.formation_end, signal_t=None, age_bars=None, status=d.status,
                lifecycle="invalidated" if d.status == "invalidated" else "active", quality=d.quality, strength=d.quality,
                underlying_id=d.detection_id, metrics=metrics, config_version=version, config_hash=chash,
                expiry_rule="formation: active while not invalidated (lifecycle of the pattern scan)",
                explanation=[f"{d.pattern_name} ({d.status_label}), quality {d.quality:.0f} ({d.quality_label})."],
            )

    def stage(self, res: dict[str, Any], version: str, chash: str) -> None:
        m = res.get("metrics") or {}
        st = res.get("stage", "data_unavailable")
        if st in ("data_unavailable",):
            return
        direction = "bullish" if st in ("stage2_confirmed", "early_stage2") else "neutral"
        keep = ("stage", "close", "week", "ema30", "ema30_position", "ema30_direction", "ema30_distance_pct", "base_high", "base_low", "base_range_pct", "setup_met", "volume_expansion", "distance_to_breakout_pct")
        self.add(
            source="stage", signal_type=f"stage_{st}", category="trend", kind="state", direction=direction,
            signal_time=m.get("week"), signal_t=None, age_bars=0, status=st, lifecycle="active",
            metrics={**{k: m.get(k) for k in keep}, "stage": st, "stage_label": res.get("stage_label"), "stage_since": res.get("stage_since")},
            config_version=version, config_hash=chash, expiry_rule="state: valid until the next weekly scan",
            explanation=[f"Stage Analysis (your stage_analysis.txt rules): {res.get('stage_label', st)} since {str(res.get('stage_since') or '')[:10]}."],
        )
        if m.get("rs") is None:
            return
        rs = m.get("rs")
        self.add(
            source="rs_line", signal_type="rs_positive" if rs > 0 else "rs_negative", category="relative_strength", kind="state",
            direction="bullish" if rs > 0 else "bearish", signal_time=m.get("week"), signal_t=None, age_bars=0, status="ok", lifecycle="active",
            strength=rs,
            metrics={k: m.get(k) for k in ("rs", "rs_zero_line", "rs_trend", "rs_trend_angle", "rs_sma", "rs_sma_direction", "price_confirmation", "rs_crossed_above_zero", "rs_crossed_below_zero", "rs_length")},
            config_version=version, config_hash=chash, expiry_rule="state: valid until the next weekly scan",
            explanation=[f"RS Line (rs_line.txt, {m.get('rs_length')} weeks vs NIFTY 50) {rs:+.4f}, trend {m.get('rs_trend')}, RS SMA {m.get('rs_sma_direction')}."],
        )

    def rrg(self, entry: dict[str, Any], benchmark: str, version: str, chash: str) -> None:
        latest = entry.get("latest")
        if entry.get("status") != "ok" or not latest:
            return
        q = latest["quadrant"]
        self.add(
            source="rrg", signal_type=f"rrg_{q}", category="relative_strength", kind="state",
            direction="bullish" if q in ("leading", "improving") else "bearish",
            signal_time=latest["week"], signal_t=latest["t"], age_bars=0, status="ok", lifecycle="active", strength=latest["ratio"],
            metrics={**latest, "benchmark": benchmark, "weeks_valid": entry.get("weeks_valid")},
            config_version=version, config_hash=chash, expiry_rule="state: valid until the next weekly scan",
            explanation=[f"RRG-style rotation vs {benchmark}: {q} (RS-Ratio {latest['ratio']}, RS-Momentum {latest['momentum']}, heading {latest['direction']})."],
        )

    def vcp(self, v: dict[str, Any], version: str, chash: str) -> None:
        if v.get("status") in (None, "none"):
            return
        idx = v.get("breakout_index")
        vu, rule = (self._event_expiry("vcp_breakout", idx) if idx is not None else (None, "formation: active while structurally valid"))
        lifecycle = "invalidated" if v["status"] in ("invalidated", "failed_breakout") else "active"
        self.add(
            source="vcp", signal_type=f"vcp_{v['status']}", category="setup", kind="formation", direction="bullish",
            signal_time=v.get("base_end"), signal_t=None, age_bars=v.get("breakout_age"), status=v["status"], lifecycle=lifecycle,
            quality=v.get("quality"), strength=v.get("quality"), underlying_id=v.get("vcp_id"), valid_until=vu, expiry_rule=rule,
            metrics={k: v.get(k) for k in ("status", "status_label", "qualified", "quality", "contractions", "depths_pct", "pivot", "pivot_distance_pct", "final_depth_pct", "base_sessions", "base_start", "base_end", "breakout_time", "breakout_age", "breakout_volume_ratio", "vcp_id")},
            config_version=version, config_hash=chash,
            explanation=v.get("explanation", [])[:4],
        )
