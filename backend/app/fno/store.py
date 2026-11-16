"""Database access of the F&O OI activity feature (tables in app/database.py)."""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import Engine, and_, delete, func, insert, select

from .. import database as db
from .parse import FUTURES


def _f(v: Any) -> float | None:
    """Finite float or None (NaN / missing never stored as a number)."""
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if not math.isnan(x) else None


def _iso(v: Any) -> str | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    ts = pd.to_datetime(v, format="%d-%b-%Y", errors="coerce")
    return None if pd.isna(ts) else ts.date().isoformat()


# ------------------------------------------------------------------ sessions
def get_session(engine: Engine, d: date) -> dict[str, Any] | None:
    with engine.connect() as conn:
        r = conn.execute(select(db.fno_sessions).where(db.fno_sessions.c.trade_date == d.isoformat())).mappings().first()
    return dict(r) if r else None


def sessions(engine: Engine, status: str | None = "ok") -> list[dict[str, Any]]:
    t = db.fno_sessions
    q = select(t).order_by(t.c.trade_date)
    if status:
        q = q.where(t.c.fo_status == status)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(q).mappings()]


def upsert_session(engine: Engine, d: date, **values: Any) -> None:
    t = db.fno_sessions
    with engine.begin() as conn:
        exists = conn.execute(select(t.c.trade_date).where(t.c.trade_date == d.isoformat())).first()
        values["updated_at"] = db.now_ist_iso()
        if exists:
            conn.execute(t.update().where(t.c.trade_date == d.isoformat()).values(**values))
        else:
            conn.execute(insert(t).values(trade_date=d.isoformat(), **values))


def previous_ok_session(engine: Engine, d: date) -> date | None:
    t = db.fno_sessions
    with engine.connect() as conn:
        v = conn.execute(select(func.max(t.c.trade_date)).where(and_(t.c.trade_date < d.isoformat(), t.c.fo_status == "ok"))).scalar()
    return date.fromisoformat(v) if v else None


# ------------------------------------------------------------------ writes
def write_futures(conn, d: date, fo: pd.DataFrame) -> int:
    t = db.fno_futures
    conn.execute(delete(t).where(t.c.trade_date == d.isoformat()))
    fut = fo[fo["INSTRUMENT"].isin(FUTURES)]
    rows = []
    for i, r in enumerate(fut.to_dict("records")):
        rows.append(
            {
                "trade_date": d.isoformat(),
                "row_no": i,
                "instrument": str(r["INSTRUMENT"]),
                "symbol": str(r["SYMBOL"]),
                "expiry_label": None if pd.isna(r.get("EXPIRY_DT")) else str(r["EXPIRY_DT"]),
                "expiry": _iso(r.get("EXPIRY_DT")),
                "open": _f(r.get("OPEN")),
                "high": _f(r.get("HIGH")),
                "low": _f(r.get("LOW")),
                "close": _f(r.get("CLOSE")),
                "settle": _f(r.get("SETTLE_PR")),
                "contracts": _f(r.get("CONTRACTS")),
                "value_lakh": _f(r.get("VAL_INLAKH")),
                "open_int": _f(r.get("OPEN_INT")),
                "chg_oi": _f(r.get("CHG_IN_OI")),
                "underlying": _f(r.get("UNDERLYING")),
                "lot_size": _f(r.get("LOT_SIZE")),
            }
        )
    if rows:
        conn.execute(insert(t), rows)
    return len(rows)


def write_underlying(conn, d: date, equity: pd.DataFrame | None, index: pd.DataFrame | None, stock_symbols: set[str]) -> tuple[int, int]:
    t = db.fno_underlying
    conn.execute(delete(t).where(t.c.trade_date == d.isoformat()))
    fields = ("open", "high", "low", "close", "prev_close", "volume", "turnover_lacs", "trades", "deliv_qty", "deliv_per")
    rows = []
    n_eq = n_idx = 0
    if equity is not None:
        for r in equity[equity["symbol"].isin(stock_symbols)].to_dict("records"):
            rows.append({"symbol": r["symbol"], "trade_date": d.isoformat(), "kind": "stock", "source_name": "EQ", **{k: _f(r.get(k)) for k in fields}})
            n_eq += 1
    if index is not None:
        for r in index.to_dict("records"):
            rows.append({"symbol": r["symbol"], "trade_date": d.isoformat(), "kind": "index", "source_name": r["source_name"], **{k: _f(r.get(k)) for k in fields}})
            n_idx += 1
    if rows:
        conn.execute(insert(t), rows)
    return n_eq, n_idx


