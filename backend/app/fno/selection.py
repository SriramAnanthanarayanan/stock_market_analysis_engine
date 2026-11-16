"""F&O stock selection: the user's own rules, ported UNCHANGED from the
standalone "Bhav copy" screener (src/processor.py, commit eeedf71).

DO NOT CHANGE THIS LOGIC unless the user explicitly asks. The only differences
from the original are mechanical:

* the functions take the two days' F&O frames instead of CSV paths (the frames
  come from the database, in file order, with the same columns);
* the thresholds are read from ``FnoConfig`` (default values = the original
  literals: OI 10 / 7 %, price 2 / 1.5 % for stocks, 1 / 1 % for indices);
* the watchlist symbols are passed as a list instead of a file path.

tests/test_fno.py runs the verbatim original (tests/fno_original_processor.py)
and this port on the same files and requires identical results.

Known properties of the rule, kept on purpose (shown as notices / flags by the
service, never "fixed" here):

* Each day is cut to its own nearest futures expiry before the merge, so on
  the first session after an expiry the two days have different front months
  and nothing matches: no signals that day.
* Prices and OI are not adjusted for corporate actions; a bonus / split shows
  up as a large price fall with a large OI rise.
* A contract with zero OI on the previous day has an infinite OI change.
"""

from __future__ import annotations

import pandas as pd

from .config import DEFAULT_FNO_CONFIG, FnoConfig


def process_bhavcopy(df_latest: pd.DataFrame, df_prev: pd.DataFrame, cfg: FnoConfig = DEFAULT_FNO_CONFIG) -> pd.DataFrame:
    """
    Compares two Bhavcopy files to identify signals.
    """
    if df_latest.empty or df_prev.empty:
        return pd.DataFrame()

    req_cols = ['INSTRUMENT', 'SYMBOL', 'EXPIRY_DT', 'STRIKE_PR', 'OPTION_TYP', 'CLOSE', 'OPEN_INT']

    # Check for missing columns
    for col in req_cols:
        if col not in df_latest.columns:
            return pd.DataFrame()

    # Filter FUTSTK and FUTIDX
    valid_instruments = ['FUTSTK', 'FUTIDX']
    df_latest = df_latest[df_latest['INSTRUMENT'].isin(valid_instruments)].copy()
    df_prev = df_prev[df_prev['INSTRUMENT'].isin(valid_instruments)].copy()

    # Filter BOTH files to their nearest (front-month) expiry BEFORE merging.
    # This prevents rollover contracts (e.g. Apr-28 with very low base OI on Mar-4)
    # from producing false 700%+ OI spikes that trigger fake signals.
    def get_nearest_expiry_df(df):
        df = df.copy()
        df['_exp'] = pd.to_datetime(df['EXPIRY_DT'], format='%d-%b-%Y', errors='coerce')
        min_exp = df['_exp'].min()
        return df[df['_exp'] == min_exp].drop(columns=['_exp'])

    df_latest = get_nearest_expiry_df(df_latest)
    df_prev = get_nearest_expiry_df(df_prev)

    # Ensure keys are compatible for merge
    merge_keys = ['INSTRUMENT', 'SYMBOL', 'EXPIRY_DT', 'STRIKE_PR', 'OPTION_TYP']

    # Merge on front-month contracts only
    merged = pd.merge(
        df_latest[merge_keys + ['CLOSE', 'OPEN_INT']],
        df_prev[merge_keys + ['CLOSE', 'OPEN_INT']],
        on=merge_keys,
        suffixes=('_today', '_yesterday'),
        how='inner'
    )

    # Calculations
    merged['Price_Change_Pct'] = ((merged['CLOSE_today'] - merged['CLOSE_yesterday']) / merged['CLOSE_yesterday']) * 100
    merged['OI_Change_Pct'] = ((merged['OPEN_INT_today'] - merged['OPEN_INT_yesterday']) / merged['OPEN_INT_yesterday']) * 100

    # Identify Signals
    # Long Buildup:
    #   Tier 1 (Strong): OI > 10% AND Price > 2% (1% for Index)
    #   Tier 2 (Normal): OI > 7% AND Price > 1.5% (1% for Index)
    # Short Buildup:
    #   Tier 1 (Strong): OI > 10% AND Price < -2% (-1% for Index)
    #   Tier 2 (Normal): OI > 7% AND Price < -1.5% (-1% for Index)

    def identify_signal(row):
        is_index = row['INSTRUMENT'] == 'FUTIDX'
        oi_change = row['OI_Change_Pct']
        price_change = row['Price_Change_Pct']

        # Thresholds
        limit_oi_strong = cfg.oi_strong_pct
        limit_oi_normal = cfg.oi_normal_pct

        limit_price_strong = cfg.price_strong_index_pct if is_index else cfg.price_strong_stock_pct
        limit_price_normal = cfg.price_normal_index_pct if is_index else cfg.price_normal_stock_pct

        # Long Logic
        if oi_change > limit_oi_strong and price_change > limit_price_strong:
            return 'Strong Long Buildup'
        elif oi_change > limit_oi_normal and price_change > limit_price_normal:
            return 'Long Buildup'

        # Short Logic
        if oi_change > limit_oi_strong and price_change < -limit_price_strong:
            return 'Strong Short Buildup'
        elif oi_change > limit_oi_normal and price_change < -limit_price_normal:
            return 'Short Buildup'

        return None

    merged['Signal'] = merged.apply(identify_signal, axis=1)

    # Filter rows with signals
    signals_df = merged[merged['Signal'].notna()].copy()

    if signals_df.empty:
        return pd.DataFrame()

    final_df = signals_df.copy()

    # Sort: Strong Signals First
    priority_map = {
        'Strong Long Buildup': 1,
        'Strong Short Buildup': 1,
        'Long Buildup': 2,
        'Short Buildup': 2
    }
    final_df['Priority'] = final_df['Signal'].map(priority_map)
    final_df = final_df.sort_values(by=['Priority', 'OI_Change_Pct'], ascending=[True, False])

    return final_df


