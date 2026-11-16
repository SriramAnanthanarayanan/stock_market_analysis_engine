"""F&O OI activity (docs/fno-activity.md).

Offline: synthetic NSE files (tests/fixtures_fno.py) are served by FakeFetcher;
never the network or the real database. The user's selection rule must give
exactly the results of the original Bhav copy processor
(tests/fno_original_processor.py, verbatim)."""

from __future__ import annotations

import math
from dataclasses import fields
from datetime import date

import pandas as pd
import pytest

from app.config import config_hash
from app.fno import store
from app.fno.config import DEFAULT_FNO_CONFIG, FNO_PARAM_META, KNOWN_FNO_CONFIG_HASHES, FnoConfig
from app.fno.fetch import eq_url, fo_urls
from app.fno.parse import parse_equity, parse_fo, parse_index
from app.fno.selection import front_expiry, process_bhavcopy, process_watchlist
from tests import fno_original_processor as original
from tests.fixtures_fno import BASIS, FakeFetcher, archive, equity_text, index_text, legacy_text, market, sessions, udiff_text

END = date(2026, 3, 27)
COLS = ["INSTRUMENT", "SYMBOL", "EXPIRY_DT", "CLOSE_today", "OPEN_INT_today", "CLOSE_yesterday", "OPEN_INT_yesterday", "Price_Change_Pct", "OI_Change_Pct"]
EXPECTED = {"EEE": "Strong Long Buildup", "DDD": "Strong Short Buildup", "TIE1": "Strong Long Buildup", "TIE2": "Strong Long Buildup", "AAA": "Strong Long Buildup", "FINNIFTY": "Strong Short Buildup", "NIFTY": "Long Buildup", "BBB": "Short Buildup"}


def _same(a: pd.DataFrame, b: pd.DataFrame, extra: tuple[str, ...]) -> None:
    assert len(a) == len(b)
    if a.empty:
        return
    a, b = a.reset_index(drop=True), b.reset_index(drop=True)
    for c in COLS + list(extra):
        if a[c].dtype.kind in "fi" or b[c].dtype.kind in "fi":
            x, y = a[c].astype(float), b[c].astype(float)
            assert ((x == y) | (x.isna() & y.isna()) | ((x - y).abs() < 1e-9)).all(), c
        else:
            assert a[c].astype(str).tolist() == b[c].astype(str).tolist(), c


def _legacy_files(tmp_path, m, days):
    out = {}
    for d in days:
        p = tmp_path / f"{d:%Y%m%d}_NSEFO.csv"
        p.write_text(legacy_text(d, m[d]))
        out[d] = str(p)
    return out


@pytest.fixture
def fno_env(services_env, calendar):
    s, _ = services_env
    days = sessions(calendar, END)
    files, m = archive(calendar, days)
    s.fno.fetcher = FakeFetcher(files)
    s.fno.latest_session = lambda now=None: END
    s.fno.backfill_sessions = len(days)
    job = s.fno.start_update(background=False, trigger="test")
    assert job["state"] == "done", job
    return s, days, m


# ---------------------------------------------------------------- config
def test_config_hash_registered_and_rule_values_are_the_originals():
    cfg = DEFAULT_FNO_CONFIG
    assert KNOWN_FNO_CONFIG_HASHES[cfg.version] == config_hash(cfg)
    assert {f.name for f in fields(FnoConfig)} <= set(FNO_PARAM_META)
    # Bhav copy src/processor.py literals: OI 10 / 7 %, stock price 2 / 1.5 %, index 1 / 1 %
    assert (cfg.oi_strong_pct, cfg.oi_normal_pct, cfg.price_strong_stock_pct, cfg.price_normal_stock_pct, cfg.price_strong_index_pct, cfg.price_normal_index_pct) == (10.0, 7.0, 2.0, 1.5, 1.0, 1.0)