def write_option_summaries(conn, d: date, rows: list[dict[str, Any]], chash: str) -> int:
    t = db.fno_option_summary
    conn.execute(delete(t).where(t.c.trade_date == d.isoformat()))
    payload = [{**{k: (_f(v) if isinstance(v, (float, np.floating)) else v) for k, v in r.items()}, "trade_date": d.isoformat(), "config_hash": chash} for r in rows]
    if payload:
        conn.execute(insert(t), payload)
    return len(payload)


# ------------------------------------------------------------------ reads
def futures_frame(engine: Engine, d: date) -> pd.DataFrame:
    """One day's futures rows in file order, in the columns the selection rule uses."""
    t = db.fno_futures
    with engine.connect() as conn:
        rows = conn.execute(select(t.c.instrument, t.c.symbol, t.c.expiry_label, t.c.close, t.c.open_int).where(t.c.trade_date == d.isoformat()).order_by(t.c.row_no)).all()
    df = pd.DataFrame(rows, columns=["INSTRUMENT", "SYMBOL", "EXPIRY_DT", "CLOSE", "OPEN_INT"])
    df.insert(3, "STRIKE_PR", np.nan)
    df.insert(4, "OPTION_TYP", pd.Series([np.nan] * len(df), dtype=object))
    df["CLOSE"] = df["CLOSE"].astype(float)
    df["OPEN_INT"] = df["OPEN_INT"].astype(float)
    return df


def contract_lots(engine: Engine, d: date) -> dict[tuple[str, str], float]:
    t = db.fno_futures
    with engine.connect() as conn:
        rows = conn.execute(select(t.c.symbol, t.c.expiry_label, t.c.lot_size).where(and_(t.c.trade_date == d.isoformat(), t.c.lot_size.is_not(None)))).all()
    return {(s, e): float(lot) for s, e, lot in rows}


def futures_symbols(engine: Engine, d: date) -> list[str]:
    t = db.fno_futures
    with engine.connect() as conn:
        return [r[0] for r in conn.execute(select(t.c.symbol).where(t.c.trade_date == d.isoformat()).distinct().order_by(t.c.symbol))]


def history(engine: Engine, symbols: list[str], d: date, days: int) -> dict[str, pd.DataFrame]:
    """Underlying rows, combined futures OI, same-contract futures closes and
    option summaries of ``symbols`` from ``d - days`` up to and including ``d``."""
    lo = (d - timedelta(days=days)).isoformat()
    hi = d.isoformat()
    u, f, o = db.fno_underlying, db.fno_futures, db.fno_option_summary
    with engine.connect() as conn:
        und = pd.DataFrame(conn.execute(select(u).where(and_(u.c.symbol.in_(symbols), u.c.trade_date >= lo, u.c.trade_date <= hi))).mappings().all())
        comb = pd.DataFrame(
            conn.execute(
                select(f.c.symbol, f.c.trade_date, func.sum(f.c.open_int).label("oi"), func.count().label("n"))
                .where(and_(f.c.symbol.in_(symbols), f.c.trade_date >= lo, f.c.trade_date <= hi))
                .group_by(f.c.symbol, f.c.trade_date)
            ).all(),
            columns=["symbol", "trade_date", "oi", "n"],
        )
        fut = pd.DataFrame(
            conn.execute(select(f.c.symbol, f.c.trade_date, f.c.expiry_label, f.c.close).where(and_(f.c.symbol.in_(symbols), f.c.trade_date >= (d - timedelta(days=60)).isoformat(), f.c.trade_date <= hi))).all(),
            columns=["symbol", "trade_date", "expiry_label", "close"],
        )
        opt = pd.DataFrame(conn.execute(select(o).where(and_(o.c.symbol.in_(symbols), o.c.trade_date >= lo, o.c.trade_date <= hi))).mappings().all())
    return {"underlying": und, "combined_oi": comb, "futures": fut, "options": opt}


