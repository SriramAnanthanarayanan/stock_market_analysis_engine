"""Persistent chart drawings (spec 11 / 15.7, backend part): create, reopen, timeframe
visibility, edit with versions, conflicting edits from another tab, delete,
application restart, export / restore, legacy browser backups, templates."""

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FNO_CSV, NIFTY_CSV, FixtureProvider, fixture_frames, make_settings


def fib(id_="fibA0001", **kw):
    body = {
        "drawing_id": id_,
        "instrument": "stock",
        "symbol": "rwalk",
        "drawing_type": "fib_retracement",
        "anchors": [{"t": 1767000000, "p": 900.0}, {"t": 1769000000, "p": 1100.0}],
        "color": "#F5C542",
        "line_style": "solid",
        "line_width": 1,
        "settings": {"levels": [{"ratio": 0}, {"ratio": 0.618, "color": "#ff0000"}, {"ratio": 1}], "mode": "linear", "reverse": False, "extend_right": True},
    }
    body.update(kw)
    return body


def test_create_reopen_edit_delete_with_versions(client_env):
    client, s, _, _ = client_env
    r = client.post("/api/drawings", json=fib())
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["symbol"] == "RWALK" and d["version"] == 1 and d["settings"]["levels"][1]["color"] == "#ff0000"
    # reopen (another page / preview / fullscreen all read the same list)
    lst = client.get("/api/drawings", params={"symbol": "RWALK"}).json()
    assert [x["drawing_id"] for x in lst["items"]] == ["fibA0001"] and lst["items"][0]["anchors"][0]["t"] == 1767000000
    # edit: move an anchor
    upd = fib(anchors=[{"t": 1767000000, "p": 950.0}, {"t": 1769000000, "p": 1100.0}], base_version=1)
    r2 = client.put("/api/drawings/fibA0001", json=upd)
    assert r2.status_code == 200 and r2.json()["version"] == 2 and r2.json()["anchors"][0]["p"] == 950.0
    # a stale tab still on version 1 cannot overwrite version 2
    stale = client.put("/api/drawings/fibA0001", json=fib(color="#00ff00", base_version=1))
    assert stale.status_code == 409 and stale.json()["detail"]["current"]["version"] == 2
    assert client.get("/api/drawings", params={"symbol": "RWALK"}).json()["items"][0]["color"] == "#F5C542"
    # delete needs the current version
    assert client.delete("/api/drawings/fibA0001", params={"version": 1}).status_code == 409
    assert client.delete("/api/drawings/fibA0001", params={"version": 2}).status_code == 204
    assert client.get("/api/drawings", params={"symbol": "RWALK"}).json()["count"] == 0
    hist = client.get("/api/drawings/fibA0001/history").json()["items"]
    assert [h["action"] for h in hist] == ["create", "update", "delete"]
    assert client.put("/api/drawings/fibA0001", json=fib(base_version=2)).status_code == 404


def test_timeframe_visibility_and_symbol_scope(client_env):
    client, _, _, _ = client_env
    client.post("/api/drawings", json={**fib("allTF001"), "drawing_type": "hline", "anchors": [{"t": 1767000000, "p": 1000}], "settings": None})
    client.post("/api/drawings", json={**fib("dailyOn1"), "drawing_type": "trend", "settings": None, "timeframes": ["1d"]})
    client.post("/api/drawings", json={**fib("otherSym"), "symbol": "ATRI", "drawing_type": "hline", "anchors": [{"t": 1, "p": 5}], "settings": None})
    d1 = {x["drawing_id"] for x in client.get("/api/drawings", params={"symbol": "RWALK", "timeframe": "1d"}).json()["items"]}
    w1 = {x["drawing_id"] for x in client.get("/api/drawings", params={"symbol": "RWALK", "timeframe": "1w"}).json()["items"]}
    assert d1 == {"allTF001", "dailyOn1"} and w1 == {"allTF001"}
    assert {x["drawing_id"] for x in client.get("/api/drawings", params={"symbol": "RWALK"}).json()["items"]} == {"allTF001", "dailyOn1"}


