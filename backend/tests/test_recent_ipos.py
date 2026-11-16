"""Recent-IPO universe (2026-11-16, user: "add recent IPOs, last 3 years; download all
the newly listed IPO data"): NSE past public issues + NSE equity list."""

from datetime import date, timedelta

import pytest

from app import database as db
from app.data.universe import UniverseError, adjusted_issue_price, checked_issue_price, parse_past_issues, recent_ipo_set, years_before
from app.timeutil import now_ist

TODAY = now_ist().date()


def nse(d: date) -> str:
    return d.strftime("%d-%b-%Y").upper()


def issue(sym: str, listed: date | None, typ: str = "Equity", price: str = "100") -> dict:
    return {
        "companyName": f"{sym.title()} Limited",
        "ipoStartDate": nse(listed - timedelta(days=7)) if listed else "01-JAN-2020",
        "ipoEndDate": nse(listed - timedelta(days=5)) if listed else "03-JAN-2020",
        "issuePrice": price,
        "listingDate": nse(listed) if listed else "-",
        "priceRange": f"Rs.{price}",
        "securityType": typ,
        "symbol": sym,
    }


D_NEW = TODAY - timedelta(days=100)
D_SME = TODAY - timedelta(days=300)
D_OLD = years_before(TODAY, 4)
D_RW = TODAY - timedelta(days=500)

PAYLOAD = [
    issue("NEWIPO", D_NEW, price="1,250"),  # mainboard IPO, listed 100 days ago -> member
    issue("OLDIPO", D_OLD),  # mainboard IPO of 4 years ago -> outside the window today
    issue("FPOCO", TODAY - timedelta(days=200)),  # an FPO: the stock was listed 10 years earlier
    issue("SMEMOVED", D_SME, typ="SME"),  # SME IPO that moved to the main board -> member
    issue("SMEONLY", TODAY - timedelta(days=50), typ="SME"),  # still on NSE Emerge -> not in the equity list
    issue("BZCO", TODAY - timedelta(days=150)),  # BZ series (non-compliant) -> left out
    issue("RWALK", D_RW, price="50"),  # a NIFTY 500 fixture stock that is also a recent IPO
    issue("DEBT1", TODAY - timedelta(days=30), typ="DEBT"),
    issue("INVIT1", TODAY - timedelta(days=30), typ="InvIT"),
    issue("WITHDRAWN", None),  # no listing date (withdrawn / postponed)
]
EQUITY = "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE\n" + "".join(
    f"{s},{s.title()} Ltd,{ser},{nse(d)},10\n"
    for s, ser, d in (
        ("NEWIPO", "EQ", D_NEW),
        ("OLDIPO", "EQ", D_OLD),
        ("FPOCO", "EQ", years_before(TODAY, 10)),
        ("SMEMOVED", "EQ", TODAY - timedelta(days=30)),  # migration date after the SME IPO
        ("BZCO", "BZ", TODAY - timedelta(days=150)),
        ("RWALK", "EQ", D_RW),
        ("ZZZ", "EQ", date(2001, 1, 1)),
    )
)


def load(s, **kw):
    return s.universe.refresh_recent_ipos(payload=PAYLOAD, equity_text=EQUITY, source="fixture", min_rows=1, equity_min_rows=1, **kw)


def test_parse_past_issues():
    rows = parse_past_issues(PAYLOAD, min_rows=1)
    by = {r.symbol: r for r in rows}
    assert set(by) == {"NEWIPO", "OLDIPO", "FPOCO", "SMEMOVED", "SMEONLY", "BZCO", "RWALK"}  # debt / InvIT / no listing date skipped
    assert by["NEWIPO"].issue_price == 1250.0 and by["NEWIPO"].listing_date == D_NEW and by["SMEMOVED"].security_type == "SME"
    with pytest.raises(UniverseError):
        parse_past_issues(PAYLOAD, min_rows=50)
    with pytest.raises(UniverseError):
        parse_past_issues({"unexpected": 1}, min_rows=1)


def test_issue_price_must_fit_the_price_band():
    assert checked_issue_price(39.0, "Rs.37 to Rs.39") == (39.0, None)
    assert checked_issue_price(1250.0, "Rs.1,190 to Rs.1,250") == (1250.0, None)
    assert checked_issue_price(35.0, "Rs.35") == (35.0, None)
    assert checked_issue_price(100.0, None) == (100.0, None)  # no band to check against
    price, note = checked_issue_price(783.05, "Rs.37 to Rs.39")  # live KRISHNADEF row of 2026-11-16
    assert price is None and "outside the price band" in note
    assert checked_issue_price(None, "Rs.37 to Rs.39")[0] is None


def test_recent_ipo_membership(services_env):
    s, _ = services_env
    res = load(s)
    assert res["status"] == "ok" and res["issues"] == 7 and res["recent_ipos"] == 3
    assert s.universe.recent_ipo_symbols() == {"NEWIPO", "SMEMOVED", "RWALK"}
    sym = lambda u, asof=None: [x.symbol for x in s.universe.members(u, asof).stocks]  # noqa: E731
    assert sym("ipo") == ["NEWIPO", "RWALK", "SMEMOVED"]
    assert sym("all") == ["ATRI", "FAIL1", "FCHAN", "NEWIPO", "RECT", "RWALK", "SMEMOVED"]
    assert sym("nifty500") == ["ATRI", "FAIL1", "FCHAN", "RECT", "RWALK"]  # unchanged
    assert sym("non_fno") == ["FAIL1", "RECT", "RWALK"]  # NIFTY 500 non-F&O only, as before
    info = {x.symbol: x for x in s.universe.members("all", None).stocks}
    assert not info["NEWIPO"].in_nifty500 and info["NEWIPO"].fno_status == "NO" and info["NEWIPO"].company == "Newipo Ltd"
    # as of 3.5 years ago: the 4-year-old IPO was recent then, today's were not listed yet
    past = years_before(TODAY, 3) - timedelta(days=180)
    assert recent_ipo_set(s.engine, past) == {"OLDIPO"}
    m = s.universe.members("ipo", past)
    assert [x.symbol for x in m.stocks] == ["OLDIPO"] and any("delisted" in w for w in m.warnings)


