"""Synthetic NSE end-of-day files for the F&O OI activity tests (fixtures, not
market data). Same layouts as the real files: UDiFF and legacy F&O bhavcopy,
full equity bhavcopy with space-padded values, index closes.

Last session (designed signals, versus the session before):
  AAA   price +3.0 %, OI +12 %          -> Strong Long Buildup (stock)
  BBB   price -1.6 %, OI +8 %           -> Short Buildup
  CCC   price +1.0 %, OI +9 %           -> nothing (stock needs 1.5 %)
  NIFTY price +1.2 %, OI +8 %           -> Long Buildup (index needs 1 %)
  FINNIFTY price -1.1 %, OI +11 %       -> Strong Short Buildup (index)
  DDD   1:1 bonus: price -50 %, OI x2, lot 500 -> 1000 -> Strong Short Buildup + lot flag
  EEE   previous OI 0, price +2.5 %      -> Strong Long Buildup, infinite OI change
  TIE1 / TIE2 identical changes          -> tie order must match the original
Every earlier session moves price < 0.5 % and OI +1 %: no signals.
"""

from __future__ import annotations

import io
import math
import zipfile
from datetime import date, timedelta

STOCKS = ("AAA", "BBB", "CCC", "DDD", "EEE", "TIE1", "TIE2")
INDICES = ("NIFTY", "FINNIFTY")
BASE = {"AAA": 1000.0, "BBB": 500.0, "CCC": 200.0, "DDD": 800.0, "EEE": 300.0, "TIE1": 100.0, "TIE2": 100.0, "NIFTY": 23000.0, "FINNIFTY": 25000.0}
INDEX_NAMES = {"NIFTY": "Nifty 50", "FINNIFTY": "Nifty Financial Services"}
LAST_MOVE = {"AAA": (3.0, 12.0), "BBB": (-1.6, 8.0), "CCC": (1.0, 9.0), "NIFTY": (1.2, 8.0), "FINNIFTY": (-1.1, 11.0), "DDD": (-50.0, 100.0), "EEE": (2.5, None), "TIE1": (2.2, 15.0), "TIE2": (2.2, 15.0)}
BASIS = 1.002


def last_tuesday(y: int, m: int) -> date:
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    while d.weekday() != 1:
        d -= timedelta(days=1)
    return d


def expiries(d: date, n: int = 3) -> list[date]:
    out, y, m = [], d.year, d.month
    while len(out) < n:
        e = last_tuesday(y, m)
        if e >= d:
            out.append(e)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def sessions(calendar, end: date = date(2026, 3, 27), n: int = 30) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if calendar.is_trading_day(d):
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def market(days: list[date]) -> dict[date, dict]:
    """Per session: futures / options / equity / index rows."""
    out: dict[date, dict] = {}
    n = len(days)
    for i, d in enumerate(days):
        last = i == n - 1
        exps = expiries(d)
        fut, opt, eq, idx = [], [], [], []
        for sym in STOCKS + INDICES:
            price = BASE[sym] * (1 + 0.004 * math.sin(i))
            front_oi = 1_000_000 * (1 + 0.01 * i)
            lot = 500.0
            if last:
                p_prev = BASE[sym] * (1 + 0.004 * math.sin(i - 1))
                oi_prev = 1_000_000 * (1 + 0.01 * (i - 1))
                dp, doi = LAST_MOVE[sym]
                price = p_prev * (1 + dp / 100)
                front_oi = 1000.0 if doi is None else oi_prev * (1 + doi / 100)
                if sym == "DDD":
                    lot = 1000.0
            elif sym == "EEE" and i == n - 2:
                front_oi = 0.0
            spot = price / BASIS
            for k, e in enumerate(exps):
                oi = front_oi if k == 0 else (200_000 * (1 + 0.01 * i) if k == 1 else 10_000.0)
                fut.append({"sym": sym, "inst": "FUTIDX" if sym in INDICES else "FUTSTK", "exp": e, "close": round(price * (1 + 0.001 * k), 2), "oi": oi, "lot": lot, "und": round(spot, 2)})
            if sym in ("AAA", "NIFTY", "FINNIFTY"):
                step = 10 if sym == "AAA" else 100
                atm = round(spot / step) * step
                for e in exps[:2]:
                    for j in range(-3, 4):
                        strike = atm + j * step
                        for typ in ("CE", "PE"):
                            settle = round(max(1.0, 0.02 * spot - abs(j) * step * 0.3), 2)
                            oi = 5000 * (4 - abs(j)) * (3 if (typ == "CE" and j == 2) or (typ == "PE" and j == -2) else 1)
                            opt.append({"sym": sym, "inst": "OPTIDX" if sym in INDICES else "OPTSTK", "exp": e, "strike": float(strike), "typ": typ, "settle": settle, "oi": float(oi)})
            if sym in STOCKS:
                vol = 300_000 if (last and sym == "AAA") else 100_000
                deliv = 60.0 if (last and sym == "AAA") else 40.0
                eq.append({"sym": sym, "series": "EQ", "close": round(spot, 2), "high": round(spot * 1.01, 2), "low": round(spot * 0.99, 2), "vol": vol, "deliv": deliv})
            else:
                idx.append({"name": INDEX_NAMES[sym], "close": round(spot, 2), "high": round(spot * 1.005, 2), "low": round(spot * 0.995, 2)})
        eq.append({"sym": "AAA", "series": "BE", "close": 1.0, "high": 1.0, "low": 1.0, "vol": 1, "deliv": None})
        eq.append({"sym": "ZZZ", "series": "EQ", "close": 50.0, "high": 51.0, "low": 49.0, "vol": 10, "deliv": None})
        out[d] = {"fut": fut, "opt": opt, "eq": eq, "idx": idx}
    return out