def process_watchlist(df_latest: pd.DataFrame, df_prev: pd.DataFrame, watchlist_symbols: list[str]) -> pd.DataFrame:
    """
    Processes specific symbols from the watchlist file.
    """
    watchlist_symbols = [s.strip().upper() for s in watchlist_symbols if s.strip()]

    if not watchlist_symbols:
        return pd.DataFrame()

    if df_latest.empty or df_prev.empty:
        return pd.DataFrame()

    # Filter for FUTSTK/FUTIDX and Watchlist Symbols
    valid_instruments = ['FUTSTK', 'FUTIDX']
    df_latest = df_latest[
        (df_latest['INSTRUMENT'].isin(valid_instruments)) &
        (df_latest['SYMBOL'].isin(watchlist_symbols))
    ].copy()
    df_prev = df_prev[df_prev['INSTRUMENT'].isin(valid_instruments)].copy()

    # Filter both files to nearest expiry BEFORE merging (same fix as process_bhavcopy).
    def get_nearest_expiry_df(df):
        df = df.copy()
        df['_exp'] = pd.to_datetime(df['EXPIRY_DT'], format='%d-%b-%Y', errors='coerce')
        min_exp = df['_exp'].min()
        return df[df['_exp'] == min_exp].drop(columns=['_exp'])

    df_latest = get_nearest_expiry_df(df_latest)
    df_prev = get_nearest_expiry_df(df_prev)

    # Merge keys
    merge_keys = ['INSTRUMENT', 'SYMBOL', 'EXPIRY_DT', 'STRIKE_PR', 'OPTION_TYP']

    merged = pd.merge(
        df_latest[merge_keys + ['CLOSE', 'OPEN_INT']],
        df_prev[merge_keys + ['CLOSE', 'OPEN_INT']],
        on=merge_keys,
        suffixes=('_today', '_yesterday'),
        how='inner'
    )

    # Calculations
    merged['Price_Change_Pct'] = ((merged['CLOSE_today'] - merged['CLOSE_yesterday']) / merged['CLOSE_yesterday']) * 100
    merged['OI_Change_Pct'] = ((merged['OPEN_INT_today'] - merged['OPEN_INT_yesterday']) / merged['OPEN_INT_yesterday']) * 100

    # Identify Status (4 Quadrants)
    def get_status(row):
        p = row['Price_Change_Pct']
        o = row['OI_Change_Pct']

        if p > 0 and o > 0: return "Long Buildup"
        if p < 0 and o > 0: return "Short Buildup"
        if p < 0 and o < 0: return "Long Unwinding"
        if p > 0 and o < 0: return "Short Covering"
        return "Neutral"

    merged['Status'] = merged.apply(get_status, axis=1)

    # Filter Nearest Expiry
    merged['EXPIRY_DT_OBJ'] = pd.to_datetime(merged['EXPIRY_DT'], format='%d-%b-%Y', errors='coerce')
    merged = merged.dropna(subset=['EXPIRY_DT_OBJ'])

    if merged.empty:
        return pd.DataFrame()

    min_expiry = merged['EXPIRY_DT_OBJ'].min()
    final_df = merged[merged['EXPIRY_DT_OBJ'] == min_expiry].copy()

    return final_df


def front_expiry(df: pd.DataFrame) -> str | None:
    """Nearest futures expiry of one day's frame, as the selection rule sees it."""
    fut = df[df["INSTRUMENT"].isin(["FUTSTK", "FUTIDX"])]
    exp = pd.to_datetime(fut["EXPIRY_DT"], format="%d-%b-%Y", errors="coerce").min()
    return None if pd.isna(exp) else exp.date().isoformat()
