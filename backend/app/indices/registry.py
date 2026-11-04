"""NSE index registry (E2, spec 9.1).

Source of truth: the official NSE index catalogue ``/api/allIndices``
(nseindia.com, NSE Indices data). Every published index is recorded with its
official name, official symbol (``indexSymbol``), the catalogue group NSE files
it under, and deterministic category / subcategory / type rules (below). Indices
that disappear from the catalogue are marked inactive, never deleted. New
indices appear automatically on the next refresh.

Category rules
--------------
* ``BROAD MARKET INDICES`` -> broad, ``SECTORAL INDICES`` -> sector,
  ``THEMATIC INDICES`` -> thematic, ``STRATEGY INDICES`` -> strategy (factor /
  strategy), ``FIXED INCOME INDICES`` -> fixed_income.
* ``INDICES ELIGIBLE IN DERIVATIVES`` is a cross-cutting list: NIFTY BANK and
  NIFTY FINANCIAL SERVICES -> sector, the others -> broad (``DERIVATIVE_GROUP``).
* Type: VIX -> volatility; LEVERAGE -> leveraged; INVERSE -> inverse; DIVIDEND
  POINTS -> dividend_points; USD -> currency; fixed income; otherwise equity.
  Only equity price indices take part in returns, indicators and the RRG.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import Engine, select, update

from .. import database as db

log = logging.getLogger(__name__)

CATALOGUE_SOURCE = "https://www.nseindia.com/api/allIndices"
GROUP_CATEGORY = {
    "BROAD MARKET INDICES": "broad",
    "SECTORAL INDICES": "sector",
    "THEMATIC INDICES": "thematic",
    "STRATEGY INDICES": "strategy",
    "FIXED INCOME INDICES": "fixed_income",
}
DERIVATIVE_GROUP = "INDICES ELIGIBLE IN DERIVATIVES"
DERIVATIVE_SECTOR = {"NIFTY BANK", "NIFTY FIN SERVICE"}
CATEGORY_LABELS = {"broad": "Broad market", "sector": "Sectoral", "thematic": "Thematic", "strategy": "Factor / strategy", "fixed_income": "Fixed income"}

_FACTORS = [
    ("momentum", r"MOMENT|MOMNTM"),
    ("quality", r"QUALIT|QUALTY|QLTY|\bQ\d|\bMQ\b|\bAQL"),
    ("value", r"VALUE|\bAQLV\b|MQVLV"),
    ("low_volatility", r"LOW[- ]?VOL|LOWVOL|\bLV\b|\bAQL|MQVLV"),
    ("alpha", r"ALPHA|\bAQL"),
]


class RegistryError(RuntimeError):
    pass


def classify(group: str, name: str, symbol: str) -> tuple[str, str | None, str]:
    """(category, subcategory, index_type) from the catalogue group and the official name."""
    u = f"{name} {symbol}".upper()
    if group == DERIVATIVE_GROUP:
        category = "sector" if symbol in DERIVATIVE_SECTOR else "broad"
    else:
        category = GROUP_CATEGORY.get(group, "other")
    if category == "fixed_income":
        return category, "g_sec" if "G-SEC" in u or "GS " in u else "bond", "fixed_income"
    if "VIX" in u:
        return category, "volatility", "volatility"
    if "LEVERAGE" in u or "2X LEV" in u:
        return category, "leveraged", "leveraged"
    if "INVERSE" in u or "1X INV" in u:
        return category, "inverse", "inverse"
    if "DIVIDEND POINTS" in u or "DIV POINT" in u:
        return category, "dividend_points", "dividend_points"
    if re.search(r"\bUSD\b", u):
        return category, "currency", "currency"
    sub: str | None = None
    if category == "strategy":
        hits = [f for f, rx in _FACTORS if re.search(rx, u)]
        if len(set(hits)) >= 2 or "MULTIFACTOR" in u:
            sub = "multi_factor"
        elif hits:
            sub = hits[0]
        elif "EQUAL" in u or " EW" in u or "EQL" in u:
            sub = "equal_weight"
        elif "DIVIDEND" in u or "DIV OPPS" in u:
            sub = "dividend"
        elif "BETA" in u:
            sub = "high_beta"
        elif "GROWTH" in u or "GROWSECT" in u:
            sub = "growth"
        else:
            sub = "other_strategy"
    elif category == "broad":
        if "MICROCAP" in u:
            sub = "microcap"
        elif "MIDSMALL" in u or "MIDSML" in u:
            sub = "mid_small"
        elif "SMALLCAP" in u or "SMLCAP" in u:
            sub = "smallcap"
        elif "LARGEMID" in u:
            sub = "large_mid"
        elif "MIDCAP" in u or "MID SELECT" in u:
            sub = "midcap"
        elif "MULTICAP" in u or "TOTAL" in u or "LMS" in u or "LARGEMIDSMALL" in u or re.search(r"NIFTY (200|500)\b", u):
            sub = "broad_multi"
        else:
            sub = "largecap"
    elif category == "sector":
        for key, rx in (
            ("financial_services", r"FIN"),
            ("banking", r"BANK"),
            ("it", r"\bIT\b|TELCM"),
            ("auto", r"AUTO"),
            ("fmcg", r"FMCG"),
            ("pharma", r"PHARMA"),
            ("healthcare", r"HEALTH|HLTH"),
            ("metal", r"METAL"),
            ("realty", r"REALTY|REIT"),
            ("oil_gas", r"OIL"),
            ("media", r"MEDIA"),
            ("consumer_durables", r"CONSUMER DURABLES|CONSR DURBL"),
            ("chemicals", r"CHEMICAL"),
            ("cement", r"CEMENT"),
        ):
            if re.search(rx, u):
                sub = key
                break
    elif category == "thematic":
        for key, rx in (
            ("consumption", r"CONSUMP|CONSUMER|CONS\b"),
            ("infrastructure", r"INFRA|HOUSING|LOGIS|TRANSPORT|MOBILITY|RAILWAY"),
            ("manufacturing", r"MANUFACTUR|MFG"),
            ("public_sector", r"CPSE|\bPSE\b|PSU"),
            ("esg_shariah", r"ESG|SHARIAH"),
            ("digital", r"DIGITAL|INTERNET|WAVES"),
            ("defence", r"DEFENCE"),
            ("tourism", r"TOURISM"),
            ("corporate_group", r"GROUP|MAATR|CONGLOMERATE"),
            ("commodities_energy", r"COMMODIT|ENERGY|\bEV\b"),
            ("liquidity", r"LIQUID"),
        ):
            if re.search(rx, u):
                sub = key
                break
        sub = sub or "other_theme"
    return category, sub, "equity"


def _reference(row: dict[str, Any]) -> dict[str, Any]:
    """Official reference closes published with the catalogue (used to verify data mappings)."""
    def pair(date_key: str, val_key: str) -> dict[str, Any] | None:
        d, v = row.get(date_key), row.get(val_key)
        try:
            return {"date": str(d), "close": float(v)} if d and v not in (None, "", "-") else None
        except (TypeError, ValueError):
            return None

    return {
        "previous_day": pair("previousDay", "previousDayVal"),
        "one_week_ago": pair("oneWeekAgo", "oneWeekAgoVal"),
        "one_month_ago": pair("date30dAgo", "oneMonthAgoVal"),
        "one_year_ago": pair("date365dAgo", "oneYearAgoVal"),
    }


def parse_catalogue(payload: dict[str, Any], min_rows: int = 50) -> list[dict[str, Any]]:
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) < min_rows:
        raise RegistryError(f"index catalogue has {len(rows) if isinstance(rows, list) else 'no'} rows; expected >= {min_rows}")
    out = []
    seen: set[str] = set()
    for r in rows:
        name = str(r.get("index") or "").strip()
        code = str(r.get("indexSymbol") or name).strip()
        group = str(r.get("key") or "").strip()
        if not name or not code or code in seen:
            continue
        seen.add(code)
        category, sub, itype = classify(group, name, code)
        out.append(
            {
                "code": code,
                "name": name,
                "short_name": code.title().replace("Nifty", "NIFTY") if len(code) <= len(name) else name,
                "category": category,
                "subcategory": sub,
                "nse_group": group,
                "index_type": itype,
                "return_basis": "PR",
                "analysis_enabled": int(itype == "equity"),
                "reference": _reference(r),
            }
        )
    return out


class IndexRegistry:
    def __init__(self, engine: Engine):
        self.engine = engine

    def upsert(self, items: list[dict[str, Any]], source_url: str = CATALOGUE_SOURCE) -> dict[str, int]:
        now = db.now_ist_iso()
        t = db.index_registry
        added = updated = 0
        with self.engine.begin() as conn:
            existing = {r["code"]: dict(r) for r in conn.execute(select(t)).mappings()}
            for it in items:
                values = {
                    "name": it["name"],
                    "short_name": it["short_name"],
                    "category": it["category"],
                    "subcategory": it["subcategory"],
                    "nse_group": it["nse_group"],
                    "index_type": it["index_type"],
                    "return_basis": it["return_basis"],
                    "analysis_enabled": it["analysis_enabled"],
                    "active": 1,
                    "source_url": source_url,
                    "last_seen_at": now,
                    "reference_json": db.dumps(it["reference"]),
                }
                if it["code"] in existing:
                    conn.execute(update(t).where(t.c.code == it["code"]).values(**values))
                    updated += 1
                else:
                    status = "pending" if it["analysis_enabled"] else "unavailable"
                    reason = None if it["analysis_enabled"] else f"{it['index_type']} index: not an equity price index; excluded from price analysis"
                    conn.execute(t.insert().values(code=it["code"], discovered_at=now, data_status=status, data_status_reason=reason, **values))
                    added += 1
            gone = [c for c in existing if c not in {i["code"] for i in items}]
            if gone:
                conn.execute(update(t).where(t.c.code.in_(gone)).values(active=0))
        return {"added": added, "updated": updated, "inactive": len(gone), "total": len(items)}

    def all(self, active_only: bool = False) -> list[dict[str, Any]]:
        t = db.index_registry
        q = select(t).order_by(t.c.category, t.c.code)
        if active_only:
            q = q.where(t.c.active == 1)
        with self.engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(q).mappings()]
        for r in rows:
            r["reference"] = db.loads(r.pop("reference_json", None)) or {}
        return rows

    def get(self, code: str) -> dict[str, Any] | None:
        t = db.index_registry
        with self.engine.connect() as conn:
            r = conn.execute(select(t).where(t.c.code == code)).mappings().first()
        if r is None:
            return None
        r = dict(r)
        r["reference"] = db.loads(r.pop("reference_json", None)) or {}
        return r

    def find(self, text: str) -> dict[str, Any] | None:
        """Exact official symbol or official name (case-insensitive)."""
        u = text.strip().upper()
        for r in self.all():
            if r["code"].upper() == u or r["name"].upper() == u:
                return r
        return None

    def set_status(self, code: str, **values: Any) -> None:
        t = db.index_registry
        with self.engine.begin() as conn:
            conn.execute(update(t).where(t.c.code == code).values(**values))
