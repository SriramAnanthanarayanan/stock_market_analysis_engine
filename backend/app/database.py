"""SQLite persistence with SQLAlchemy Core (no ORM).

Schema changes are applied through numbered, append-only migrations recorded in
``schema_version``. Normal startup never drops or recreates user data.

Timestamps are stored as ISO-8601 strings in Asia/Kolkata (``+05:30``) so they
sort lexicographically and read naturally.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Column,
    Engine,
    Float,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    create_engine,
    event,
    func,
    select,
)
from sqlalchemy.engine import Connection

from .timeutil import IST

log = logging.getLogger(__name__)

metadata = MetaData()

schema_version = Table(
    "schema_version",
    metadata,
    Column("version", Integer, primary_key=True),
    Column("description", Text, nullable=False),
    Column("applied_at", String, nullable=False),
)

config_versions = Table(
    "config_versions",
    metadata,
    Column("config_hash", String, primary_key=True),
    Column("version", String, nullable=False),
    Column("params_json", Text, nullable=False),
    Column("created_at", String, nullable=False),
)

stocks = Table(
    "stocks",
    metadata,
    Column("symbol", String, primary_key=True),
    Column("company", String),
    Column("industry", String),
    Column("isin", String),
    Column("series", String),
    Column("in_nifty500", Integer, nullable=False, default=0),
    Column("first_seen", String),
    Column("last_seen", String),
    Column("updated_at", String),
)

universe_snapshots = Table(
    "universe_snapshots",
    metadata,
    Column("list_name", String, nullable=False),
    Column("snapshot_date", String, nullable=False),
    Column("symbol", String, nullable=False),
    Column("info_json", Text),
    PrimaryKeyConstraint("list_name", "snapshot_date", "symbol"),
)

universe_refresh_log = Table(
    "universe_refresh_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("list_name", String, nullable=False),
    Column("started_at", String, nullable=False),
    Column("finished_at", String),
    Column("status", String, nullable=False),
    Column("rows", Integer),
    Column("source", String),
    Column("error", Text),
)

market_data = Table(
    "market_data",
    metadata,
    Column("symbol", String, nullable=False),
    Column("interval", String, nullable=False),
    Column("provider", String, nullable=False),
    Column("file_path", String),
    Column("first_ts", String),
    Column("last_ts", String),
    Column("rows", Integer),
    Column("last_fetch_at", String),
    Column("last_full_fetch_at", String),
    Column("status", String),
    Column("error", Text),
    Column("notes", Text),
    Column("price_basis", String),  # E1: raw_split_adjusted | adjusted_legacy (NULL = legacy)
    # History depth the cache was last fully downloaded for (provider period such as
    # "max"/"5y"; "max" for NSE index caches after their backward backfill). A cache
    # whose depth differs from the provider's preferred period is re-downloaded.
    Column("history_period", String),
    PrimaryKeyConstraint("symbol", "interval"),
)

download_errors = Table(
    "download_errors",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("scan_id", String),
    Column("symbol", String, nullable=False),
    Column("interval", String, nullable=False),
    Column("occurred_at", String, nullable=False),
    Column("error", Text, nullable=False),
)

scans = Table(
    "scans",
    metadata,
    Column("scan_id", String, primary_key=True),
    Column("universe", String, nullable=False),
    Column("timeframe", String, nullable=False),
    Column("asof", String),
    Column("patterns_json", Text),
    Column("config_hash", String, nullable=False),
    Column("config_version", String, nullable=False),
    Column("trigger", String, nullable=False),
    Column("state", String, nullable=False),
    Column("phase", String),
    Column("created_at", String, nullable=False),
    Column("started_at", String),
    Column("finished_at", String),
    Column("total_symbols", Integer, default=0),
    Column("processed_symbols", Integer, default=0),
    Column("successful_symbols", Integer, default=0),
    Column("failed_symbols", Integer, default=0),
    Column("detection_count", Integer, default=0),
    Column("data_cutoff", String),
    Column("message", Text),
    Column("warnings_json", Text),
    Column("price_basis", String),  # E1
    Column("tech_config_version", String),  # E1: TechnicalConfig used for indicator/candle/breakout results
    Column("tech_config_hash", String),
    Column("stage_config_version", String),  # E3: StageConfig (weekly scans)
    Column("stage_config_hash", String),
)
Index("ix_scans_lookup", scans.c.timeframe, scans.c.universe, scans.c.asof, scans.c.state)

scan_symbols = Table(
    "scan_symbols",
    metadata,
    Column("scan_id", String, nullable=False),
    Column("symbol", String, nullable=False),
    Column("status", String, nullable=False),
    Column("error", Text),
    Column("bars", Integer),
    Column("last_candle_time", String),
    Column("fno_status", String),
    Column("in_nifty500", Integer),
    Column("data_note", Text),
    PrimaryKeyConstraint("scan_id", "symbol"),
)

detections = Table(
    "detections",
    metadata,
    Column("scan_id", String, nullable=False),
    Column("detection_id", String, nullable=False),
    Column("symbol", String, nullable=False),
    Column("company", String),
    Column("fno_status", String),
    Column("in_nifty500", Integer),
    Column("timeframe", String, nullable=False),
    Column("pattern", String, nullable=False),
    Column("category", String, nullable=False),
    Column("status", String, nullable=False),
    Column("status_reason", String),
    Column("quality", Float, nullable=False),
    Column("quality_label", String, nullable=False),
    Column("formation_start", String, nullable=False),
    Column("formation_end", String, nullable=False),
    Column("formation_length", Integer, nullable=False),
    Column("upper_touches", Integer),
    Column("lower_touches", Integer),
    Column("width_pct", Float),
    Column("move_pct", Float),
    Column("volume_ratio", Float),
    Column("last_close", Float),
    Column("last_candle_time", String),
    Column("metrics_json", Text),
    Column("quality_json", Text),
    Column("explanation_json", Text),
    Column("config_hash", String, nullable=False),
    Column("config_version", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("track_id", String),  # P3 (added by migration 3 on existing databases)
    PrimaryKeyConstraint("scan_id", "detection_id"),
)
Index("ix_detections_symbol_tf", detections.c.symbol, detections.c.timeframe)
Index("ix_detections_pattern", detections.c.pattern)
Index("ix_detections_detection_id", detections.c.detection_id)

pattern_geometry = Table(
    "pattern_geometry",
    metadata,
    Column("scan_id", String, nullable=False),
    Column("detection_id", String, nullable=False),
    Column("schema_version", String, nullable=False),
    Column("geometry_json", Text, nullable=False),
    PrimaryKeyConstraint("scan_id", "detection_id"),
)

lifecycle_events = Table(
    "lifecycle_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("scan_id", String, nullable=False),
    Column("detection_id", String, nullable=False),
    Column("event_time", String, nullable=False),
    Column("event", String, nullable=False),
    Column("status", String),
    Column("detail_json", Text),
)
Index("ix_lifecycle_detection", lifecycle_events.c.scan_id, lifecycle_events.c.detection_id)

V1_TABLES = [
    config_versions,
    stocks,
    universe_snapshots,
    universe_refresh_log,
    market_data,
    download_errors,
    scans,
    scan_symbols,
    detections,
    pattern_geometry,
    lifecycle_events,
]


sr_zones = Table(
    "sr_zones",
    metadata,
    Column("scan_id", String, nullable=False),
    Column("symbol", String, nullable=False),
    Column("timeframe", String, nullable=False),
    Column("zone_id", String, nullable=False),
    Column("p_low", Float, nullable=False),
    Column("p_high", Float, nullable=False),
    Column("role", String, nullable=False),
    Column("status", String, nullable=False),
    Column("side", String, nullable=False),
    Column("touches", Integer, nullable=False),
    Column("distance_pct", Float),
    Column("distance_atr", Float),
    Column("first_touch_time", String),
    Column("last_touch_time", String),
    Column("broken_time", String),
    Column("retest_time", String),
    Column("detail_json", Text),
    PrimaryKeyConstraint("scan_id", "symbol", "zone_id"),
)
Index("ix_sr_zones_symbol", sr_zones.c.scan_id, sr_zones.c.symbol)


# --- P3: tracking across scans, labels, evaluation -------------------------

pattern_tracks = Table(
    "pattern_tracks",
    metadata,
    Column("track_id", String, primary_key=True),
    Column("symbol", String, nullable=False),
    Column("timeframe", String, nullable=False),
    Column("pattern", String, nullable=False),
    Column("family", String, nullable=False),
    Column("state", String, nullable=False),  # active | frozen | closed
    Column("status", String, nullable=False),
    Column("status_reason", String),
    Column("closed_reason", String),  # disappeared | invalidated | archived | data_changed | config_changed
    Column("first_scan_id", String, nullable=False),
    Column("last_scan_id", String, nullable=False),
    Column("first_detected_at", String, nullable=False),
    Column("last_detected_at", String, nullable=False),
    Column("first_detection_id", String, nullable=False),
    Column("current_detection_id", String, nullable=False),
    Column("formation_start", String, nullable=False),
    Column("formation_end", String, nullable=False),
    Column("quality_first", Float),
    Column("quality_last", Float),
    Column("quality_max", Float),
    Column("frozen_at", String),
    Column("breakout_time", String),
    Column("breakout_direction", String),
    Column("breakout_close", Float),
    Column("invalidated_time", String),
    Column("archived_time", String),
    Column("closed_time", String),
    Column("config_hash", String, nullable=False),
    Column("config_version", String, nullable=False),
    Column("geometry_json", Text, nullable=False),  # latest (or frozen) geometry snapshot
    Column("detection_json", Text, nullable=False),  # latest (or frozen) detection snapshot
    Column("outcome_json", Text),
    Column("updated_at", String, nullable=False),
    Column("price_basis", String),  # E1
)
Index("ix_tracks_symbol_tf", pattern_tracks.c.symbol, pattern_tracks.c.timeframe, pattern_tracks.c.state)

track_events = Table(
    "track_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("track_id", String, nullable=False),
    Column("scan_id", String, nullable=False),
    Column("event_time", String, nullable=False),
    Column("event", String, nullable=False),
    Column("status", String),
    Column("quality", Float),
    Column("detection_id", String),
    Column("detail_json", Text),
)
Index("ix_track_events_track", track_events.c.track_id)

labels = Table(
    "labels",
    metadata,
    Column("label_id", String, primary_key=True),
    Column("symbol", String, nullable=False),
    Column("timeframe", String, nullable=False),
    Column("pattern", String, nullable=False),
    Column("verdict", String, nullable=False),  # valid | invalid | near_miss | false_detection
    Column("asof", String, nullable=False),  # chart date the label describes (IST)
    Column("formation_start", String, nullable=False),
    Column("formation_end", String, nullable=False),
    Column("upper_line_json", Text),
    Column("lower_line_json", Text),
    Column("swing_points_json", Text),
    Column("dataset", String, nullable=False),  # tuning | validation
    Column("detection_id", String),
    Column("notes", Text),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("price_basis", String),  # E1: basis of the line prices
)
Index("ix_labels_symbol_tf", labels.c.symbol, labels.c.timeframe)

evaluation_runs = Table(
    "evaluation_runs",
    metadata,
    Column("run_id", String, primary_key=True),
    Column("created_at", String, nullable=False),
    Column("kind", String, nullable=False),  # evaluate | compare | sweep
    Column("config_hash", String, nullable=False),
    Column("config_version", String, nullable=False),
    Column("overrides_json", Text),
    Column("label_count", Integer, nullable=False),
    Column("summary_json", Text, nullable=False),
    Column("details_json", Text),
)

P3_TABLES = [pattern_tracks, track_events, labels, evaluation_runs]

# ------------------------------------------------------------------ E1: technical analysis
# Latest indicator state per symbol and scan (EMA/RSI/ATR values, alignment, flags).
tech_snapshots = Table(
    "tech_snapshots",
    metadata,
    Column("scan_id", String, nullable=False),
    Column("symbol", String, nullable=False),
    Column("timeframe", String, nullable=False),
    Column("company", String),
    Column("fno_status", String),
    Column("in_nifty500", Integer),
    Column("last_candle_time", String),
    Column("close", Float),
    Column("status", String, nullable=False),  # ok | insufficient_history
    Column("metrics_json", Text, nullable=False),
    Column("config_version", String, nullable=False),
    Column("config_hash", String, nullable=False),
    PrimaryKeyConstraint("scan_id", "symbol"),
)

# Candlestick, breakout, EMA crossover and RSI divergence events found by a scan.
tech_signals = Table(
    "tech_signals",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("scan_id", String, nullable=False),
    Column("symbol", String, nullable=False),
    Column("company", String),
    Column("fno_status", String),
    Column("in_nifty500", Integer),
    Column("timeframe", String, nullable=False),
    Column("category", String, nullable=False),  # candlestick | breakout | ema_cross | rsi_divergence
    Column("kind", String, nullable=False),  # e.g. bullish_marubozu, breakout_high_10d, ema_cross_20_50, rsi_divergence
    Column("direction", String, nullable=False),  # bullish | bearish
    Column("signal_time", String, nullable=False),  # bar start (IST) of the confirming candle
    Column("age_bars", Integer, nullable=False),  # completed bars since signal_time (0 = latest bar)
    Column("status", String, nullable=False),
    Column("confirm_closes", Integer),  # breakouts: 1 or 2
    Column("metrics_json", Text, nullable=False),
    Column("config_version", String, nullable=False),
    Column("config_hash", String, nullable=False),
)
Index("ix_tech_signals_scan", tech_signals.c.scan_id, tech_signals.c.category)
Index("ix_tech_signals_symbol", tech_signals.c.symbol, tech_signals.c.timeframe)

E1_TABLES = [tech_snapshots, tech_signals]

# ------------------------------------------------------------------ E2: indices and RRG
index_registry = Table(
    "index_registry",
    metadata,
    Column("code", String, primary_key=True),  # official NSE index symbol (allIndices.indexSymbol)
    Column("name", String, nullable=False),  # official index name
    Column("short_name", String, nullable=False),
    Column("category", String, nullable=False),  # broad | sector | thematic | strategy | fixed_income
    Column("subcategory", String),
    Column("nse_group", String, nullable=False),  # catalogue group as published by NSE
    Column("index_type", String, nullable=False),  # equity | volatility | leveraged | inverse | dividend_points | currency | fixed_income
    Column("return_basis", String, nullable=False),  # PR (price return)
    Column("active", Integer, nullable=False),
    Column("analysis_enabled", Integer, nullable=False),  # equity price indices only
    Column("source_url", String, nullable=False),
    Column("discovered_at", String, nullable=False),
    Column("last_seen_at", String, nullable=False),
    Column("reference_json", Text),  # official reference closes (previous day / 1W / 30D / 1Y ago)
    Column("data_provider", String),  # daily/weekly data source
    Column("data_status", String, nullable=False),  # pending | ok | unavailable | error
    Column("data_status_reason", Text),
    Column("data_verified", String),  # result of the check against official reference closes
    Column("last_refresh_at", String),
    Column("first_date", String),
    Column("last_date", String),
    Column("rows", Integer),
    Column("intraday_provider", String),
    Column("intraday_ticker", String),
    Column("intraday_status", String),  # verified | failed | unavailable
    Column("intraday_detail", Text),
    Column("intraday_checked_at", String),
)

rrg_runs = Table(
    "rrg_runs",
    metadata,
    Column("run_id", String, primary_key=True),
    Column("created_at", String, nullable=False),
    Column("benchmark", String, nullable=False),
    Column("asof", String),
    Column("last_week", String),
    Column("params_json", Text, nullable=False),
    Column("config_version", String, nullable=False),
    Column("config_hash", String, nullable=False),
    Column("data_basis", String, nullable=False),
    Column("result_json", Text, nullable=False),
)
Index("ix_rrg_runs_lookup", rrg_runs.c.benchmark, rrg_runs.c.asof, rrg_runs.c.last_week)

E2_TABLES = [index_registry, rrg_runs]

# ------------------------------------------------------------------ E3: Stage Analysis
stage_results = Table(
    "stage_results",
    metadata,
    Column("scan_id", String, nullable=False),
    Column("symbol", String, nullable=False),
    Column("company", String),
    Column("fno_status", String),
    Column("in_nifty500", Integer),
    Column("week", String),  # latest completed week evaluated
    Column("stage", String, nullable=False),
    Column("stage_since", String),
    Column("previous_stage", String),
    Column("rank_json", Text),
    Column("metrics_json", Text, nullable=False),
    Column("gates_json", Text, nullable=False),
    Column("reasons_json", Text, nullable=False),
    Column("config_version", String, nullable=False),
    Column("config_hash", String, nullable=False),
    PrimaryKeyConstraint("scan_id", "symbol"),
)
Index("ix_stage_results_stage", stage_results.c.scan_id, stage_results.c.stage)

E3_TABLES = [stage_results]


def _migration_1(conn: Connection) -> None:
    metadata.create_all(conn, tables=V1_TABLES, checkfirst=True)


def _migration_2(conn: Connection) -> None:
    metadata.create_all(conn, tables=[sr_zones], checkfirst=True)


def _migration_3(conn: Connection) -> None:
    metadata.create_all(conn, tables=P3_TABLES, checkfirst=True)
    cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(detections)")}
    if "track_id" not in cols:
        conn.exec_driver_sql("ALTER TABLE detections ADD COLUMN track_id VARCHAR")


def _add_column(conn: Connection, table: str, column: str, ddl: str = "VARCHAR") -> None:
    cols = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _migration_4(conn: Connection) -> None:
    """E1: price basis tags + technical-analysis tables.

    Existing caches, scans and tracks were built on dividend-adjusted OHLC; they
    are tagged ``adjusted_legacy`` (never rewritten). Open tracks are closed as
    ``data_changed`` because their frozen geometry is on the old basis; caches
    are re-downloaded on the raw basis by the next refresh."""
    metadata.create_all(conn, tables=E1_TABLES, checkfirst=True)
    for table in ("market_data", "scans", "pattern_tracks", "labels"):
        _add_column(conn, table, "price_basis")
    _add_column(conn, "scans", "tech_config_version")
    _add_column(conn, "scans", "tech_config_hash")
    legacy = "adjusted_legacy"
    conn.exec_driver_sql("UPDATE market_data SET price_basis = ? WHERE price_basis IS NULL", (legacy,))
    conn.exec_driver_sql("UPDATE scans SET price_basis = ? WHERE price_basis IS NULL", (legacy,))
    conn.exec_driver_sql("UPDATE labels SET price_basis = ? WHERE price_basis IS NULL", (legacy,))
    now = now_ist_iso()
    rows = conn.exec_driver_sql("SELECT track_id, status FROM pattern_tracks WHERE state IN ('active', 'frozen')").fetchall()
    for track_id, status in rows:
        conn.exec_driver_sql(
            "INSERT INTO track_events (track_id, scan_id, event_time, event, status, quality, detection_id, detail_json) VALUES (?, 'migration-4', ?, 'closed', ?, NULL, NULL, ?)",
            (track_id, now, status, dumps({"reason": "price basis changed from dividend-adjusted to raw OHLC (E1); frozen geometry is not comparable"})),
        )
    conn.exec_driver_sql(
        "UPDATE pattern_tracks SET state = 'closed', closed_reason = 'data_changed', closed_time = ?, updated_at = ? WHERE state IN ('active', 'frozen')",
        (now, now),
    )
    conn.exec_driver_sql("UPDATE pattern_tracks SET price_basis = ? WHERE price_basis IS NULL", (legacy,))


def _migration_5(conn: Connection) -> None:
    metadata.create_all(conn, tables=E2_TABLES, checkfirst=True)


def _migration_6(conn: Connection) -> None:
    metadata.create_all(conn, tables=E3_TABLES, checkfirst=True)
    _add_column(conn, "scans", "stage_config_version")
    _add_column(conn, "scans", "stage_config_hash")


def _migration_7(conn: Connection) -> None:
    """History depth tag on market_data.

    Existing caches are tagged with the depth they were downloaded for before
    this migration (Yahoo: 1h 2y, 1d 5y, 1wk max; NSE index caches: 10 years),
    so the next refresh re-downloads only the ones that are now deeper
    (stock daily -> max) and backfills index caches to the start of NSE history."""
    _add_column(conn, "market_data", "history_period")
    conn.exec_driver_sql(
        "UPDATE market_data SET history_period = CASE "
        "WHEN symbol LIKE 'IDX:%' THEN '10y' "
        "WHEN interval = '1h' THEN '2y' WHEN interval = '1wk' THEN 'max' ELSE '5y' END "
        "WHERE history_period IS NULL"
    )


# Append-only. Never edit a released migration; add a new one instead.
MIGRATIONS: list[tuple[int, str, Callable[[Connection], None]]] = [
    (1, "initial schema: universe, market data metadata, scans, detections, geometry, lifecycle", _migration_1),
    (2, "support/resistance zones (P2)", _migration_2),
    (3, "pattern tracks, track events, labels, evaluation runs; detections.track_id (P3)", _migration_3),
    (4, "price basis tags (raw vs dividend-adjusted), technical snapshots and signals (E1)", _migration_4),
    (5, "NSE index registry and RRG runs (E2)", _migration_5),
    (6, "Stage Analysis results; scans.stage_config_version/hash (E3)", _migration_6),
    (7, "market_data.history_period: history depth of each cache (full-history backfill)", _migration_7),
]
SCHEMA_VERSION = MIGRATIONS[-1][0]


def now_ist_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def make_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn: Any, _record: Any) -> None:  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def migrate(engine: Engine) -> int:
    """Apply pending migrations. Returns the resulting schema version."""
    with engine.begin() as conn:
        schema_version.create(conn, checkfirst=True)
        current = conn.execute(select(func.max(schema_version.c.version))).scalar() or 0
        for version, description, fn in MIGRATIONS:
            if version <= current:
                continue
            log.info("Applying schema migration %s: %s", version, description)
            fn(conn)
            conn.execute(
                schema_version.insert().values(version=version, description=description, applied_at=now_ist_iso())
            )
            current = version
    return current


def current_schema_version(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.max(schema_version.c.version))).scalar() or 0


def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), default=_json_default)


def loads(text: str | None) -> Any:
    if text is None:
        return None
    return json.loads(text)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    try:
        import numpy as np

        if isinstance(obj, np.generic):
            return obj.item()
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    except Exception:  # pragma: no cover
        pass
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serialisable")