UDIFF_HEADER = "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4"
UDIFF_CODE = {"FUTSTK": "STF", "FUTIDX": "IDF", "OPTSTK": "STO", "OPTIDX": "IDO"}


def udiff_text(d: date, m: dict) -> str:
    lines = [UDIFF_HEADER]
    rows = [("o", r) for r in m["opt"][: len(m["opt"]) // 2]] + [("f", r) for r in m["fut"]] + [("o", r) for r in m["opt"][len(m["opt"]) // 2 :]]
    for k, r in rows:
        e = r["exp"].isoformat()
        if k == "f":
            lines.append(f"{d},{d},FO,NSE,{UDIFF_CODE[r['inst']]},1,,{r['sym']},,{e},{e},,,{r['sym']}FUT,{r['close']},{r['close']},{r['close']},{r['close']},{r['close']},{r['close']},{r['und']},{r['close']},{r['oi']:.0f},0,10,100.0,10,F1,{r['lot']:.0f},,,,,")
        else:
            lines.append(f"{d},{d},FO,NSE,{UDIFF_CODE[r['inst']]},2,,{r['sym']},,{e},{e},{r['strike']},{r['typ']},{r['sym']}OPT,0,0,0,{r['settle']},0,{r['settle']},0,{r['settle']},{r['oi']:.0f},0,0,0.0,0,F1,500,,,,,")
    return "\n".join(lines) + "\n"


LEGACY_HEADER = "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP,"


def legacy_text(d: date, m: dict) -> str:
    lines = [LEGACY_HEADER]
    ts = d.strftime("%d-%b-%Y").upper()
    for r in m["fut"]:
        lines.append(f"{r['inst']},{r['sym']},{r['exp'].strftime('%d-%b-%Y')},0,XX,{r['close']},{r['close']},{r['close']},{r['close']},{r['close']},10,1.0,{r['oi']:.0f},0,{ts},")
    for r in m["opt"]:
        lines.append(f"{r['inst']},{r['sym']},{r['exp'].strftime('%d-%b-%Y')},{r['strike']},{r['typ']},0,0,0,{r['settle']},{r['settle']},0,0,{r['oi']:.0f},0,{ts},")
    return "\n".join(lines) + "\n"


def equity_text(d: date, m: dict) -> str:
    lines = ["SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER"]
    for r in m["eq"]:
        dq, dp = ("-", "-") if r["deliv"] is None else (f"{r['vol'] * r['deliv'] / 100:.0f}", f"{r['deliv']:.2f}")
        lines.append(f"{r['sym']}, {r['series']}, {d.strftime('%d-%b-%Y')}, {r['close']}, {r['close']}, {r['high']}, {r['low']}, {r['close']}, {r['close']}, {r['close']}, {r['vol']}, 1.00, 10, {dq}, {dp}")
    return "\n".join(lines) + "\n"


def index_text(d: date, m: dict) -> str:
    lines = ["Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value,Points Change,Change(%),Volume,Turnover (Rs. Cr.),P/E,P/B,Div Yield"]
    for r in m["idx"]:
        lines.append(f'{r["name"]},{d.strftime("%d-%m-%Y")},"{r["close"]:,.2f}","{r["high"]:,.2f}","{r["low"]:,.2f}","{r["close"]:,.2f}",0,0,0,0,0,0,0')
    lines.append("Nifty Bank,x,1,1,1,1,0,0,0,0,0,0,0")
    return "\n".join(lines) + "\n"


def zipped(name: str, text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, text)
    return buf.getvalue()


class FakeFetcher:
    """Offline stand-in for NseArchiveFetcher: url -> bytes (None = 404)."""

    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.calls: list[str] = []

    def get(self, url: str, kind: str) -> bytes | None:
        self.calls.append(url)
        return self.files.get(url)


def archive(calendar, days: list[date], fmt: str = "udiff", with_eq: bool = True, with_idx: bool = True) -> tuple[dict[str, bytes], dict[date, dict]]:
    from app.fno.fetch import eq_url, fo_urls, index_url

    m = market(days)
    files: dict[str, bytes] = {}
    for d in days:
        urls = dict(fo_urls(d))
        if fmt == "udiff":
            files[urls["udiff"]] = zipped(f"BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv", udiff_text(d, m[d]))
        else:
            files[urls["legacy"]] = zipped(f"fo{d:%d}{d:%b}{d:%Y}bhav.csv", legacy_text(d, m[d]))
        if with_eq:
            files[eq_url(d)] = equity_text(d, m[d]).encode()
        if with_idx:
            files[index_url(d)] = index_text(d, m[d]).encode()
    return files, m