# ------------------------------------------------------------------ runs
def write_run(engine: Engine, d: date, chash: str, version: str, prev: date | None, fronts: tuple[str | None, str | None], notices: list[dict[str, Any]], signals: list[dict[str, Any]]) -> None:
    r, s = db.fno_runs, db.fno_signals
    with engine.begin() as conn:
        conn.execute(delete(s).where(and_(s.c.trade_date == d.isoformat(), s.c.config_hash == chash)))
        conn.execute(delete(r).where(and_(r.c.trade_date == d.isoformat(), r.c.config_hash == chash)))
        conn.execute(
            insert(r).values(
                trade_date=d.isoformat(),
                config_hash=chash,
                config_version=version,
                prev_date=prev.isoformat() if prev else None,
                latest_front_expiry=fronts[0],
                prev_front_expiry=fronts[1],
                signals=len(signals),
                notices_json=db.dumps(notices),
                created_at=db.now_ist_iso(),
            )
        )
        if signals:
            conn.execute(
                insert(s),
                [
                    {
                        "trade_date": d.isoformat(),
                        "config_hash": chash,
                        "rank": x["rank"],
                        "symbol": x["symbol"],
                        "instrument": x["instrument"],
                        "expiry_label": x["expiry"],
                        "close_today": x["close_today"],
                        "oi_today": x["oi_today"],
                        "close_prev": x["close_prev"],
                        "oi_prev": x["oi_prev"],
                        "price_change_pct": x["price_change_pct"],
                        "oi_change_pct": x["oi_change_pct"],
                        "signal": x["signal"],
                        "priority": x["priority"],
                        "enrichment_json": db.dumps(x["enrichment"]),
                        "flags_json": db.dumps(x["flags"]),
                    }
                    for x in signals
                ],
            )


def get_run(engine: Engine, d: date, chash: str) -> dict[str, Any] | None:
    r = db.fno_runs
    with engine.connect() as conn:
        row = conn.execute(select(r).where(and_(r.c.trade_date == d.isoformat(), r.c.config_hash == chash))).mappings().first()
    return dict(row) if row else None


def run_signals(engine: Engine, d: date, chash: str) -> list[dict[str, Any]]:
    s = db.fno_signals
    with engine.connect() as conn:
        rows = conn.execute(select(s).where(and_(s.c.trade_date == d.isoformat(), s.c.config_hash == chash)).order_by(s.c.rank)).mappings().all()
    return [dict(r) for r in rows]


def run_dates(engine: Engine, chash: str) -> list[dict[str, Any]]:
    r = db.fno_runs
    with engine.connect() as conn:
        rows = conn.execute(select(r.c.trade_date, r.c.prev_date, r.c.signals, r.c.notices_json).where(r.c.config_hash == chash).order_by(r.c.trade_date.desc())).mappings().all()
    return [{"trade_date": x["trade_date"], "prev_date": x["prev_date"], "signals": x["signals"], "notices": len(db.loads(x["notices_json"]) or [])} for x in rows]


# ------------------------------------------------------------------ watchlist
def watchlist(engine: Engine) -> list[str]:
    w = db.fno_watchlist
    with engine.connect() as conn:
        return [r[0] for r in conn.execute(select(w.c.symbol).order_by(w.c.symbol))]


def set_watchlist(engine: Engine, symbols: list[str]) -> list[str]:
    w = db.fno_watchlist
    now = db.now_ist_iso()
    with engine.begin() as conn:
        existing = {r[0] for r in conn.execute(select(w.c.symbol))}
        keep = set(symbols)
        for sym in existing - keep:
            conn.execute(delete(w).where(w.c.symbol == sym))
        for sym in sorted(keep - existing):
            conn.execute(insert(w).values(symbol=sym, added_at=now))
    return watchlist(engine)


def company_names(engine: Engine, symbols: list[str]) -> dict[str, str]:
    t = db.stocks
    with engine.connect() as conn:
        return {s: c for s, c in conn.execute(select(t.c.symbol, t.c.company).where(t.c.symbol.in_(symbols))) if c}
