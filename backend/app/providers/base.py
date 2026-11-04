"""Market data provider interface.

Detectors never talk to a provider. The data layer calls a provider, normalises
its output into the canonical OHLCV frame and caches it.

Canonical frame:

* index: bar *start* time, tz-aware ``Asia/Kolkata`` (daily bars at 09:15 IST
  of their date, weekly bars at 09:15 IST of the week's Monday);
* columns: ``open, high, low, close, volume`` (float64; volume may be NaN when
  the provider reports it as missing) plus optional ``adj_close``.

Price basis (E1, docs/data-sources.md): ``open/high/low/close`` are **raw**
exchange prices adjusted for splits/bonus issues only - the basis TradingView
shows by default. ``adj_close`` is the provider's split- *and* dividend-adjusted
close, used only for return and relative-performance calculations. The two are
never mixed: charts, patterns, S/R, candlesticks and breakouts use raw OHLC.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]
ADJ_CLOSE = "adj_close"
PROVIDER_INTERVALS = ("1h", "1d", "1wk")

# Stored with every cache file, scan and track so results on different bases
# are never compared or drawn together.
PRICE_BASIS_RAW = "raw_split_adjusted"  # OHLC adjusted for splits/bonus only (TradingView default)
PRICE_BASIS_LEGACY = "adjusted_legacy"  # P1-P3 caches: OHLC split + dividend adjusted (yfinance auto_adjust)
PRICE_BASIS_LABELS = {
    PRICE_BASIS_RAW: "Raw OHLC (split/bonus adjusted, not dividend adjusted)",
    PRICE_BASIS_LEGACY: "Dividend-adjusted OHLC (legacy cache)",
}


@dataclass
class FetchResult:
    data: dict[str, pd.DataFrame] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    period_used: str | None = None


class MarketDataProvider(ABC):
    name: str = "abstract"
    price_basis: str = PRICE_BASIS_RAW

    @abstractmethod
    def fetch(
        self,
        symbols: list[str],
        interval: str,
        *,
        period: str | None = None,
        start: date | None = None,
    ) -> FetchResult:
        """Fetch history for NSE ``symbols`` (without exchange suffix).

        Either ``period`` (full history) or ``start`` (incremental) is given.
        Must allow partial success: failed symbols go to ``FetchResult.errors``.
        """

    @abstractmethod
    def default_periods(self, interval: str) -> list[str]:
        """Full-history periods to try in order (first = preferred)."""


def empty_frame() -> pd.DataFrame:
    idx = pd.DatetimeIndex([], tz="Asia/Kolkata", name="ts")
    return pd.DataFrame({c: pd.Series(dtype="float64") for c in OHLCV_COLUMNS + [ADJ_CLOSE]}, index=idx)