@pytest.mark.parametrize(
    "patch,why",
    [
        ({"drawing_type": "fib_extension"}, "3 anchors"),
        ({"anchors": [{"t": 1, "p": 1}]}, "2 anchors"),
        ({"color": "red; drop"}, "colour"),
        ({"timeframes": ["2d"]}, "timeframes"),
        ({"settings": {"mode": "log"}, "anchors": [{"t": 1, "p": -5}, {"t": 2, "p": 10}]}, "positive"),
        ({"settings": {"bogus": 1}}, "unknown"),
        ({"drawing_id": "x"}, "id"),
    ],
)
def test_invalid_drawings_are_rejected(client_env, patch, why):
    client, _, _, _ = client_env
    assert client.post("/api/drawings", json=fib("badOne01", **patch)).status_code == 422


def test_fib_extension_and_channel_need_three_anchors(client_env):
    client, _, _, _ = client_env
    three = [{"t": 1767000000, "p": 900.0}, {"t": 1768000000, "p": 1100.0}, {"t": 1769000000, "p": 1000.0}]
    assert client.post("/api/drawings", json=fib("fibExt01", drawing_type="fib_extension", anchors=three)).status_code == 201
    assert client.post("/api/drawings", json=fib("chan0001", drawing_type="channel", anchors=three, settings=None)).status_code == 201


def test_drawings_survive_application_restart(tmp_path, calendar):
    from app.main import create_app

    settings = make_settings(tmp_path)

    def app():
        a = create_app(settings, provider=FixtureProvider(fixture_frames(calendar)), background=False)
        return a

    with TestClient(app()) as c1:
        c1.app.state.services.universe.refresh_nifty500(text=NIFTY_CSV, source="fixture")
        assert c1.post("/api/drawings", json=fib("persist1")).status_code == 201
    with TestClient(app()) as c2:  # same database file, new process state
        items = c2.get("/api/drawings", params={"symbol": "RWALK"}).json()["items"]
        assert [x["drawing_id"] for x in items] == ["persist1"] and items[0]["settings"]["levels"][1]["ratio"] == 0.618


def test_export_and_restore(client_env):
    client, _, _, _ = client_env
    client.post("/api/drawings", json=fib("exp00001"))
    client.put("/api/drawings/exp00001", json=fib("exp00001", color="#123456", base_version=1))
    client.post("/api/drawing-templates", json={"template_id": "tplGold1", "name": "Gold fib", "drawing_type": "fib_retracement", "settings": {"levels": [{"ratio": 0.5}], "mode": "linear"}})
    backup = client.get("/api/drawings/export")
    assert backup.status_code == 200 and "attachment" in backup.headers["content-disposition"]
    data = backup.json()
    assert data["format"] == "pas-drawings-v2" and data["count"] == 1 and data["templates"][0]["template_id"] == "tplGold1"
    # delete, then restore from the backup
    assert client.delete("/api/drawings/exp00001", params={"version": 2}).status_code == 204
    res = client.post("/api/drawings/import", json=data).json()
    assert res["inserted"] == 1 and res["invalid"] == 0
    back = client.get("/api/drawings", params={"symbol": "RWALK"}).json()["items"][0]
    assert back["color"] == "#123456" and back["version"] == 2
    # restoring again skips (not newer); a newer server copy is never overwritten by an older backup
    client.put("/api/drawings/exp00001", json=fib("exp00001", color="#abcdef", base_version=2))
    again = client.post("/api/drawings/import", json=data).json()
    assert again["skipped"] == 1 and client.get("/api/drawings", params={"symbol": "RWALK"}).json()["items"][0]["color"] == "#abcdef"
    assert client.post("/api/drawings/import", json={"nope": 1}).status_code == 422


