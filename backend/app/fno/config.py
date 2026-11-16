"""F&O OI activity configuration (docs/fno-activity.md).

The selection thresholds are the user's own rules, ported unchanged from the
standalone "Bhav copy" screener (src/processor.py). Treat them like StageConfig:
change them only when the user explicitly asks, then bump ``version`` and
register the new hash in ``KNOWN_FNO_CONFIG_HASHES``.

The enrichment parameters are informational only: they never select, rank or
suppress a stock.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import ParamMeta

_F = ("F&O OI activity",)
_D = ("1d",)


@dataclass(frozen=True)
class FnoConfig:
    version: str = "1.0.0"
    # ---- selection: the user's own rules (Bhav copy src/processor.py), DO NOT CHANGE
    oi_strong_pct: float = 10.0
    oi_normal_pct: float = 7.0
    price_strong_stock_pct: float = 2.0
    price_normal_stock_pct: float = 1.5
    price_strong_index_pct: float = 1.0
    price_normal_index_pct: float = 1.0
    # ---- enrichment (informational; never affects selection)
    volume_avg_sessions: int = 20
    range_sessions: tuple[int, ...] = (10, 20)
    oi_change_sessions: tuple[int, ...] = (5, 20)
    basis_avg_sessions: int = 5
    basis_trend_band: float = 0.05
    basis_trend_band_zero: float = 0.5
    delivery_avg_sessions: int = 20
    rank_history_days: int = 365
    rank_min_obs: int = 10
    iv_min_days_to_expiry: int = 2


FNO_PARAM_META: dict[str, ParamMeta] = {
    "version": ParamMeta("Configuration version. Bump on any parameter change.", "semver", _F, _D),
    "oi_strong_pct": ParamMeta("User rule: Strong Long/Short Buildup needs a front-month futures OI change above this % versus the previous session.", "%", _F, _D),
    "oi_normal_pct": ParamMeta("User rule: Long/Short Buildup needs a front-month futures OI change above this %.", "%", _F, _D),
    "price_strong_stock_pct": ParamMeta("User rule: stock futures (FUTSTK) price change beyond +/- this % for a Strong buildup.", "%", _F, _D),
    "price_normal_stock_pct": ParamMeta("User rule: stock futures price change beyond +/- this % for a normal buildup.", "%", _F, _D),
    "price_strong_index_pct": ParamMeta("User rule: index futures (FUTIDX) price change beyond +/- this % for a Strong buildup.", "%", _F, _D),
    "price_normal_index_pct": ParamMeta("User rule: index futures price change beyond +/- this % for a normal buildup.", "%", _F, _D),
    "volume_avg_sessions": ParamMeta("Volume ratio = today's traded quantity / mean of this many previous sessions (needs the full window).", "sessions", _F, _D),
    "range_sessions": ParamMeta("High/low windows of previous sessions; distance of today's spot close from them (needs the full window).", "sessions", _F, _D),
    "oi_change_sessions": ParamMeta("OI change versus this many sessions ago, on combined OI of all expiries (rollover-safe).", "sessions", _F, _D),
    "basis_avg_sessions": ParamMeta("Basis trend: today's basis versus the mean basis of this many previous sessions of the same futures contract.", "sessions", _F, _D),
    "basis_trend_band": ParamMeta("Basis trend band: Expanding / Contracting when today's basis is beyond the average +/- this fraction of |average| (original value).", "fraction", _F, _D),
    "basis_trend_band_zero": ParamMeta("Basis trend band in rupees when the average basis is exactly 0 (original value).", "Rs", _F, _D),
    "delivery_avg_sessions": ParamMeta("Delivery surge: today's delivery % versus the mean of this many previous sessions.", "sessions", _F, _D),
    "rank_history_days": ParamMeta("Volume rank and IV rank / percentile use previous sessions within this many calendar days.", "days", _F, _D),
    "rank_min_obs": ParamMeta("Minimum previous sessions for a volume rank or an IV rank / percentile.", "sessions", _F, _D),
    "iv_min_days_to_expiry": ParamMeta("ATM IV uses the nearest option expiry with at least this many calendar days left (the original returned nothing for 0-1 days; now the next expiry is used). Brenner-Subrahmanyam approximation from settlement prices.", "days", _F, _D),
}

KNOWN_FNO_CONFIG_HASHES: dict[str, str] = {
    "1.0.0": "9a1275c4ebc50526",  # Bhav copy rules ported unchanged + enrichment fixes (docs/fno-activity.md)
}

DEFAULT_FNO_CONFIG = FnoConfig()

# FO index symbol -> accepted names in NSE ind_close_all (case-insensitive).
# Fix: the original mapped FINNIFTY to "Nifty Fin Service"; the file says
# "Nifty Financial Services", so FINNIFTY never got a spot price.
INDEX_SPOT_NAMES: dict[str, tuple[str, ...]] = {
    "NIFTY": ("Nifty 50",),
    "BANKNIFTY": ("Nifty Bank",),
    "FINNIFTY": ("Nifty Financial Services", "Nifty Fin Service"),
    "MIDCPNIFTY": ("Nifty Midcap Select",),
    "NIFTYNXT50": ("Nifty Next 50",),
    "CNXAUTO": ("Nifty Auto",),
    "CNXIT": ("Nifty IT",),
    "CNXPHARMA": ("Nifty Pharma",),
    "CNXMETAL": ("Nifty Metal",),
    "CNXREALTY": ("Nifty Realty",),
    "CNXENERGY": ("Nifty Energy",),
    "CNXFMCG": ("Nifty FMCG",),
    "CNXINFRA": ("Nifty Infrastructure",),
    "CNXMEDIA": ("Nifty Media",),
    "CNXPSUBANK": ("Nifty PSU Bank",),
    "CNXPVTBANK": ("Nifty Private Bank",),
}

# FO index symbol -> index_registry code (chart preview of the index).
INDEX_CHART_CODES: dict[str, str] = {
    "NIFTY": "NIFTY 50",
    "BANKNIFTY": "NIFTY BANK",
    "FINNIFTY": "NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NIFTY MID SELECT",
    "NIFTYNXT50": "NIFTY NEXT 50",
}
