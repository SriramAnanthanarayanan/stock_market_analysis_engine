"""Yahoo Finance provider (via yfinance).

Limitations (documented in docs/data-sources.md): data may be delayed,
incomplete, revised or re-adjusted; hourly history is limited to ~730 days; the
09:15 hourly bar is reported with zero volume; holidays occasionally appear as
flat zero-volume bars. Yahoo is not exchange-authoritative.
"""

from __future__ import annotations

import logging
import random
import threading
import time
import warnings
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from ..timeutil import IST
from .base import ADJ_CLOSE, OHLCV_COLUMNS, PRICE_BASIS_RAW, FetchResult, MarketDataProvider

log = logging.getLogger(__name__)

# yfinance keeps per-call error state in module globals; serialise calls.
_YF_LOCK = threading.Lock()
_SESSION_START = pd.Timedelta(hours=9, minutes=15)


def to_ticker(symbol: str) -> str:
    """NSE stock symbol -> Yahoo ticker. Explicit Yahoo tickers (``^NSEI``,
    ``NIFTY_FIN_SERVICE.NS``; used only for verified index mappings) pass through."""
    if symbol.startswith("^") or symbol.endswith(".NS"):
        return symbol
    return f"{symbol}.NS"


def normalize_frame(raw: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Convert one ticker's yfinance frame into the canonical OHLCV frame."""
    frame = raw.rename(columns={c: str(c).strip().lower().replace(" ", "_") for c in raw.columns})
    if any(c not in frame.columns for c in ("open", "high", "low", "close")):
        return pd.DataFrame(columns=OHLCV_COLUMNS + [ADJ_CLOSE])
    if "volume" not in frame.columns:
        frame = frame.assign(volume=np.nan)
    if ADJ_CLOSE not in frame.columns:
        frame = frame.assign(adj_close=np.nan)  # never guessed: returns fall back to raw closes, flagged by callers
    frame = frame[OHLCV_COLUMNS + [ADJ_CLOSE]].apply(pd.to_numeric, errors="coerce").astype("float64")
    frame = frame.dropna(subset=["open", "high", "low", "close"], how="all")
    idx = pd.DatetimeIndex(frame.index)
    # Daily/weekly bars arrive as naive exchange-local dates; intraday is tz-aware.
    idx = idx.tz_localize(IST) if idx.tz is None else idx.tz_convert(IST)
    if interval in ("1d", "1wk"):
        idx = idx.normalize() + _SESSION_START
    frame.index = idx.rename("ts")
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    return frame


class YahooProvider(MarketDataProvider):
    """``auto_adjust=False``: OHLC are raw (split/bonus adjusted only, as on
    TradingView by default); ``Adj Close`` (split + dividend) is kept separately.

    Daily and weekly history ``max``: everything Yahoo has (NSE stocks from
    1996 or their listing date) for long-range chart review and long EMA
    warm-ups. Hourly history is capped by Yahoo at ~730 days; the local cache
    keeps growing past that through incremental refreshes (store.py).
    """

    name = "yahoo"
    price_basis = PRICE_BASIS_RAW
    _PERIODS = {"1h": ["2y", "1y"], "1d": ["max", "10y", "5y"], "1wk": ["max", "10y"]}

    def __init__(
        self,
        batch_size: int = 100,
        max_attempts: int = 3,
        backoff_base_s: float = 2.0,
        cache_dir: Path | None = None,
        sleep=time.sleep,
    ):
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self.backoff_base_s = backoff_base_s
        self.cache_dir = cache_dir
        self._sleep = sleep
        self._yf = None

    def _lib(self):
        if self._yf is None:
            import yfinance as yf  # imported lazily: after net.configure_ssl()

            if self.cache_dir is not None:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                try:
                    yf.set_tz_cache_location(str(self.cache_dir))
                except Exception as exc:  # pragma: no cover - best effort
                    log.debug("yfinance cache location not set: %s", exc)
            self._yf = yf
        return self._yf

    def default_periods(self, interval: str) -> list[str]:
        return list(self._PERIODS[interval])

    def _backoff(self, attempt: int) -> None:
        self._sleep(self.backoff_base_s * (2**attempt) + random.uniform(0, self.backoff_base_s))

    def _download(self, symbols: list[str], interval: str, period: str | None, start: date | None, attempts: int):
        tickers = [to_ticker(s) for s in symbols]
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                with _YF_LOCK:
                    yf = self._lib()
                    kwargs = dict(
                        tickers=tickers,
                        interval=interval,
                        group_by="ticker",
                        auto_adjust=False,
                        threads=True,
                        progress=False,
                    )
                    if start is not None:
                        kwargs["start"] = start.isoformat()
                    else:
                        kwargs["period"] = period
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")  # yfinance-internal pandas deprecations
                        df = yf.download(**kwargs)
                    raw_errors = dict(getattr(getattr(yf, "shared", None), "_ERRORS", {}) or {})
                back = {to_ticker(sym): sym for sym in symbols}
                errors = {back.get(t, t[:-3] if t.endswith(".NS") else t): str(msg) for t, msg in raw_errors.items()}
                return self._split(df, symbols, interval), errors
            except Exception as exc:  # network / rate limit / parsing
                last_exc = exc
                log.warning("Yahoo download attempt %s/%s failed for %s symbols: %s", attempt + 1, attempts, len(symbols), exc)
                if attempt + 1 < attempts:
                    self._backoff(attempt)
        return {}, {s: f"download failed: {last_exc}" for s in symbols}

    @staticmethod
    def _split(df: pd.DataFrame | None, symbols: list[str], interval: str) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        if df is None or df.empty:
            return out
        if isinstance(df.columns, pd.MultiIndex):
            present = set(df.columns.get_level_values(0))
            for sym in symbols:
                t = to_ticker(sym)
                if t in present:
                    frame = normalize_frame(df[t], interval)
                    if not frame.empty:
                        out[sym] = frame
        elif len(symbols) == 1:
            frame = normalize_frame(df, interval)
            if not frame.empty:
                out[symbols[0]] = frame
        return out

    def fetch(self, symbols, interval, *, period=None, start=None) -> FetchResult:
        if interval not in self._PERIODS:
            raise ValueError(f"unsupported interval {interval!r}")
        if start is None and period is None:
            period = self._PERIODS[interval][0]
        result = FetchResult(period_used=period if start is None else f"start={start}")
        todo = list(dict.fromkeys(symbols))
        pending: dict[str, str] = {}
        for i in range(0, len(todo), self.batch_size):
            batch = todo[i : i + self.batch_size]
            frames, errors = self._download(batch, interval, period, start, self.max_attempts)
            for sym in batch:
                if sym in frames:
                    result.data[sym] = frames[sym]
                else:
                    pending[sym] = errors.get(sym, "empty response")
        # Retry failed / empty symbols individually, once.
        for sym, first_error in pending.items():
            frames, errors = self._download([sym], interval, period, start, 1)
            if sym in frames:
                result.data[sym] = frames[sym]
            else:
                result.errors[sym] = errors.get(sym) or first_error
        return result