def test_ipo_listing_dates_survive_equity_list_refresh(services_env):
    s, _ = services_env
    load(s)
    assert s.universe.listing_date("SMEMOVED") == (D_SME, "nse_ipo")  # the SME IPO date, not the main-board migration date
    assert s.universe.listing_date("RWALK") == (D_RW, "nse_ipo")
    s.universe.refresh_listing_dates(text=EQUITY, source="fixture", min_rows=1)
    assert s.universe.listing_date("SMEMOVED") == (D_SME, "nse_ipo")
    assert s.universe.listing_date("ZZZ") == (None, None)  # not in the universe: no stock row
    one = s.universe.ipo_issue("NEWIPO")
    assert one["issue_price"] == 1250.0 and one["mainboard"] == 1 and one["equity_series"] == "EQ"
    fpo = s.universe.ipo_issue("FPOCO")
    assert fpo["mainboard"] == 0  # equity-list listing 10 years before the issue: an FPO, not an IPO


def test_failed_ipo_refresh_keeps_previous_list(services_env):
    s, _ = services_env
    load(s)
    bad = s.universe.refresh_recent_ipos(payload=[], equity_text=EQUITY, source="fixture", min_rows=1, equity_min_rows=1)
    assert bad["status"] == "error"
    assert s.universe.recent_ipo_symbols() == {"NEWIPO", "SMEMOVED", "RWALK"}
    st = s.universe.status()["recent_ipos"]
    assert st["last_error"] and st["last_success_rows"] == 3


def test_refresh_is_idempotent_and_replaces_the_list(services_env):
    s, _ = services_env
    load(s)
    load(s)
    with s.engine.connect() as conn:
        assert conn.execute(db.ipo_listings.select()).fetchall().__len__() == 7
    s.universe.refresh_recent_ipos(payload=[p for p in PAYLOAD if p["symbol"] != "NEWIPO"], equity_text=EQUITY, source="fixture", min_rows=1, equity_min_rows=1)
    assert s.universe.recent_ipo_symbols() == {"SMEMOVED", "RWALK"}


class SplitProvider:
    """Fake provider: split events per symbol; a missing symbol = the provider cannot tell."""

    name = "fake"

    def __init__(self, events):
        self.events = events
        self.calls = []

    def splits(self, symbol):
        self.calls.append(symbol)
        return self.events.get(symbol)


def test_split_factor_since_listing(services_env):
    s, _ = services_env
    load(s)
    prov = SplitProvider({
        "NEWIPO": {D_NEW - timedelta(days=30): 5.0, D_NEW + timedelta(days=20): 2.0, D_NEW + timedelta(days=40): 1.5},  # the pre-listing event is ignored
        "RWALK": {},
    })
    res = s.universe.refresh_ipo_splits(prov, pause_s=0)
    assert res == {"status": "ok", "checked": 2, "with_split_or_bonus": 1, "unknown": 1}
    assert s.universe.ipo_issue("NEWIPO")["split_factor"] == pytest.approx(3.0)
    assert s.universe.ipo_issue("RWALK")["split_factor"] == 1.0
    assert s.universe.ipo_issue("SMEMOVED")["split_factor"] is None  # provider could not tell: not guessed
    load(s)  # a list refresh keeps the factors
    assert s.universe.ipo_issue("NEWIPO")["split_factor"] == pytest.approx(3.0)
    prov.calls.clear()
    s.universe.refresh_ipo_splits(prov, pause_s=0, only_unchecked=True)
    assert prov.calls == ["SMEMOVED"]


def test_adjusted_issue_price():
    row = {"issue_price": 808.0, "price_range": "Rs.769 to Rs.808", "split_factor": 10.0, "security_type": "Equity"}
    a = adjusted_issue_price(row, {"low": 62.3, "high": 80.0})  # live CORDELIA: 10:1 split after the IPO
    assert a["issue_price"] == 80.8 and a["issue_price_raw"] == 808.0 and "adjusted" in a["issue_price_note"]
    b = adjusted_issue_price({**row, "split_factor": None}, {"low": 62.3, "high": 80.0})
    assert b["issue_price"] is None and "not checked" in b["issue_price_note"]
    c = adjusted_issue_price({**row, "split_factor": 1.0}, {"low": 62.3, "high": 80.0})  # unrecorded split
    assert c["issue_price"] is None and "unrecorded split" in c["issue_price_note"]
    d = adjusted_issue_price({**row, "split_factor": 1.0}, {"low": 760.0, "high": 990.0})
    assert d["issue_price"] == 808.0 and d["issue_price_note"] is None
    assert adjusted_issue_price(None)["issue_price"] is None


def test_universe_api_recent_ipos(client_env):
    client, s, _, _ = client_env
    load(s)
    r = client.get("/api/universe", params={"universe": "ipo", "include_members": True}).json()
    assert r["count"] == 3 and {m["symbol"] for m in r["members"]} == {"NEWIPO", "SMEMOVED", "RWALK"}
    assert client.get("/api/universe", params={"universe": "all"}).json()["count"] == 7
    assert r["status"]["recent_ipos"]["last_success_rows"] == 3