def test_legacy_browser_backup_import(client_env):
    client, _, _, _ = client_env
    legacy = {
        "format": "pas-drawings-v1",
        "drawings": {
            "pas.drawings.v1:stock:RWALK:1d": [
                {"id": "dk2l3m4n5", "type": "fib", "points": [{"t": 1767000000, "p": 900}, {"t": 1769000000, "p": 1100}], "color": "#19C3DA", "width": 2, "dashed": True},
                {"id": "dk2l3m4n6", "type": "hline", "points": [{"t": 1767000000, "p": 1000}], "color": "#19C3DA", "width": 1, "dashed": False},
            ],
            "pas.drawings.v1:index:NIFTY 50:1w": [{"id": "dk2l3m4n7", "type": "trend", "points": [{"t": 1, "p": 1}, {"t": 2, "p": 2}], "color": "#19C3DA", "width": 1, "dashed": False}],
        },
    }
    res = client.post("/api/drawings/import", json=legacy).json()
    assert res["inserted"] == 3 and res["invalid"] == 0
    items = {x["drawing_id"]: x for x in client.get("/api/drawings", params={"symbol": "RWALK"}).json()["items"]}
    assert items["dk2l3m4n5"]["drawing_type"] == "fib_retracement" and items["dk2l3m4n5"]["line_style"] == "dashed"
    assert items["dk2l3m4n5"]["timeframes"] == ["1d"] and items["dk2l3m4n5"]["origin"] == "legacy_browser"
    idx = client.get("/api/drawings", params={"symbol": "NIFTY 50", "instrument": "index"}).json()["items"]
    assert idx and idx[0]["timeframes"] == ["1w"]
    assert client.post("/api/drawings/import", json=legacy).json()["skipped"] == 3  # idempotent


def test_templates_global_and_symbol(client_env):
    client, _, _, _ = client_env
    g = client.post("/api/drawing-templates", json={"template_id": "tplGlob1", "name": "Golden", "drawing_type": "fib_retracement", "settings": {"levels": [{"ratio": 0.382}, {"ratio": 0.618}], "mode": "log", "color": "#F5C542"}})
    assert g.status_code == 200 and g.json()["settings"]["color"] == "#F5C542"
    client.post("/api/drawing-templates", json={"template_id": "tplSym01", "name": "RWALK ext", "drawing_type": "fib_extension", "scope": "symbol", "symbol": "RWALK", "settings": {"levels": [{"ratio": 1.618}]}})
    assert client.post("/api/drawing-templates", json={"template_id": "tplBad01", "name": "x", "drawing_type": "fib_extension", "scope": "symbol", "settings": {}}).status_code == 422
    all_ = client.get("/api/drawing-templates", params={"symbol": "RWALK"}).json()["items"]
    assert {t["template_id"] for t in all_} == {"tplGlob1", "tplSym01"}
    assert {t["template_id"] for t in client.get("/api/drawing-templates", params={"symbol": "ATRI"}).json()["items"]} == {"tplGlob1"}
    assert client.delete("/api/drawing-templates/tplSym01").status_code == 204
    assert client.delete("/api/drawing-templates/tplSym01").status_code == 404


# ------------------------------------------------------------------ users, creating timeframe, annotations
def test_drawings_are_stored_per_user_symbol_and_timeframe(client_env):
    client, _, _, _ = client_env
    A, B = {"X-PAS-User": "alice"}, {"X-PAS-User": "bob"}
    body = {**fib("userA001"), "drawing_type": "trend", "settings": None, "created_timeframe": "1w"}
    r = client.post("/api/drawings", json=body, headers=A)
    assert r.status_code == 201 and r.json()["owner"] == "alice" and r.json()["created_timeframe"] == "1w"
    assert client.get("/api/drawings", params={"symbol": "RWALK"}, headers=B).json()["count"] == 0
    assert client.get("/api/drawings", params={"symbol": "RWALK"}).json()["owner"] == "local"  # no header: the local user
    mine = client.get("/api/drawings", params={"symbol": "RWALK"}, headers=A).json()
    assert [x["drawing_id"] for x in mine["items"]] == ["userA001"]
    # another user can neither change nor delete it
    assert client.put("/api/drawings/userA001", json={**body, "base_version": 1, "color": "#ff00ff"}, headers=B).status_code == 404
    assert client.delete("/api/drawings/userA001", params={"version": 1}, headers=B).status_code == 404
    # an update without created_timeframe keeps the timeframe it was drawn on
    upd = {k: v for k, v in body.items() if k != "created_timeframe"}
    r2 = client.put("/api/drawings/userA001", json={**upd, "base_version": 1, "color": "#ff00ff"}, headers=A)
    assert r2.status_code == 200 and r2.json()["created_timeframe"] == "1w" and r2.json()["color"] == "#ff00ff"
    assert client.get("/api/drawings", params={"symbol": "RWALK"}, headers={"X-PAS-User": "bad user!"}).status_code == 422
    exp = client.get("/api/drawings/export", headers=A).json()
    assert {d["owner"] for d in exp["drawings"]} == {"alice"}
    assert client.post("/api/drawings", json={**body, "drawing_id": "badTF001", "created_timeframe": "2d"}, headers=A).status_code == 422


