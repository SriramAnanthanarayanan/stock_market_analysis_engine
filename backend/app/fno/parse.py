"""Parsers for the NSE end-of-day files of the F&O OI activity feature.

* F&O bhavcopy, UDiFF format (July 2024 onwards):
  ``BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip``.
* F&O bhavcopy, legacy format (before July 2024): ``foDDMONYYYYbhav.csv.zip``.
* Full equity bhavcopy with delivery data: ``sec_bhavdata_full_DDMMYYYY.csv``.
* Index closes: ``ind_close_all_DDMMYYYY.csv``.

Both F&O formats are normalised to the "standard" schema the user's selection
rules were written for (Bhav copy src/schema.py): INSTRUMENT, SYMBOL,
EXPIRY_DT (``DD-Mon-YYYY``), STRIKE_PR, OPTION_TYP, OPEN, HIGH, LOW, CLOSE,
SETTLE_PR, CONTRACTS, VAL_INLAKH, OPEN_INT, CHG_IN_OI.

Fixes against the original project (none of them changes the selection rule):

* Legacy files mark futures with ``STRIKE_PR = 0`` and ``OPTION_TYP = XX``;
  UDiFF leaves them empty. Futures get NaN for both in every format, so the
  merge keys of two days always align (the original only did this for UDiFF).
* The equity file uses ``CLOSE_PRICE``, ``TTL_TRD_QNTY``, ``HIGH_PRICE`` ... and
  pads every value with a space (``" EQ"``). The original looked for ``CLOSE``,
  ``TOTTRDQTY``, ``HIGH`` and compared ``SERIES == "EQ"``, so no stock row was
  ever found and every stock-level metric was empty.
* ``DELIV_PER`` / ``DELIV_QTY`` are ``-`` for some rows; they become missing,
  never 0.
"""

from __future__ import annotations

import io
import zipfile

import numpy as np
import pandas as pd

from .config import INDEX_SPOT_NAMES

FUTURES = ("FUTSTK", "FUTIDX")
OPTIONS = ("OPTSTK", "OPTIDX")
REQUIRED_FO_COLS = ["INSTRUMENT", "SYMBOL", "EXPIRY_DT", "STRIKE_PR", "OPTION_TYP", "OPEN", "HIGH", "LOW", "CLOSE", "OPEN_INT"]

# NSE UDiFF column -> standard column (Bhav copy src/schema.py)
NSE_FO_COL_MAP = {
    "FinInstrmTp": "INSTRUMENT",
    "TckrSymb": "SYMBOL",
    "XpryDt": "EXPIRY_DT",
    "StrkPric": "STRIKE_PR",
    "OptnTp": "OPTION_TYP",
    "OpnPric": "OPEN",
    "HghPric": "HIGH",
    "LwPric": "LOW",
    "ClsPric": "CLOSE",
    "SttlmPric": "SETTLE_PR",
    "TtlTradgVol": "CONTRACTS",
    "TtlTrfVal": "VAL_INLAKH",
    "OpnIntrst": "OPEN_INT",
    "ChngInOpnIntrst": "CHG_IN_OI",
    "TradDt": "TIMESTAMP",
    # kept for enrichment flags (UDiFF only)
    "UndrlygPric": "UNDERLYING",
    "NewBrdLotQty": "LOT_SIZE",
}
NSE_INSTRUMENT_MAP = {"STF": "FUTSTK", "IDF": "FUTIDX", "STO": "OPTSTK", "IDO": "OPTIDX"}
_FO_NUMERIC = ("STRIKE_PR", "OPEN", "HIGH", "LOW", "CLOSE", "SETTLE_PR", "CONTRACTS", "VAL_INLAKH", "OPEN_INT", "CHG_IN_OI", "UNDERLYING", "LOT_SIZE")

EQ_COL_MAP = {
    "OPEN_PRICE": "open",
    "HIGH_PRICE": "high",
    "LOW_PRICE": "low",
    "CLOSE_PRICE": "close",
    "PREV_CLOSE": "prev_close",
    "TTL_TRD_QNTY": "volume",
    "TURNOVER_LACS": "turnover_lacs",
    "NO_OF_TRADES": "trades",
    "DELIV_QTY": "deliv_qty",
    "DELIV_PER": "deliv_per",
}
# Column names the original project's documentation assumed (old NSE layout).
EQ_LEGACY_COL_MAP = {"OPEN": "open", "HIGH": "high", "LOW": "low", "CLOSE": "close", "PREVCLOSE": "prev_close", "TOTTRDQTY": "volume", "TOTALTRADES": "trades"}
EQ_FIELDS = ("open", "high", "low", "close", "prev_close", "volume", "turnover_lacs", "trades", "deliv_qty", "deliv_per")

INDEX_COL_MAP = {"Index Name": "index_name", "Open Index Value": "open", "High Index Value": "high", "Low Index Value": "low", "Closing Index Value": "close"}


class ParseError(ValueError):
    pass


def zip_csv_text(content: bytes) -> str:
    """Text of the first CSV inside a zip archive."""
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise ParseError("no CSV inside the zip archive")
        return zf.read(names[0]).decode("utf-8-sig", errors="replace")