# ---------------------------------------------------------------- selection parity
def test_selection_port_matches_original_processor(tmp_path, calendar):
    days = sessions(calendar, END)
    m = market(days)
    paths = _legacy_files(tmp_path, m, days)
    wl = tmp_path / "existing_positions.txt"
    wl.write_text("aaa\nNIFTY\n\nDDD\nXYZ\nEEE\n")
    checked = 0
    for prev, d in zip(days, days[1:]):
        o = original.process_bhavcopy(paths[d], paths[prev])
        for text_of in (legacy_text, udiff_text):
            p = process_bhavcopy(parse_fo(text_of(d, m[d])), parse_fo(text_of(prev, m[prev])))
            _same(o, p, ("Signal", "Priority"))
            ow = original.process_watchlist(paths[d], paths[prev], str(wl))
            pw = process_watchlist(parse_fo(text_of(d, m[d])), parse_fo(text_of(prev, m[prev])), ["aaa", "NIFTY", "", "DDD", "XYZ", "EEE"])
            _same(ow, pw, ("Status",))
        checked += len(o)
    assert checked == len(EXPECTED)  # only the last session has signals


def test_designed_signals_order_and_ties(tmp_path, calendar):
    days = sessions(calendar, END)
    m = market(days)
    p = process_bhavcopy(parse_fo(udiff_text(END, m[END])), parse_fo(udiff_text(days[-2], m[days[-2]])))
    assert dict(zip(p["SYMBOL"], p["Signal"])) == EXPECTED
    assert "CCC" not in set(p["SYMBOL"])  # +1.0 % is below the 1.5 % stock threshold
    assert p["Priority"].tolist() == sorted(p["Priority"].tolist())
    assert math.isinf(p.loc[p["SYMBOL"] == "EEE", "OI_Change_Pct"].iloc[0])


def test_rollover_day_has_no_signals_in_the_original_too(tmp_path, calendar):
    days = sessions(calendar, END)
    m = market(days)
    paths = _legacy_files(tmp_path, m, days)
    roll = [(a, b) for a, b in zip(days, days[1:]) if front_expiry(parse_fo(legacy_text(a, m[a]))) != front_expiry(parse_fo(legacy_text(b, m[b])))]
    assert roll, "fixture must contain an expiry rollover"
    prev, d = roll[0]
    assert original.process_bhavcopy(paths[d], paths[prev]).empty
    assert process_bhavcopy(parse_fo(legacy_text(d, m[d])), parse_fo(legacy_text(prev, m[prev]))).empty


# ---------------------------------------------------------------- parsing fixes
def test_both_fo_formats_normalise_to_the_same_futures(calendar):
    days = sessions(calendar, END)
    m = market(days)
    a, b = parse_fo(udiff_text(END, m[END])), parse_fo(legacy_text(END, m[END]))
    key = ["INSTRUMENT", "SYMBOL", "EXPIRY_DT", "CLOSE", "OPEN_INT"]
    fa = a[a["INSTRUMENT"].isin(["FUTSTK", "FUTIDX"])][key].sort_values(key[:3]).reset_index(drop=True)
    fb = b[b["INSTRUMENT"].isin(["FUTSTK", "FUTIDX"])][key].sort_values(key[:3]).reset_index(drop=True)
    pd.testing.assert_frame_equal(fa, fb, check_dtype=False)
    for df in (a, b):
        fut = df["INSTRUMENT"].isin(["FUTSTK", "FUTIDX"])
        assert df.loc[fut, "STRIKE_PR"].isna().all() and df.loc[fut, "OPTION_TYP"].isna().all()  # legacy 0 / XX -> NaN
    assert a.attrs["source"] == "udiff" and b.attrs["source"] == "legacy"
    assert a.loc[a["SYMBOL"] == "DDD", "LOT_SIZE"].iloc[0] == 1000


