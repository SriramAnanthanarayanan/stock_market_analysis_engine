"""Local OHLCV cache and the market-data refresh service.

Candles live in CSV files (``cache/ohlcv/<interval>/<SYMBOL>.csv``) so SQLite
holds only metadata (``market_data`` table). Refresh strategy:

* No cache, ``full_refresh`` or a detected re-adjustment -> full download for
  the provider's preferred period (Yahoo: 1d/1wk ``max``, 1h 2y = Yahoo's cap).
* Shallow cache (``market_data.history_period`` differs from the provider's
  preferred period, e.g. a 5-year daily cache after the switch to ``max``) ->
  one full download to backfill the older history (``EnsureReport.backfilled``).
* Hourly caches keep growing past Yahoo's ~730-day limit: incremental refreshes
  only append. A re-adjustment (split) re-downloads them and resets the depth
  to what Yahoo still serves, because older bars on the old basis cannot be
  merged without guessing the adjustment.
* Stale cache -> incremental download starting at the 5th-last cached candle.
  Overlapping candles are replaced by the newly fetched provider values and
  timestamps are deduplicated (no duplicate appends).
* If overlapping closes (raw: a split/bonus) or adjusted closes (a dividend)
  differ by more than ``adjustment_mismatch_tolerance`` the provider has
  re-adjusted history; the symbol is fully re-downloaded so the series stays
  internally consistent.
* Price basis (E1): every cache file is tagged in ``market_data.price_basis``.
  A cache on another basis (P1-P3 dividend-adjusted caches) is fully
  re-downloaded and never analysed: ``load_current`` returns None for it.
* Fresh cache (fetched after the latest completed bar) -> no download.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .. import database as db
from ..config import Settings
from ..providers.base import ADJ_CLOSE, OHLCV_COLUMNS, PRICE_BASIS_LEGACY, MarketDataProvider
from ..timeutil import IST, now_ist, parse_iso
from .calendar import NSECalendar

log = logging.getLogger(__name__)

_INTERVAL_TF = {"1h": "1h", "1d": "1d", "1wk": "1w"}
_SAFE = re.compile(r"[^A-Za-z0-9_\-&]")


def merge_incremental(old: pd.DataFrame, new: pd.DataFrame, tolerance: float) -> tuple[pd.DataFrame, bool]:
    """Merge newly fetched candles into cached ones.

    Returns (merged, adjustment_mismatch). New provider values win on overlap.
    """
    if old is None or old.empty:
        return new.sort_index(), False
    if new.empty:
        return old.sort_index(), False
    overlap = old.index.intersection(new.index)
    mismatch = False
    if len(overlap):
        for col in ("close", ADJ_CLOSE):
            if col not in old.columns or col not in new.columns:
                continue
            a = old.loc[overlap, col].to_numpy(dtype=float)
            b = new.loc[overlap, col].to_numpy(dtype=float)
            ok = ~(pd.isna(a) | pd.isna(b))
            if ok.any():
                rel = abs(a[ok] - b[ok]) / abs(b[ok]).clip(min=1e-12)
                mismatch = mismatch or bool((rel > tolerance).any())
    merged = pd.concat([old[old.index < new.index.min()], new])
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    return merged, mismatch


class OhlcvStore:
    def __init__(self, cache_dir: Path, engine: Engine, provider_name: str, price_basis: str | None = None):
        from ..providers.base import PRICE_BASIS_RAW

        self.root = cache_dir / "ohlcv"
        self.engine = engine
        self.provider_name = provider_name
        self.price_basis = price_basis or PRICE_BASIS_RAW

    def path(self, symbol: str, interval: str) -> Path:
        return self.root / interval / f"{_SAFE.sub('_', symbol)}.csv"

    def load(self, symbol: str, interval: str) -> pd.DataFrame | None:
        p = self.path(symbol, interval)
        if not p.exists():
            return None
        df = pd.read_csv(p, index_col=0)
        idx = pd.to_datetime(df.index, utc=True, format="ISO8601").tz_convert(IST)
        df.index = pd.DatetimeIndex(idx, name="ts")
        for c in OHLCV_COLUMNS + [ADJ_CLOSE]:
            if c not in df.columns:
                df[c] = float("nan")
        return df[OHLCV_COLUMNS + [ADJ_CLOSE]].astype("float64")

    def basis(self, symbol: str, interval: str) -> str | None:
        """Price basis of the cached file (legacy caches without a tag are dividend-adjusted)."""
        meta = self.meta(symbol, interval)
        if meta is None or not self.path(symbol, interval).exists():
            return None
        return meta.get("price_basis") or PRICE_BASIS_LEGACY

    def load_current(self, symbol: str, interval: str) -> pd.DataFrame | None:
        """Cached candles only if they are on the provider's current price basis.

        Analysis code uses this so a scan never mixes raw and dividend-adjusted
        series (a legacy cache that could not be re-downloaded is skipped)."""
        if self.basis(symbol, interval) != self.price_basis:
            return None
        return self.load(symbol, interval)

    def save(self, symbol: str, interval: str, df: pd.DataFrame) -> Path:
        p = self.path(symbol, interval)
        p.parent.mkdir(parents=True, exist_ok=True)
        cols = OHLCV_COLUMNS + ([ADJ_CLOSE] if ADJ_CLOSE in df.columns else [])
        out = df[cols].copy()
        out.index = pd.Index([t.isoformat() for t in pd.DatetimeIndex(out.index).tz_convert(IST)], name="ts")
        tmp = p.with_suffix(".csv.tmp")
        out.to_csv(tmp, index_label="ts")
        tmp.replace(p)
        return p

    def meta(self, symbol: str, interval: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(db.market_data).where(
                    (db.market_data.c.symbol == symbol) & (db.market_data.c.interval == interval)
                )
            ).mappings().first()
        return dict(row) if row else None

    def all_meta(self, interval: str) -> dict[str, dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(db.market_data).where(db.market_data.c.interval == interval)).mappings().all()
        return {r["symbol"]: dict(r) for r in rows}

    def upsert_meta(self, symbol: str, interval: str, **values: Any) -> None:
        base = {"symbol": symbol, "interval": interval, "provider": self.provider_name}
        if values.get("status") == "ok" and "first_ts" in values:
            values.setdefault("price_basis", self.price_basis)  # written together with new candles
        stmt = sqlite_insert(db.market_data).values(**base, **values)
        stmt = stmt.on_conflict_do_update(index_elements=["symbol", "interval"], set_=dict(values, provider=self.provider_name))
        with self.engine.begin() as conn:
            conn.execute(stmt)

    def record_error(self, symbol: str, interval: str, error: str, scan_id: str | None) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                db.download_errors.insert().values(
                    scan_id=scan_id, symbol=symbol, interval=interval, occurred_at=db.now_ist_iso(), error=error[:2000]
                )
            )


@dataclass
class EnsureReport:
    interval: str
    requested: int = 0
    fresh: int = 0
    full: int = 0
    incremental: int = 0
    readjusted: int = 0
    backfilled: int = 0
    skipped_recent_failures: int = 0
    failed: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "interval": self.interval,
            "requested": self.requested,
            "fresh": self.fresh,
            "full": self.full,
            "incremental": self.incremental,
            "readjusted": self.readjusted,
            "backfilled": self.backfilled,
            "skipped_recent_failures": self.skipped_recent_failures,
            "failed": len(self.failed),
            "notes": self.notes,
        }


class MarketDataService:
    """Keeps the local cache up to date. One refresh per interval at a time."""

    def __init__(
        self,
        provider: MarketDataProvider,
        store: OhlcvStore,
        calendar: Callable[[], NSECalendar],
        settings: Settings,
    ):
        self.provider = provider
        self.store = store
        self._calendar = calendar
        self.settings = settings
        self._locks = {iv: threading.Lock() for iv in ("1h", "1d", "1wk")}
        self.last_error: str | None = None
        self.last_success_at: str | None = None

    @property
    def calendar(self) -> NSECalendar:
        return self._calendar()

    def target_period(self, interval: str) -> str:
        """History depth a full download asks the provider for."""
        return self.provider.default_periods(interval)[0]

    def needs_backfill(self, meta: dict[str, Any] | None, interval: str) -> bool:
        """A usable cache downloaded for a shallower history than the provider now serves."""
        return bool(meta and meta.get("status") == "ok" and meta.get("history_period") != self.target_period(interval))

    def _is_fresh(self, meta: dict[str, Any] | None, interval: str, now: datetime, cutoff: datetime) -> bool:
        if not meta or meta.get("status") != "ok":
            return False
        if (meta.get("price_basis") or PRICE_BASIS_LEGACY) != self.store.price_basis:
            return False
        if not self.store.path(meta["symbol"], interval).exists():
            return False
        last_fetch = parse_iso(meta.get("last_fetch_at"))
        if last_fetch is None:
            return False
        if cutoff < now - timedelta(minutes=1):
            # Historical scan: any cache fetched after the cutoff already holds
            # every candle completed by then (within the provider's history).
            return last_fetch >= pd.Timestamp(cutoff)
        latest_end = self.calendar.latest_completed_bar_end(_INTERVAL_TF[interval], now)
        return last_fetch >= pd.Timestamp(latest_end)

    def ensure(
        self,
        symbols: list[str],
        interval: str,
        *,
        now: datetime | None = None,
        cutoff: datetime | None = None,
        full_refresh: bool = False,
        scan_id: str | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> EnsureReport:
        now = now or now_ist()
        cutoff = cutoff or now
        rep = EnsureReport(interval=interval, requested=len(symbols))
        with self._locks[interval]:
            metas = self.store.all_meta(interval)
            recent_failures = self._recent_failures(interval, now)
            full: list[str] = []
            incr: dict[str, date] = {}
            for sym in symbols:
                meta = metas.get(sym)
                shallow = self.needs_backfill(meta, interval)
                if not full_refresh and not shallow and self._is_fresh(meta, interval, now, cutoff):
                    rep.fresh += 1
                    continue
                if not full_refresh and sym in recent_failures and not self.store.path(sym, interval).exists():
                    # A symbol the provider has no data for (e.g. not yet listed on
                    # Yahoo) is retried after a cool-down, not on every scan.
                    rep.failed[sym] = f"skipped: provider returned no data at {recent_failures[sym]}"
                    rep.skipped_recent_failures += 1
                    continue
                cached = None if full_refresh else self.store.load_current(sym, interval)
                n = self.settings.incremental_overlap_bars
                if shallow and not full_refresh and cached is not None:
                    full.append(sym)  # deeper history available: replace the cache once
                    rep.backfilled += 1
                elif cached is None or len(cached) < n:  # incl. a cache on another price basis
                    full.append(sym)
                else:
                    incr[sym] = cached.index[-n].date()
            if not self.settings.network_enabled and (full or incr):
                for sym in full + list(incr):
                    rep.failed[sym] = "network disabled (PAS_NETWORK=0); using cached data only"
                rep.notes.append("network disabled: no downloads attempted")
                return rep
            if incr:
                if progress:
                    progress(f"incremental download {interval}: {len(incr)} symbols")
                readjust = self._incremental(incr, interval, rep, scan_id)
                full.extend(readjust)
            if full:
                if progress:
                    progress(f"full download {interval}: {len(full)} symbols")
                self._full(full, interval, rep, scan_id)
        attempted = rep.requested - rep.fresh - rep.skipped_recent_failures
        attempt_failures = len(rep.failed) - rep.skipped_recent_failures
        if attempted >= 5 and attempt_failures == attempted:
            # Every download failed: a provider/network problem, not a few unlisted symbols.
            self.last_error = f"{interval}: all {attempted} attempted downloads failed at {db.now_ist_iso()}"
        elif rep.full or rep.incremental:
            self.last_success_at = db.now_ist_iso()
            self.last_error = None
        return rep

    def _recent_failures(self, interval: str, now: datetime) -> dict[str, str]:
        since = (pd.Timestamp(now) - pd.Timedelta(minutes=self.settings.failed_symbol_retry_minutes)).isoformat()
        t = db.download_errors
        with self.store.engine.connect() as conn:
            rows = conn.execute(
                select(t.c.symbol, func.max(t.c.occurred_at))
                .where((t.c.interval == interval) & (t.c.occurred_at >= since))
                .group_by(t.c.symbol)
            ).all()
        return {s: ts for s, ts in rows}

    def _full(self, symbols: list[str], interval: str, rep: EnsureReport, scan_id: str | None) -> None:
        remaining = list(symbols)
        periods = self.provider.default_periods(interval)
        # Tagged with the preferred period even after a fallback, so a symbol
        # whose full history the provider cannot serve is not re-downloaded on
        # every refresh (the fallback is recorded in ``notes``).
        target = periods[0]
        last_errors: dict[str, str] = {}
        for i, period in enumerate(periods):
            if not remaining:
                break
            res = self.provider.fetch(remaining, interval, period=period)
            fetched_at = db.now_ist_iso()
            for sym, df in res.data.items():
                self.store.save(sym, interval, df)
                self.store.upsert_meta(
                    sym,
                    interval,
                    file_path=str(self.store.path(sym, interval)),
                    first_ts=df.index[0].isoformat(),
                    last_ts=df.index[-1].isoformat(),
                    rows=len(df),
                    last_fetch_at=fetched_at,
                    last_full_fetch_at=fetched_at,
                    status="ok",
                    error=None,
                    history_period=target,
                    notes=None if i == 0 else f"history period fell back to {period}",
                )
                rep.full += 1
            if i > 0 and res.data:
                rep.notes.append(f"{interval}: {len(res.data)} symbols fell back to period={period}")
            remaining = [s for s in remaining if s not in res.data]
            last_errors = res.errors
        for sym in remaining:
            err = last_errors.get(sym, "no data returned")
            rep.failed[sym] = err
            self.store.record_error(sym, interval, err, scan_id)
            if self.store.path(sym, interval).exists():
                # Keep the previously cached candles usable; record the failure.
                self.store.upsert_meta(sym, interval, error=err[:500])
            else:
                self.store.upsert_meta(sym, interval, status="error", error=err[:500], last_fetch_at=None)

    def _incremental(self, starts: dict[str, date], interval: str, rep: EnsureReport, scan_id: str | None) -> list[str]:
        readjust: list[str] = []
        ordered = sorted(starts, key=lambda s: starts[s])
        batch_size = max(1, self.settings.yahoo_batch_size)
        for i in range(0, len(ordered), batch_size):
            batch = ordered[i : i + batch_size]
            start = min(starts[s] for s in batch)
            res = self.provider.fetch(batch, interval, start=start)
            fetched_at = db.now_ist_iso()
            for sym in batch:
                new = res.data.get(sym)
                if new is None or new.empty:
                    err = res.errors.get(sym, "no data returned")
                    rep.failed[sym] = err
                    self.store.record_error(sym, interval, err, scan_id)
                    # Keep the previous cache; mark the refresh failure only.
                    self.store.upsert_meta(sym, interval, error=err[:500])
                    continue
                old = self.store.load(sym, interval)
                merged, mismatch = merge_incremental(old, new, self.settings.adjustment_mismatch_tolerance)
                if mismatch:
                    readjust.append(sym)
                    rep.readjusted += 1
                    continue
                self.store.save(sym, interval, merged)
                self.store.upsert_meta(
                    sym,
                    interval,
                    file_path=str(self.store.path(sym, interval)),
                    first_ts=merged.index[0].isoformat(),
                    last_ts=merged.index[-1].isoformat(),
                    rows=len(merged),
                    last_fetch_at=fetched_at,
                    status="ok",
                    error=None,
                )
                rep.incremental += 1
        return readjust

    def status(self) -> dict[str, Any]:
        out: dict[str, Any] = {"provider": self.provider.name, "last_success_at": self.last_success_at, "last_error": self.last_error}
        with self.store.engine.connect() as conn:
            for iv in ("1h", "1d", "1wk"):
                rows = conn.execute(select(db.market_data).where(db.market_data.c.interval == iv)).mappings().all()
                ok = [r for r in rows if r["status"] == "ok"]
                out[iv] = {
                    "symbols_cached": len(ok),
                    "symbols_error": len(rows) - len(ok),
                    "latest_fetch": max((r["last_fetch_at"] for r in ok if r["last_fetch_at"]), default=None),
                }
        return out
