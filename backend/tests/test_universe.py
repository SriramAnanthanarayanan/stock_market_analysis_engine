from datetime import date

import pytest

from app.data.universe import UniverseError, parse_fno_mktlots, parse_nifty500_csv
from tests.conftest import FNO_CSV, NIFTY_CSV

REAL_FORMAT_FNO = (
    "UNDERLYING                          ,SYMBOL    ,NOV-26     ,DEC-26     \n"
    "NIFTY 50                            ,NIFTY     ,65         ,65         \n"
    "NIFTY BANK                          ,BANKNIFTY ,30         ,30         \n"
    "Derivatives on Individual Securities,Symbol    ,NOV-26     ,DEC-26     \n"
    "ADANI ENERGY SOLUTION LTD           ,ADANIENSOL,675        ,675        \n"
    "GE VERNOVA T&D INDIA LTD            ,GVT&D     ,125        ,125        \n"
)


def test_nifty500_schema_validated():
    df = parse_nifty500_csv(NIFTY_CSV, min_rows=1)
    assert list(df["Symbol"]) == ["FCHAN", "ATRI", "RECT", "RWALK", "FAIL1"]
    with pytest.raises(UniverseError):
        parse_nifty500_csv("Name,Ticker\nA,B\n", min_rows=1)
    with pytest.raises(UniverseError):
        parse_nifty500_csv(NIFTY_CSV)  # 5 rows is not a plausible NIFTY 500 file


def test_fno_market_lot_file_parsing():
    rows = parse_fno_mktlots(REAL_FORMAT_FNO, min_active=1)
    assert [r.symbol for r in rows] == ["ADANIENSOL", "GVT&D"]  # index derivatives excluded
    rows = parse_fno_mktlots(FNO_CSV, min_active=1)
    active = {r.symbol: r.active for r in rows}
    assert active == {"FCHAN": True, "ATRI": True, "RECT": False}  # RECT has no near-month contract
    with pytest.raises(UniverseError):
        parse_fno_mktlots("UNDERLYING,SYMBOL,NOV-26\nNIFTY 50,NIFTY,65\n", min_active=1)


def test_failed_refresh_keeps_previous_universe(services_env):
    s, _ = services_env
    before = s.universe.members("nifty500", None).stocks
    res = s.universe.refresh_nifty500(text="garbage,file\n1,2\n", source="fixture")
    assert res["status"] == "error"
    after = s.universe.members("nifty500", None).stocks
    assert [x.symbol for x in after] == [x.symbol for x in before] and len(after) == 5
    assert s.universe.status()["nifty500"]["last_error"]


def test_universe_filters(services_env):
    s, _ = services_env
    sym = lambda u: [x.symbol for x in s.universe.members(u, None).stocks]  # noqa: E731
    assert sym("nifty500") == ["ATRI", "FAIL1", "FCHAN", "RECT", "RWALK"]
    assert sym("fno") == ["ATRI", "FCHAN"]
    assert sym("non_fno") == ["FAIL1", "RECT", "RWALK"]
    assert sym("all") == ["ATRI", "FAIL1", "FCHAN", "RECT", "RWALK"]


def test_fno_unknown_before_first_snapshot(services_env):
    s, _ = services_env
    first = s.universe.snapshot_dates("fno")[0]
    earlier = date.fromisoformat(first).replace(year=date.fromisoformat(first).year - 1)
    assert s.universe.fno_status("FCHAN", earlier) == "UNKNOWN"
    m = s.universe.members("fno", earlier)
    assert m.stocks == [] and any("UNKNOWN" in w for w in m.warnings)
    assert all(x.fno_status == "UNKNOWN" for x in s.universe.members("nifty500", earlier).stocks)
    assert s.universe.fno_status("FCHAN", None) == "YES"
    assert s.universe.fno_status("RECT", None) == "NO"


def test_fno_dated_snapshots_are_used_historically(services_env):
    s, _ = services_env
    later_file = FNO_CSV.replace("ASCENDING TRIANGLE LTD              ,ATRI      ,250        ,250        ,250", "")
    s.universe.refresh_fno(text=later_file, source="fixture", snapshot_date=date(2099, 1, 1))
    snap0 = date.fromisoformat(s.universe.snapshot_dates("fno")[0])
    assert s.universe.fno_status("ATRI", snap0) == "YES"  # first snapshot still applies
    assert s.universe.fno_status("ATRI", date(2099, 6, 1)) == "NO"  # later snapshot applies