def test_equity_file_with_padded_values_is_read():
    days = [END]
    m = market(days)
    eq = parse_equity(equity_text(END, m[END]))
    assert set(eq["symbol"]) == {"AAA", "BBB", "CCC", "DDD", "EEE", "TIE1", "TIE2", "ZZZ"}  # EQ series only (the BE row is dropped)
    aaa = eq[eq["symbol"] == "AAA"].iloc[0]
    assert aaa["close"] > 0 and aaa["volume"] == 300_000 and aaa["deliv_per"] == 60.0
    assert pd.isna(eq.loc[eq["symbol"] == "ZZZ", "deliv_per"].iloc[0])  # "-" stays missing, never 0


def test_index_file_maps_finnifty_and_parses_commas():
    m = market([END])
    idx = parse_index(index_text(END, m[END]))
    got = dict(zip(idx["symbol"], idx["close"]))
    assert set(got) == {"NIFTY", "FINNIFTY", "BANKNIFTY"}  # "Nifty Financial Services" (the original looked for "Nifty Fin Service")
    assert got["NIFTY"] > 20000


# ---------------------------------------------------------------- service: ingest + runs
def test_ingest_backfill_and_daily_runs(fno_env):
    s, days, m = fno_env
    ok = store.sessions(s.engine, "ok")
    assert [x["trade_date"] for x in ok] == [d.isoformat() for d in days]
    assert all(x["eq_status"] == "ok" and x["idx_status"] == "ok" and x["fo_source"] == "udiff" for x in ok)
    dates = s.fno.dates()
    assert len(dates) == len(days) - 1 and dates[0]["trade_date"] == END.isoformat()
    act = s.fno.activity()
    assert act["trade_date"] == END.isoformat() and act["prev_date"] == days[-2].isoformat()
    assert [(r["symbol"], r["signal"]) for r in act["signals"]] == list(process_bhavcopy(store.futures_frame(s.engine, END), store.futures_frame(s.engine, days[-2]))[["SYMBOL", "Signal"]].itertuples(index=False, name=None))
    assert {r["symbol"]: r["signal"] for r in act["signals"]} == EXPECTED
    # nothing was downloaded twice; raw files of old sessions are cleaned up
    calls = s.fno.fetcher.calls
    assert len(calls) == len(set(calls))
    assert not list((s.settings.cache_dir / "bhavcopy").glob("*/202603*"))


def test_selection_through_database_equals_original(fno_env, tmp_path):
    s, days, m = fno_env
    paths = _legacy_files(tmp_path, m, days[-2:])
    o = original.process_bhavcopy(paths[END], paths[days[-2]])
    p = process_bhavcopy(store.futures_frame(s.engine, END), store.futures_frame(s.engine, days[-2]))
    _same(o, p, ("Signal", "Priority"))


def test_rollover_notice(fno_env):
    s, days, m = fno_env
    for d in days[1:]:
        run = store.get_run(s.engine, d, s.fno.config_hash)
        if run["latest_front_expiry"] != run["prev_front_expiry"]:
            act = s.fno.activity(d)
            assert act["signals"] == [] and "rollover" in {n["code"] for n in act["notices"]}
            return
    pytest.fail("no rollover session in the fixture")