def _read(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text.lstrip("﻿")), skipinitialspace=True)
    df.columns = df.columns.str.strip()
    return df.loc[:, ~df.columns.str.startswith("Unnamed")]


def _strip(s: pd.Series) -> pd.Series:
    return s.map(lambda x: x.strip() if isinstance(x, str) else x)


def detect_source(df: pd.DataFrame) -> str:
    """'udiff', 'legacy' or 'unknown'."""
    if "FinInstrmTp" in df.columns and "TckrSymb" in df.columns:
        return "udiff"
    if "INSTRUMENT" in df.columns and "SYMBOL" in df.columns:
        return "legacy"
    return "unknown"


def _normalize_expiry_dt(series: pd.Series) -> pd.Series:
    """YYYY-MM-DD -> DD-Mon-YYYY (the format the selection rule parses)."""
    parsed = pd.to_datetime(series, format="%Y-%m-%d", errors="coerce")
    out = parsed.dt.strftime("%d-%b-%Y")
    return out.where(parsed.notna(), series)


def normalize_fo(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise a raw F&O bhavcopy frame (either format) to the standard schema."""
    df = df.copy()
    df.columns = df.columns.str.strip()
    df = df.loc[:, ~df.columns.str.startswith("Unnamed")]
    source = detect_source(df)
    if source == "unknown":
        raise ParseError(f"unknown F&O bhavcopy schema (first columns: {list(df.columns[:8])})")
    if source == "udiff":
        df = df.rename(columns={k: v for k, v in NSE_FO_COL_MAP.items() if k in df.columns})
    for col in ("INSTRUMENT", "SYMBOL", "OPTION_TYP", "EXPIRY_DT"):
        if col in df.columns:
            df[col] = _strip(df[col])
    if source == "udiff":
        df["INSTRUMENT"] = df["INSTRUMENT"].map(lambda x: NSE_INSTRUMENT_MAP.get(x, x))
        df["EXPIRY_DT"] = _normalize_expiry_dt(df["EXPIRY_DT"])
    missing = [c for c in REQUIRED_FO_COLS if c not in df.columns]
    if missing:
        raise ParseError(f"F&O bhavcopy is missing columns {missing}")
    for col in _FO_NUMERIC:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    fut = df["INSTRUMENT"].isin(FUTURES)
    df["STRIKE_PR"] = df["STRIKE_PR"].astype(float)
    df.loc[fut, "STRIKE_PR"] = np.nan
    df["OPTION_TYP"] = df["OPTION_TYP"].astype(object)
    df.loc[fut, "OPTION_TYP"] = np.nan
    df.attrs["source"] = source
    return df.reset_index(drop=True)


def parse_fo(text: str) -> pd.DataFrame:
    return normalize_fo(_read(text))


def parse_equity(text: str) -> pd.DataFrame:
    """EQ-series rows of the full equity bhavcopy: symbol + EQ_FIELDS."""
    df = _read(text)
    if "SYMBOL" not in df.columns or "SERIES" not in df.columns:
        raise ParseError(f"equity bhavcopy without SYMBOL / SERIES (columns: {list(df.columns[:8])})")
    df["SYMBOL"] = _strip(df["SYMBOL"])
    df["SERIES"] = _strip(df["SERIES"])
    df = df[df["SERIES"] == "EQ"]
    out = pd.DataFrame({"symbol": df["SYMBOL"].astype(str)})
    for src, dst in {**EQ_LEGACY_COL_MAP, **EQ_COL_MAP}.items():
        if src in df.columns and dst not in out.columns:
            out[dst] = pd.to_numeric(_strip(df[src]).replace({"-": None, "": None}), errors="coerce")
    for f in EQ_FIELDS:
        if f not in out.columns:
            out[f] = np.nan
    if out["close"].isna().all():
        raise ParseError("equity bhavcopy has no close prices")
    return out.drop_duplicates("symbol").reset_index(drop=True)


def parse_index(text: str) -> pd.DataFrame:
    """Index closes keyed by the F&O index symbol (NIFTY, BANKNIFTY, ...)."""
    df = _read(text)
    if "Index Name" not in df.columns or "Closing Index Value" not in df.columns:
        raise ParseError(f"index file without Index Name / Closing Index Value (columns: {list(df.columns[:6])})")
    df = df.rename(columns={k: v for k, v in INDEX_COL_MAP.items() if k in df.columns})
    df["index_name"] = _strip(df["index_name"]).astype(str)
    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", "", regex=False).str.strip(), errors="coerce")
    by_name = {n.lower(): r for n, r in zip(df["index_name"], df.to_dict("records"))}
    rows = []
    for symbol, names in INDEX_SPOT_NAMES.items():
        for n in names:
            r = by_name.get(n.lower())
            if r is not None and pd.notna(r["close"]):
                rows.append({"symbol": symbol, "source_name": r["index_name"], "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"]})
                break
    return pd.DataFrame(rows, columns=["symbol", "source_name", "open", "high", "low", "close"])
