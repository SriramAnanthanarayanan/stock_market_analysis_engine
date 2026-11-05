"""Confluence score from persisted signal evidence (docs/confluence-engine.md).

Inputs are the normalized evidence rows that the scanners already stored
(``signal_evidence``): the primary scan (1D by default) and the latest weekly
scan as context (Stage Analysis, RS Line, RRG). Nothing is detected here.

Per stock and direction (bullish and bearish are scored separately):

* five categories, each normalized to 0-1: trend, setup / structure,
  breakout / trigger, volume + candlestick, relative strength;
* **no double counting**: inside a category the strongest evidence counts fully
  and at most one more piece of a *different* family adds a capped bonus
  (``second_evidence_bonus`` x its strength); correlated pairs (Stage vs EMA
  alignment, RS Line vs RRG, 10-day vs 52-week breakout, breakout volume vs the
  volume shock of the same candle) never add up as independent evidence;
* events are scored only while fresh (``freshness_sessions``); expired and
  invalidated evidence is listed, never scored;
* a category without the data it needs is **unavailable** (not zero); the score
  is normalized over the available categories and ``coverage`` says how much of
  the total weight that is; a stock is ranked only with ``coverage >= min_coverage``;
* same-session combination bonus (fresh breakout + Marubozu + volume shock),
  up to ``combo_points``, total capped at 100.

Scores are explainable screening aids, not probabilities of success.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from ..config import ConfluenceConfig

CATEGORY_LABELS = {
    "trend": "Trend quality",
    "setup": "Setup / structure",
    "trigger": "Breakout / trigger",
    "volume_candle": "Volume + candlestick",
    "relative_strength": "Relative strength",
}
SETUP_FAMILY = {"parallel": "parallel", "converging": "converging", "continuation": "continuation", "reversal": "reversal"}
MIRROR_RRG = {"leading": "lagging", "lagging": "leading", "improving": "weakening", "weakening": "improving"}
MIRROR_ALIGN = {"bullish_full": "bearish_full", "bearish_full": "bullish_full", "bullish_partial": "bearish_partial", "bearish_partial": "bullish_partial", "mixed": "mixed"}


def _clamp(x: float) -> float:
    return float(min(1.0, max(0.0, x)))


def _m(e: dict[str, Any]) -> dict[str, Any]:
    return e.get("metrics") or {}


@dataclass
class Item:
    evidence_id: str
    source: str
    label: str
    strength: float
    family: str
    note: str = ""
    status: str = "used"  # used | bonus | overlapping | expired | invalidated
    time: str | None = None

    def out(self) -> dict[str, Any]:
        return {"evidence_id": self.evidence_id, "source": self.source, "label": self.label, "strength": round(self.strength, 4), "family": self.family, "status": self.status, "note": self.note, "time": self.time}


def _best_plus_one(items: list[Item], bonus: float, correlated_note: str) -> float:
    """Strongest item counts fully; one item of another family adds bonus x strength; cap 1."""
    if not items:
        return 0.0
    items.sort(key=lambda x: -x.strength)
    best = items[0]
    best.status = "used"
    score = best.strength
    extra_done = False
    for it in items[1:]:
        if not extra_done and it.family != best.family and it.strength > 0:
            it.status = "bonus"
            it.note = it.note or f"second independent evidence: + {bonus} x strength"
            score += bonus * it.strength
            extra_done = True
        else:
            it.status = "overlapping"
            it.note = it.note or correlated_note
    return _clamp(score)


class SymbolEvidence:
    """Evidence of one symbol split by source (primary scan + weekly context)."""

    def __init__(self, primary: list[dict[str, Any]], context: list[dict[str, Any]]):
        self.by: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for e in primary:
            self.by[e["source"]].append(e)
        for e in context:
            if e["source"] in ("stage", "rs_line", "rrg"):
                self.by[f"ctx_{e['source']}"].append(e)
        self.primary = primary

    def get(self, source: str) -> list[dict[str, Any]]:
        return self.by.get(source, [])

    def one(self, source: str, signal_type: str | None = None) -> dict[str, Any] | None:
        for e in self.get(source):
            if signal_type is None or e["signal_type"] == signal_type:
                return e
        return None


def _fresh(e: dict[str, Any], key: str, cfg: ConfluenceConfig) -> tuple[bool, str]:
    if e.get("lifecycle") == "invalidated":
        return False, "invalidated"
    age = e.get("age_bars")
    if age is not None and age > cfg.freshness_sessions.get(key, 0):
        return False, "expired"
    return True, "used"


def _breakout_strength(m: dict[str, Any], cfg: ConfluenceConfig) -> float:
    d = abs(m.get("distance_atr") or 0.0)
    s = 0.5 + 0.5 * _clamp(d / cfg.breakout_atr_full)
    if m.get("first_in_lookback") is False:
        s *= cfg.non_first_breakout_factor
    return s


def _rvol_strength(rvol: float | None, cfg: ConfluenceConfig) -> float:
    if rvol is None:
        return 0.0
    return _clamp((rvol - cfg.rvol_zero) / (cfg.rvol_full - cfg.rvol_zero))


def _trend(ev: SymbolEvidence, direction: str, cfg: ConfluenceConfig) -> tuple[bool, float, list[Item], str]:
    items: list[Item] = []
    st = ev.one("ctx_stage")
    stage_c = None
    if st and st["status"] not in ("insufficient_history", "data_unavailable"):
        m = _m(st)
        if direction == "bullish":
            stage_c = cfg.stage_scores.get(st["status"], 0.0)
        else:
            pos, dirn = m.get("ema30_position"), m.get("ema30_direction")
            stage_c = 1.0 if (pos == "below" and dirn == "falling") else 0.5 if (pos == "below" and dirn == "flat") else 0.0
        items.append(Item(st["evidence_id"], "stage", f"Stage Analysis: {m.get('stage_label') or st['status']}", stage_c, "stage", time=st.get("signal_time")))
    ema = ev.one("ema_alignment")
    ema_c = None
    if ema and _m(ema).get("alignment") not in (None, "not_available"):
        m = _m(ema)
        al = m["alignment"] if direction == "bullish" else MIRROR_ALIGN.get(m["alignment"], m["alignment"])
        al_s = cfg.alignment_scores.get(al, 0.0)
        pv = m.get("price_vs_ema") or {}
        want = "above" if direction == "bullish" else "below"
        pos = sum(1 for k in ("50", "200") if pv.get(k) == want) / 2.0
        ema_c = 0.7 * al_s + 0.3 * pos
        items.append(Item(ema["evidence_id"], "ema_alignment", f"EMA alignment {m['alignment'].replace('_', ' ')}; price {want} EMA 50/200: {int(pos * 2)}/2", ema_c, "ema", time=ema.get("signal_time")))
    if stage_c is None and ema_c is None:
        return False, 0.0, items, "no Stage Analysis or EMA alignment available"
    parts = [x for x in (stage_c, ema_c) if x is not None]
    score = max(parts)
    agree = stage_c is not None and ema_c is not None and min(stage_c, ema_c) >= 0.6
    if agree:
        score += cfg.agreement_bonus
    items.sort(key=lambda x: -x.strength)
    for i, it in enumerate(items):
        it.status = "used" if i == 0 else ("bonus" if agree else "overlapping")
        if i:
            it.note = f"correlated with {items[0].source}: agreement bonus {cfg.agreement_bonus} only" if agree else "correlated trend measure: not added"
    return True, _clamp(score), items, f"max(Stage, EMA) + {cfg.agreement_bonus} when both >= 0.6"


def _setup(ev: SymbolEvidence, direction: str, cfg: ConfluenceConfig) -> tuple[bool, float, list[Item], list[Item]]:
    items: list[Item] = []
    listed: list[Item] = []
    v = ev.one("vcp")
    if v is not None:
        m = _m(v)
        st = v["status"]
        q = (v.get("quality") or 0.0) / 100.0
        if direction != "bullish":
            listed.append(Item(v["evidence_id"], "vcp", f"VCP {m.get('status_label', st)}", 0.0, "vcp", status="overlapping", note="bullish structure: not counted for bearish"))
        elif st in ("invalidated", "failed_breakout"):
            listed.append(Item(v["evidence_id"], "vcp", f"VCP {m.get('status_label', st)}", 0.0, "vcp", status="invalidated"))
        elif st == "developing":
            listed.append(Item(v["evidence_id"], "vcp", "VCP developing (one contraction)", 0.0, "vcp", status="overlapping", note="not a setup yet"))
        else:
            f = cfg.forming_factor if st == "forming" else 0.8 if st == "pivot_cross_low_volume" else 1.0
            items.append(Item(v["evidence_id"], "vcp", f"VCP {m.get('status_label', st)} (quality {v.get('quality'):.0f})", q * f, "vcp", time=m.get("base_end")))
    for p in ev.get("pattern"):
        m = _m(p)
        lab = f"{m.get('pattern_name')} ({p['status']}, quality {p.get('quality') or 0:.0f})"
        fam = SETUP_FAMILY.get(m.get("family"), "pattern")
        if p["lifecycle"] == "invalidated":
            listed.append(Item(p["evidence_id"], "pattern", lab, 0.0, fam, status="invalidated"))
            continue
        if p["direction"] != direction:
            continue
        q = (p.get("quality") or 0.0) / 100.0
        f = cfg.forming_factor if p["status"] == "forming" else 1.0
        items.append(Item(p["evidence_id"], "pattern", lab, q * f, fam, time=m.get("formation_end")))
    score = _best_plus_one(items, cfg.second_evidence_bonus, "same structural family / overlapping formation: not added")
    return True, score, items, listed


def _trigger(ev: SymbolEvidence, direction: str, cfg: ConfluenceConfig, tf: str) -> tuple[bool, float, list[Item], list[Item]]:
    items: list[Item] = []
    listed: list[Item] = []
    for src, key, fam in (("fresh_breakout", "fresh_breakout", "breakout_10d"), ("week52", "week52", "breakout_52w")):
        for e in ev.get(src):
            if e["kind"] != "event" or e["status"] != "confirmed" or e["direction"] != direction:
                continue
            m = _m(e)
            ok, why = _fresh(e, key, cfg)
            label = ("Fresh 10-day " if src == "fresh_breakout" else "52-week ") + ("high breakout" if direction == "bullish" else "low breakdown") + f" ({e['age_bars']} sessions ago)"
            it = Item(e["evidence_id"], src, label, _breakout_strength(m, cfg), fam, time=e.get("signal_time"))
            if ok:
                items.append(it)
            else:
                it.status, it.strength = why, 0.0
                listed.append(it)
    for p in ev.get("pattern"):
        m = _m(p)
        want = "broke_above" if direction == "bullish" else "broke_below"
        if p["status"] != want or m.get("breakout_age") is None:
            continue
        age = m["breakout_age"]
        it = Item(p["evidence_id"], "pattern", f"{m.get('pattern_name')} {want.replace('_', ' ')} ({age} bars ago)", (p.get("quality") or 0.0) / 100.0, "pattern_breakout", time=m.get("breakout_time"))
        if age <= cfg.freshness_sessions.get("pattern_breakout", 0):
            items.append(it)
        else:
            it.status, it.strength = "expired", 0.0
            listed.append(it)
    v = ev.one("vcp")
    if v is not None and direction == "bullish" and v["status"] in ("breakout", "breakout_pending"):
        m = _m(v)
        age = m.get("breakout_age") or 0
        it = Item(v["evidence_id"], "vcp", f"VCP pivot breakout ({age} sessions ago, volume {m.get('breakout_volume_ratio')}x)", max(0.7, (v.get("quality") or 0.0) / 100.0), "vcp_breakout", time=m.get("breakout_time"))
        if age <= cfg.freshness_sessions.get("vcp_breakout", 0):
            items.append(it)
        else:
            it.status, it.strength = "expired", 0.0
            listed.append(it)
    score = _best_plus_one(items, cfg.second_evidence_bonus, "correlated breakout signal (same price move): capped, not added")
    return True, score, items, listed


def _volume_candle(ev: SymbolEvidence, direction: str, cfg: ConfluenceConfig) -> tuple[bool, float, list[Item], list[Item], dict[str, Any]]:
    vs = ev.one("volume_shock", "volume_state")
    available = vs is None or vs["status"] == "ok"
    if not available:
        return False, 0.0, [], [], {}
    vol_items: list[Item] = []
    listed: list[Item] = []
    for e in ev.get("volume_shock"):
        if e["kind"] != "event":
            continue
        if e["direction"] not in (direction, "neutral"):
            continue
        m = _m(e)
        ok, why = _fresh(e, "volume_shock", cfg)
        f = 0.5 if e["direction"] == "neutral" else 1.0
        it = Item(e["evidence_id"], "volume_shock", f"{e['direction'].title()} volume shock {m.get('rvol')}x ({e['age_bars']} bars ago)", _rvol_strength(m.get("rvol"), cfg) * f, "volume", time=e.get("signal_time"))
        if ok:
            vol_items.append(it)
        else:
            it.status, it.strength = why, 0.0
            listed.append(it)
    for src in ("fresh_breakout", "week52"):
        for e in ev.get(src):
            if e["kind"] != "event" or e["status"] != "confirmed" or e["direction"] != direction:
                continue
            ok, _ = _fresh(e, src, cfg)
            if not ok:
                continue
            m = _m(e)
            if m.get("volume_ratio") is not None:
                vol_items.append(Item(e["evidence_id"], src, f"Breakout volume {m.get('volume_ratio')}x (20-day average)", _rvol_strength(m.get("volume_ratio"), cfg), "volume", time=e.get("signal_time")))
    vol_items.sort(key=lambda x: -x.strength)
    for i, it in enumerate(vol_items):
        it.status = "used" if i == 0 else "overlapping"
        if i:
            it.note = "same volume measured again (breakout candle / shock): not added"
    V = vol_items[0].strength if vol_items else 0.0
    candles: list[Item] = []
    want = "bullish_marubozu" if direction == "bullish" else "bearish_marubozu"
    for e in ev.get("marubozu"):
        if e["signal_type"] != want:
            continue
        m = _m(e)
        ok, why = _fresh(e, "marubozu", cfg)
        it = Item(e["evidence_id"], "marubozu", f"{want.replace('_', ' ').title()} (range {m.get('range_atr')} ATR, {e['age_bars']} bars ago)", 0.8 + 0.2 * _clamp(((m.get("range_atr") or 1.0) - 1.0) / 1.0), "candle", time=e.get("signal_time"))
        if ok:
            candles.append(it)
        else:
            it.status, it.strength = why, 0.0
            listed.append(it)
    candles.sort(key=lambda x: -x.strength)
    for i, it in enumerate(candles):
        it.status = "used" if i == 0 else "overlapping"
    C = candles[0].strength if candles else 0.0
    score = _clamp(cfg.volume_share * V + cfg.candle_share * C)
    return True, score, vol_items + candles, listed, {"volume": V, "candle": C}


def _rs(ev: SymbolEvidence, direction: str, cfg: ConfluenceConfig) -> tuple[bool, float, list[Item]]:
    items: list[Item] = []
    rs = ev.one("ctx_rs_line")
    rs_c = None
    if rs is not None:
        m = _m(rs)
        val = m.get("rs")
        if val is not None:
            if direction == "bullish":
                rs_c = (0.6 + 0.2 * (m.get("rs_trend") == "improving") + 0.2 * (m.get("rs_sma_direction") == "rising")) if val > 0 else (0.2 if m.get("rs_trend") == "improving" else 0.0)
            else:
                rs_c = (0.6 + 0.2 * (m.get("rs_trend") == "deteriorating") + 0.2 * (m.get("rs_sma_direction") == "falling")) if val < 0 else (0.2 if m.get("rs_trend") == "deteriorating" else 0.0)
            items.append(Item(rs["evidence_id"], "rs_line", f"RS Line {val:+.3f} ({m.get('rs_trend')}, RS SMA {m.get('rs_sma_direction')})", rs_c, "rs_line", time=rs.get("signal_time")))
    rr = ev.one("ctx_rrg")
    rr_c = None
    if rr is not None:
        q = _m(rr).get("quadrant")
        key = q if direction == "bullish" else MIRROR_RRG.get(q, q)
        rr_c = cfg.rrg_scores.get(key, 0.0)
        items.append(Item(rr["evidence_id"], "rrg", f"RRG {q} vs {_m(rr).get('benchmark')}", rr_c, "rrg", time=rr.get("signal_time")))
    if rs_c is None and rr_c is None:
        return False, 0.0, items
    score = max(x for x in (rs_c, rr_c) if x is not None)
    agree = rs_c is not None and rr_c is not None and min(rs_c, rr_c) >= 0.6
    if agree:
        score += cfg.agreement_bonus
    items.sort(key=lambda x: -x.strength)
    for i, it in enumerate(items):
        it.status = "used" if i == 0 else ("bonus" if agree else "overlapping")
        if i:
            it.note = f"correlated relative-strength measure: agreement bonus {cfg.agreement_bonus} only" if agree else "correlated relative-strength measure: not added"
    return True, _clamp(score), items


def _combo(ev: SymbolEvidence, direction: str, cfg: ConfluenceConfig) -> dict[str, Any]:
    """Same-session fresh breakout + Marubozu + volume shock (bonus points)."""
    sessions: dict[str, set[str]] = defaultdict(set)
    for src in ("fresh_breakout", "week52"):
        for e in ev.get(src):
            if e["kind"] == "event" and e["status"] == "confirmed" and e["direction"] == direction and e["lifecycle"] == "active" and (e.get("age_bars") or 0) <= cfg.combo_max_age_sessions:
                sessions[e["signal_time"]].add("breakout")
    want = "bullish_marubozu" if direction == "bullish" else "bearish_marubozu"
    for e in ev.get("marubozu"):
        if e["signal_type"] == want and e["signal_time"] in sessions:
            sessions[e["signal_time"]].add("marubozu")
    for e in ev.get("volume_shock"):
        if e["kind"] == "event" and e["status"] == "shock" and e["direction"] in (direction, "neutral") and e["signal_time"] in sessions:
            sessions[e["signal_time"]].add("volume")
    best: dict[str, Any] = {"type": None, "points": 0.0, "session": None}
    for t, parts in sessions.items():
        if {"breakout", "marubozu", "volume"} <= parts:
            key = "breakout_marubozu_volume"
        elif {"breakout", "volume"} <= parts:
            key = "breakout_volume"
        elif {"breakout", "marubozu"} <= parts:
            key = "breakout_marubozu"
        else:
            continue
        pts = cfg.combo_points.get(key, 0.0)
        if pts > best["points"]:
            best = {"type": key, "points": pts, "session": t}
    return best


def band_of(score: float, cfg: ConfluenceConfig) -> str:
    for label, floor in sorted(cfg.bands.items(), key=lambda kv: -kv[1]):
        if score >= floor:
            return label
    return "Low Confluence"


def score_symbol(primary: list[dict[str, Any]], context: list[dict[str, Any]], direction: str, cfg: ConfluenceConfig, timeframe: str = "1d") -> dict[str, Any]:
    ev = SymbolEvidence(primary, context)
    cats: dict[str, dict[str, Any]] = {}
    t_av, t_s, t_items, t_rule = _trend(ev, direction, cfg)
    cats["trend"] = {"available": t_av, "score": t_s, "items": t_items, "listed": [], "rule": t_rule}
    s_av, s_s, s_items, s_listed = _setup(ev, direction, cfg)
    cats["setup"] = {"available": s_av, "score": s_s, "items": s_items, "listed": s_listed, "rule": f"strongest structure + {cfg.second_evidence_bonus} x one structure of another family, cap 1"}
    g_av, g_s, g_items, g_listed = _trigger(ev, direction, cfg, timeframe)
    cats["trigger"] = {"available": g_av, "score": g_s, "items": g_items, "listed": g_listed, "rule": f"strongest fresh trigger + {cfg.second_evidence_bonus} x one trigger of another type, cap 1 (10-day and 52-week breakouts are correlated)"}
    v_av, v_s, v_items, v_listed, v_parts = _volume_candle(ev, direction, cfg)
    cats["volume_candle"] = {"available": v_av, "score": v_s, "items": v_items, "listed": v_listed, "rule": f"{cfg.volume_share} x volume strength (RVOL {cfg.rvol_zero} -> 0, {cfg.rvol_full} -> 1; best of shock / breakout volume) + {cfg.candle_share} x Marubozu strength", "parts": v_parts}
    r_av, r_s, r_items = _rs(ev, direction, cfg)
    cats["relative_strength"] = {"available": r_av, "score": r_s, "items": r_items, "listed": [], "rule": f"max(RS Line, RRG) + {cfg.agreement_bonus} when both >= 0.6"}
    total_w = sum(cfg.weights.values())
    avail_w = sum(cfg.weights[k] for k, c in cats.items() if c["available"])
    base = 100.0 * sum(cfg.weights[k] * c["score"] for k, c in cats.items() if c["available"]) / avail_w if avail_w else 0.0
    combo = _combo(ev, direction, cfg)
    score = min(100.0, base + combo["points"])
    coverage = avail_w / total_w if total_w else 0.0
    explanation = []
    for k, c in cats.items():
        w = cfg.weights[k]
        explanation.append(
            {
                "key": k,
                "label": CATEGORY_LABELS[k],
                "weight": w,
                "available": c["available"],
                "score": round(c["score"], 4),
                "points": round(100.0 * w * c["score"] / avail_w, 2) if (c["available"] and avail_w) else None,
                "rule": c["rule"],
                "evidence": [it.out() for it in c["items"]] + [it.out() for it in c["listed"]],
                **({"parts": {kk: round(vv, 4) for kk, vv in c["parts"].items()}} if c.get("parts") else {}),
            }
        )
    active = sum(1 for c in cats.values() for it in c["items"] if it.status in ("used", "bonus") and it.strength > 0)
    return {
        "direction": direction,
        "score": round(score, 2),
        "base_score": round(base, 2),
        "bonus": combo["points"],
        "combo": combo,
        "coverage": round(coverage, 4),
        "ranked": coverage >= cfg.min_coverage,
        "band": band_of(score, cfg),
        "active_signals": active,
        "categories": explanation,
    }


def summarize(primary: list[dict[str, Any]], context: list[dict[str, Any]], direction: str, cfg: ConfluenceConfig) -> dict[str, Any]:
    """Column values of the dashboard table (one stock, one direction)."""
    ev = SymbolEvidence(primary, context)
    out: dict[str, Any] = {}
    v = ev.one("vcp")
    out["vcp_status"] = v["status"] if v else None
    out["vcp_label"] = _m(v).get("status_label") if v else None

    def latest_event(src: str) -> dict[str, Any] | None:
        evs = [e for e in ev.get(src) if e["kind"] == "event" and e["status"] == "confirmed" and e["direction"] == direction and _fresh(e, src, cfg)[0]]
        return min(evs, key=lambda e: e.get("age_bars") or 0) if evs else None

    fb = latest_event("fresh_breakout")
    out["fresh_breakout"] = {"age": fb["age_bars"], "first_in_lookback": _m(fb).get("first_in_lookback"), "level": _m(fb).get("level")} if fb else None
    w52 = latest_event("week52")
    out["week52"] = {"age": w52["age_bars"], "level": _m(w52).get("level")} if w52 else None
    want = "bullish_marubozu" if direction == "bullish" else "bearish_marubozu"
    mar = [e for e in ev.get("marubozu") if e["signal_type"] == want and _fresh(e, "marubozu", cfg)[0]]
    out["marubozu"] = {"age": min(e["age_bars"] for e in mar)} if mar else None
    shocks = [e for e in ev.get("volume_shock") if e["kind"] == "event" and e["status"] == "shock" and e["direction"] in (direction, "neutral") and _fresh(e, "volume_shock", cfg)[0]]
    out["volume_shock"] = {"age": min(e["age_bars"] for e in shocks), "rvol": max(_m(e).get("rvol") or 0 for e in shocks), "direction": shocks[0]["direction"]} if shocks else None
    pats = [e for e in ev.get("pattern") if e["direction"] == direction and e["lifecycle"] != "invalidated"]
    best = max(pats, key=lambda e: e.get("quality") or 0) if pats else None
    out["pattern"] = {"name": _m(best).get("pattern_name"), "status": best["status"], "quality": best.get("quality")} if best else None
    ema = ev.one("ema_alignment")
    out["ema_alignment"] = _m(ema).get("alignment") if ema else None
    st = ev.one("ctx_stage")
    out["stage"] = st["status"] if st else None
    out["stage_label"] = _m(st).get("stage_label") if st else None
    rs = ev.one("ctx_rs_line")
    out["rs"] = _m(rs).get("rs") if rs else None
    out["rs_trend"] = _m(rs).get("rs_trend") if rs else None
    rr = ev.one("ctx_rrg")
    out["rrg_quadrant"] = _m(rr).get("quadrant") if rr else None
    times = [e.get("signal_time") for e in primary if e["kind"] == "event" and e.get("signal_time") and e.get("lifecycle") == "active"]
    out["latest_signal_time"] = max(times) if times else None
    ema_m = _m(ema) if ema else {}
    out["close"] = ema_m.get("close")
    return out


def inputs_key(primary_scan: str, context_scan: str | None, chash: str, timeframe: str) -> str:
    return hashlib.sha1(f"{primary_scan}|{context_scan}|{chash}|{timeframe}".encode()).hexdigest()[:16]