def test_context_metrics(fno_env):
    s, days, m = fno_env
    rows = {r["symbol"]: r for r in s.fno.activity(END)["signals"]}
    aaa = rows["AAA"]["context"]
    spot = [r for r in m[END]["eq"] if r["sym"] == "AAA" and r["series"] == "EQ"][0]["close"]
    assert aaa["spot"] == round(spot, 2)
    assert aaa["basis_pct"] == pytest.approx(0.2, abs=0.02)
    assert aaa["volume_ratio"] == 3.0  # previous 20 sessions only (today included would give 2.73)
    assert aaa["delivery_pct"] == 60.0 and aaa["delivery_surge_pct"] == 50.0
    assert aaa["volume_rank_pct"] == 100.0
    assert {"high_10d", "low_10d", "high_20d", "low_20d", "dist_high_10d_pct"} <= set(aaa)
    fut_oi = {d: sum(r["oi"] for r in m[d]["fut"] if r["sym"] == "AAA") for d in days}
    assert aaa["oi_all_expiries"] == round(fut_oi[END])
    assert aaa["oi_change_5d_pct"] == pytest.approx((fut_oi[END] - fut_oi[days[-6]]) / fut_oi[days[-6]] * 100, abs=0.01)
    assert aaa["oi_build_streak"] == len(days) - 1  # combined OI rose every session, across the rollover
    assert aaa["basis_trend"] in ("Expanding", "Contracting", "Neutral") and "basis_avg" in aaa
    # option chain: walls at +/- 2 strikes, ATM IV by Brenner-Subrahmanyam on the 4-day expiry
    atm = round(spot / 10) * 10
    assert aaa["max_call_strike"] == atm + 20 and aaa["max_put_strike"] == atm - 20
    near = [r for r in m[END]["opt"] if r["sym"] == "AAA" and r["exp"] == min(r2["exp"] for r2 in m[END]["opt"])]
    strike = min({r["strike"] for r in near}, key=lambda k: abs(k - spot))
    prices = [r["settle"] for r in near if r["strike"] == strike]
    days_left = (near[0]["exp"] - END).days
    assert aaa["atm_iv"] == pytest.approx(round(sum(prices) / len(prices) * math.sqrt(2 * math.pi) / (spot * math.sqrt(days_left / 365)) * 100, 2))
    assert rows["FINNIFTY"]["context"]["spot"] > 0  # corrected index name
    assert rows["NIFTY"]["chart"] == {"instrument": "index", "symbol": "NIFTY 50"} and rows["AAA"]["chart"] == {"instrument": "stock", "symbol": "AAA"}


def test_flags_never_change_the_selection(fno_env):
    s, days, m = fno_env
    rows = {r["symbol"]: r for r in s.fno.activity(END)["signals"]}
    assert rows["DDD"]["signal"] == "Strong Short Buildup"
    assert [f["code"] for f in rows["DDD"]["flags"]] == ["lot_size_changed"]
    assert rows["EEE"]["oi_change_infinite"] is True and rows["EEE"]["oi_change_pct"] is None
    assert "prev_oi_zero" in {f["code"] for f in rows["EEE"]["flags"]}
    assert all(not r["flags"] for k, r in rows.items() if k not in ("DDD", "EEE"))


def test_rerun_is_idempotent(fno_env):
    s, days, m = fno_env
    before = s.fno.activity(END)["signals"]
    s.fno.compute_run(END)
    s.fno.compute_run(END)
    after = s.fno.activity(END)["signals"]
    assert before == after
    assert {r["symbol"]: r["context"]["oi_build_streak"] for r in after}["AAA"] == len(days) - 1  # the original read 0 after a same-day rerun


def test_watchlist_uses_the_same_rule_and_basis_needs_the_same_contract(fno_env, tmp_path):
    s, days, m = fno_env
    s.fno.set_watchlist(["AAA", "NIFTY", "DDD"])
    act = s.fno.activity(END)
    paths = _legacy_files(tmp_path, m, days[-2:])
    wl = tmp_path / "wl.txt"
    wl.write_text("AAA\nNIFTY\nDDD\n")
    o = original.process_watchlist(paths[END], paths[days[-2]], str(wl)).sort_values("SYMBOL")
    assert [(r["symbol"], r["signal"]) for r in act["watchlist"]] == list(zip(o["SYMBOL"], o["Status"]))
    # Two sessions after the rollover the 5-day basis average uses the new front contract's own
    # history (it traded as the next month before), not the expired contract (original behaviour).
    roll = next(d for d in days[1:] if store.get_run(s.engine, d, s.fno.config_hash)["latest_front_expiry"] != store.get_run(s.engine, d, s.fno.config_hash)["prev_front_expiry"])
    i3 = days.index(roll) + 2
    d3 = days[i3]
    ctx = {r["symbol"]: r["context"] for r in s.fno.activity(d3)["watchlist"]}["AAA"]
    contract = min(r["exp"] for r in m[d3]["fut"])

    def basis(p, front_only):
        fut = [r for r in m[p]["fut"] if r["sym"] == "AAA"]
        close = min(fut, key=lambda r: r["exp"])["close"] if front_only else next(r["close"] for r in fut if r["exp"] == contract)
        return close - next(r["close"] for r in m[p]["eq"] if r["sym"] == "AAA" and r["series"] == "EQ")

    same = sum(basis(p, False) for p in days[i3 - 5 : i3]) / 5
    mixed = sum(basis(p, True) for p in days[i3 - 5 : i3]) / 5
    assert ctx["basis_avg"] == pytest.approx(same, abs=0.006) and abs(same - mixed) > 0.5


