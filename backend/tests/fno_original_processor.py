# Verbatim copy of the standalone "Bhav copy" screener src/processor.py
# (C:/Users/Admin/Desktop/MicroSaas-Repo/Bhav copy, commit eeedf71), kept ONLY
# as the reference for tests/test_fno.py parity tests. Do not edit: the
# screener port (app/fno/selection.py) must produce exactly these results.
import pandas as pd
import datetime
import os

def load_and_clean_data(file_path: str) -> pd.DataFrame:
    """
    Loads CSV and performs basic cleaning.
    """
    try:
        df = pd.read_csv(file_path)
        
        # Standardize column names (strip whitespace)
        df.columns = df.columns.str.strip()
        
        return df
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return pd.DataFrame()

def process_bhavcopy(latest_file: str, previous_file: str) -> pd.DataFrame:
    """
    Compares two Bhavcopy files to identify signals.
    """
    # Load data
    df_latest = load_and_clean_data(latest_file)
    df_prev = load_and_clean_data(previous_file)
    
    if df_latest.empty or df_prev.empty:
        print("One or both input files are empty/invalid.")
        return pd.DataFrame()
        
    # Filter for Futures only
    # Note: Column name might vary slightly (INSTRUMENT vs INSTRUMENT_TYPE), checking for standard
    # If not found, we might need to inspect columns manually or guess
    
    req_cols = ['INSTRUMENT', 'SYMBOL', 'EXPIRY_DT', 'STRIKE_PR', 'OPTION_TYP', 'CLOSE', 'OPEN_INT']
    
    # Check for missing columns
    for col in req_cols:
        if col not in df_latest.columns:
            # Try to find a close match or specific known variations?
            # For now, let's assume standard NSE format but print error if missing
            print(f"Critical column {col} missing in latest file. Columns: {df_latest.columns}")
            return pd.DataFrame()
            
    # Filter FUTSTK and FUTIDX
    valid_instruments = ['FUTSTK', 'FUTIDX']
    df_latest = df_latest[df_latest['INSTRUMENT'].isin(valid_instruments)].copy()
    df_prev = df_prev[df_prev['INSTRUMENT'].isin(valid_instruments)].copy()
    
    # --- FIX: Filter BOTH files to their nearest (front-month) expiry BEFORE merging.
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
    # Logic based on Image:
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
        limit_oi_strong = 10
        limit_oi_normal = 7
        
        limit_price_strong = 1.0 if is_index else 2.0
        limit_price_normal = 1.0 if is_index else 1.5
        
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
        
    # Note: Nearest expiry filter is already applied before merge, so all signals_df
    # rows are already from the front-month contract. No need to re-filter here.
    final_df = signals_df.copy()
    
    # Sort: Strong Signals First
    # Custom sort order: Strong Long/Short -> Normal Long/Short
    priority_map = {
        'Strong Long Buildup': 1,
        'Strong Short Buildup': 1,
        'Long Buildup': 2,
        'Short Buildup': 2
    }
    final_df['Priority'] = final_df['Signal'].map(priority_map)
    final_df = final_df.sort_values(by=['Priority', 'OI_Change_Pct'], ascending=[True, False])
    
    return final_df

def process_watchlist(latest_file: str, previous_file: str, watchlist_path: str) -> pd.DataFrame:
    """
    Processes specific symbols from the watchlist file.
    """
    if not os.path.exists(watchlist_path):
        print(f"Watchlist file not found: {watchlist_path}")
        return pd.DataFrame()
        
    with open(watchlist_path, 'r') as f:
        watchlist_symbols = [line.strip().upper() for line in f if line.strip()]
        
    if not watchlist_symbols:
        return pd.DataFrame()

    # Load data (Redundant data loading, but simplest for now without refactoring everything)
    # Ideally main.py should load DFs once and pass them, but keeping it decoupled for now.
    df_latest = load_and_clean_data(latest_file)
    df_prev = load_and_clean_data(previous_file)
    
    if df_latest.empty or df_prev.empty:
        return pd.DataFrame()

    # Filter for FUTSTK/FUTIDX and Watchlist Symbols
    valid_instruments = ['FUTSTK', 'FUTIDX']
    df_latest = df_latest[
        (df_latest['INSTRUMENT'].isin(valid_instruments)) & 
        (df_latest['SYMBOL'].isin(watchlist_symbols))
    ].copy()
    df_prev = df_prev[df_prev['INSTRUMENT'].isin(valid_instruments)].copy()
    
    # --- FIX: Filter both files to nearest expiry BEFORE merging (same fix as process_bhavcopy).
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

if __name__ == "__main__":
    # Test logic
    pass