def test_text_annotation_persists(client_env):
    client, _, _, _ = client_env
    note = {**fib("note0001"), "drawing_type": "text", "anchors": [{"t": 1767000000, "p": 1010.0}], "settings": None, "style": {"label": "Results on 12-Nov"}}
    r = client.post("/api/drawings", json=note)
    assert r.status_code == 201 and r.json()["style"]["label"] == "Results on 12-Nov"
    again = client.get("/api/drawings", params={"symbol": "RWALK"}).json()["items"]
    assert [x for x in again if x["drawing_id"] == "note0001"][0]["drawing_type"] == "text"
    blank = client.post("/api/drawings", json={**note, "drawing_id": "note0002", "style": {}}).json()
    assert blank["style"]["label"] == "Note"
    assert client.post("/api/drawings", json={**note, "drawing_id": "note0003", "anchors": note["anchors"] * 2}).status_code == 422
    assert client.post("/api/drawings", json={**note, "drawing_id": "note0004", "style": {"label": "x" * 301}}).status_code == 422


def test_migration_assigns_existing_drawings_to_the_local_user(tmp_path):
    from sqlalchemy import text

    from app import database as db

    eng = db.make_engine(tmp_path / "m.db")
    with eng.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE schema_version (version INTEGER NOT NULL, applied_at VARCHAR NOT NULL, description VARCHAR)")
        for v, desc, fn in db.MIGRATIONS[:8]:
            fn(conn)
            conn.exec_driver_sql("INSERT INTO schema_version VALUES (?, ?, ?)", (v, "2026-11-04T00:00:00+05:30", desc))
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(chart_drawings)")}
        if "owner" in cols:  # metadata already carries migration-9 columns on a fresh create; simulate the old table
            conn.exec_driver_sql("DROP TABLE chart_drawings")
            conn.exec_driver_sql("CREATE TABLE chart_drawings (drawing_id VARCHAR PRIMARY KEY, instrument VARCHAR NOT NULL, symbol VARCHAR NOT NULL, drawing_type VARCHAR NOT NULL, anchors_json TEXT NOT NULL, color VARCHAR NOT NULL, line_style VARCHAR NOT NULL, line_width INTEGER NOT NULL, style_json TEXT, settings_json TEXT, visible INTEGER NOT NULL, timeframes_json TEXT, created_at VARCHAR NOT NULL, updated_at VARCHAR NOT NULL, version INTEGER NOT NULL, origin VARCHAR)")
        conn.exec_driver_sql("INSERT INTO chart_drawings VALUES ('old00001','stock','ITC','hline','[{\"t\":1,\"p\":5}]','#19C3DA','solid',2,'{}',NULL,1,NULL,'x','x',3,'ui')")
    assert db.migrate(eng) == db.SCHEMA_VERSION
    with eng.connect() as conn:
        row = conn.execute(text("SELECT owner, created_timeframe, version, symbol FROM chart_drawings WHERE drawing_id='old00001'")).one()
    assert tuple(row) == ("local", None, 3, "ITC")  # nothing rewritten except the new columns' defaults


def test_owner_in_the_body_is_ignored(client_env):
    client, _, _, _ = client_env
    body = {**fib("ownerX01"), "drawing_type": "hline", "anchors": [{"t": 1767000000, "p": 1000}], "settings": None, "owner": "mallory"}
    r = client.post("/api/drawings", json=body, headers={"X-PAS-User": "alice"})
    assert r.status_code == 201 and r.json()["owner"] == "alice"  # the request's user decides, never the payload
    echoed = {**r.json(), "base_version": 1}
    for k in ("version", "created_at", "updated_at", "origin"):
        echoed.pop(k)
    assert client.put("/api/drawings/ownerX01", json=echoed, headers={"X-PAS-User": "alice"}).status_code == 200