def test_missing_fo_file_and_missing_equity_are_reported(services_env, calendar):
    s, _ = services_env
    days = sessions(calendar, END, n=8)
    files, m = archive(calendar, days)
    gap = days[3]
    for _, url in fo_urls(gap):
        files.pop(url, None)
    files.pop(eq_url(days[5]))
    s.fno.fetcher = FakeFetcher(files)
    s.fno.latest_session = lambda now=None: END
    assert s.fno.start_update(background=False, sessions=len(days))["state"] == "done"
    assert store.get_session(s.engine, gap)["fo_status"] == "missing"
    after_gap = s.fno.activity(days[4])
    assert after_gap["prev_date"] == days[2].isoformat() and "gap" in {n["code"] for n in after_gap["notices"]}
    eq_missing = s.fno.activity(days[5])
    assert "equity_missing" in {n["code"] for n in eq_missing["notices"]}
    # a second update does not retry an old missing session
    s.fno.fetcher.calls.clear()
    s.fno.start_update(background=False, sessions=len(days))
    assert s.fno.fetcher.calls == []


# ---------------------------------------------------------------- API
def test_api(client_env, calendar):
    client, s, _, _ = client_env
    days = sessions(calendar, END, n=25)
    files, _ = archive(calendar, days)
    s.fno.fetcher = FakeFetcher(files)
    s.fno.latest_session = lambda now=None: END
    assert client.get("/api/fno/activity").json()["trade_date"] is None
    assert s.fno.start_update(background=False, sessions=len(days))["state"] == "done"
    st = client.get("/api/fno/status").json()
    assert st["sessions"]["stored"] == len(days) and st["config"]["hash"] == s.fno.config_hash and len(st["sources"]) == 4
    act = client.get("/api/fno/activity").json()  # infinite OI change must serialise
    assert act["trade_date"] == END.isoformat() and {r["symbol"] for r in act["signals"]} == set(EXPECTED)
    assert client.get(f"/api/fno/activity?date={days[-2].isoformat()}").status_code == 200
    assert client.get("/api/fno/activity?date=2026-01-01").status_code == 404
    assert client.get("/api/fno/dates").json()["dates"][0]["trade_date"] == END.isoformat()
    assert {p["name"] for p in client.get("/api/fno/config").json()["params"]} >= {"oi_strong_pct", "price_normal_index_pct"}
    assert client.put("/api/fno/watchlist", json={"symbols": ["aaa", "NOPE"]}).status_code == 422
    assert client.put("/api/fno/watchlist", json={"symbols": ["bad symbol!"]}).status_code == 422
    r = client.put("/api/fno/watchlist", json={"symbols": ["aaa", "NIFTY", "AAA"]})
    assert r.status_code == 200 and r.json()["symbols"] == ["AAA", "NIFTY"]
    act = client.get("/api/fno/activity").json()
    assert [x["symbol"] for x in act["watchlist"]] == ["AAA", "NIFTY"] and act["watchlist_symbols"] == ["AAA", "NIFTY"]
    assert client.post("/api/fno/refresh", json={"sessions": 0}).status_code == 422
