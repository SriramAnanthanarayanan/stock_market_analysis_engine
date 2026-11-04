"""Database-backed manual chart drawings (docs/chart-drawing-persistence.md).

A drawing belongs to an instrument (stock or index) and symbol - not to one
chart page or timeframe - and is anchored to (candle start time in epoch
seconds, price), never to pixels. ``timeframes`` limits where it is shown
(``None`` = every timeframe).

Concurrency: every row carries ``version``. An update or delete must name the
version it was based on; when another tab saved a newer version first the call
is rejected with the current copy (HTTP 409) instead of silently overwriting it.
Every create / update / delete / import is appended to ``chart_drawing_history``.
Nothing is deleted except by an explicit delete of that drawing.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import Engine, delete, select

from . import database as db
from .config import CHART_TIMEFRAMES

DRAWING_TYPES = ("hline", "vline", "trend", "ray", "rect", "channel", "fib_retracement", "fib_extension")
ANCHORS = {"hline": 1, "vline": 1, "trend": 2, "ray": 2, "rect": 2, "channel": 3, "fib_retracement": 2, "fib_extension": 3}
ID_RE = r"^[A-Za-z0-9_\-]{6,64}$"
COLOR_RE = re.compile(r"^(#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?|rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(,\s*(0|1|0?\.\d+)\s*)?\))$")


class Anchor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    t: int = Field(ge=0, le=4_102_444_800)  # epoch seconds, may lie beyond the last candle
    p: float


class FibLevel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ratio: float = Field(ge=-10, le=100)
    visible: bool = True
    color: str | None = Field(default=None, max_length=40)
    label: str | None = Field(default=None, max_length=40)


class DrawingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    drawing_id: str = Field(pattern=ID_RE)
    instrument: Literal["stock", "index"] = "stock"
    symbol: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9&\-_.^ :/]+$")
    drawing_type: Literal["hline", "vline", "trend", "ray", "rect", "channel", "fib_retracement", "fib_extension"]
    anchors: list[Anchor] = Field(min_length=1, max_length=3)
    color: str = Field(default="#19C3DA", max_length=40)
    line_style: Literal["solid", "dashed", "dotted"] = "solid"
    line_width: int = Field(default=2, ge=1, le=4)
    style: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] | None = None
    visible: bool = True
    timeframes: list[str] | None = None

    @field_validator("color")
    @classmethod
    def _color(cls, v: str) -> str:
        if not COLOR_RE.match(v.strip()):
            raise ValueError("colour must be #RRGGBB(AA) or rgb()/rgba()")
        return v.strip()

    @field_validator("timeframes")
    @classmethod
    def _tfs(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        bad = [x for x in v if x not in CHART_TIMEFRAMES]
        if bad or not v:
            raise ValueError(f"timeframes must be a non-empty subset of {list(CHART_TIMEFRAMES)} or null (all)")
        return sorted(set(v), key=CHART_TIMEFRAMES.index)

    @field_validator("style")
    @classmethod
    def _style(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(repr(v)) > 4000:
            raise ValueError("style too large")
        return v

    @model_validator(mode="after")
    def _anchors_and_settings(self) -> "DrawingIn":
        need = ANCHORS[self.drawing_type]
        if len(self.anchors) != need:
            raise ValueError(f"{self.drawing_type} needs {need} anchor(s), got {len(self.anchors)}")
        if self.drawing_type.startswith("fib"):
            self.settings = validate_fib_settings(self.settings or {}, self.anchors)
        elif self.settings and len(repr(self.settings)) > 4000:
            raise ValueError("settings too large")
        return self


def validate_fib_settings(st: dict[str, Any], anchors: list[Anchor]) -> dict[str, Any]:
    allowed = {"levels", "mode", "reverse", "extend_left", "extend_right", "show_trendline", "show_prices", "show_ratios", "show_background", "background_opacity", "label_position", "font_size", "one_color", "percent_labels", "template_id"}
    unknown = set(st) - allowed
    if unknown:
        raise ValueError(f"unknown Fibonacci settings {sorted(unknown)}")
    levels = st.get("levels")
    if levels is not None:
        if not isinstance(levels, list) or len(levels) > 64:
            raise ValueError("levels: a list of at most 64 levels")
        st["levels"] = [FibLevel(**lv).model_dump() for lv in levels]
    mode = st.get("mode", "linear")
    if mode not in ("linear", "log"):
        raise ValueError("mode must be linear or log")
    if mode == "log" and any(a.p <= 0 for a in anchors):
        raise ValueError("logarithmic Fibonacci needs positive anchor prices")
    if "font_size" in st and not (8 <= int(st["font_size"]) <= 24):
        raise ValueError("font_size 8-24")
    if "label_position" in st and st["label_position"] not in ("left", "right"):
        raise ValueError("label_position left or right")
    if "background_opacity" in st and not (0 <= float(st["background_opacity"]) <= 1):
        raise ValueError("background_opacity 0-1")
    return st


class DrawingUpdate(DrawingIn):
    base_version: int = Field(ge=1)


def norm_symbol(instrument: str, symbol: str) -> str:
    return symbol.strip().upper() if instrument == "stock" else symbol.strip()


def row_out(r: Any) -> dict[str, Any]:
    d = dict(r)
    d["anchors"] = db.loads(d.pop("anchors_json")) or []
    d["style"] = db.loads(d.pop("style_json", None)) or {}
    d["settings"] = db.loads(d.pop("settings_json", None))
    d["timeframes"] = db.loads(d.pop("timeframes_json", None))
    d["visible"] = bool(d["visible"])
    return d


def _values(d: DrawingIn) -> dict[str, Any]:
    return {
        "instrument": d.instrument,
        "symbol": norm_symbol(d.instrument, d.symbol),
        "drawing_type": d.drawing_type,
        "anchors_json": db.dumps([a.model_dump() for a in d.anchors]),
        "color": d.color,
        "line_style": d.line_style,
        "line_width": d.line_width,
        "style_json": db.dumps(d.style),
        "settings_json": db.dumps(d.settings) if d.settings is not None else None,
        "visible": int(d.visible),
        "timeframes_json": db.dumps(d.timeframes) if d.timeframes is not None else None,
    }


class Conflict(Exception):
    def __init__(self, current: dict[str, Any] | None):
        self.current = current


def get(engine: Engine, drawing_id: str) -> dict[str, Any] | None:
    t = db.chart_drawings
    with engine.connect() as conn:
        r = conn.execute(select(t).where(t.c.drawing_id == drawing_id)).mappings().first()
    return row_out(r) if r else None


def list_for(engine: Engine, instrument: str, symbol: str, timeframe: str | None = None) -> list[dict[str, Any]]:
    t = db.chart_drawings
    with engine.connect() as conn:
        rows = conn.execute(select(t).where((t.c.instrument == instrument) & (t.c.symbol == norm_symbol(instrument, symbol))).order_by(t.c.created_at)).mappings().all()
    out = [row_out(r) for r in rows]
    if timeframe:
        out = [d for d in out if d["timeframes"] is None or timeframe in d["timeframes"]]
    return out


def _history(conn, drawing_id: str, version: int, action: str, payload: dict[str, Any]) -> None:
    conn.execute(db.chart_drawing_history.insert().values(drawing_id=drawing_id, version=version, action=action, payload_json=db.dumps(payload), at=db.now_ist_iso()))


def create(engine: Engine, d: DrawingIn, origin: str = "ui") -> dict[str, Any]:
    t = db.chart_drawings
    now = db.now_ist_iso()
    with engine.begin() as conn:
        exists = conn.execute(select(t.c.drawing_id).where(t.c.drawing_id == d.drawing_id)).first()
        if exists:
            raise Conflict(None)
        vals = {**_values(d), "drawing_id": d.drawing_id, "created_at": now, "updated_at": now, "version": 1, "origin": origin}
        conn.execute(t.insert().values(**vals))
        _history(conn, d.drawing_id, 1, "create", d.model_dump())
    return get(engine, d.drawing_id)  # type: ignore[return-value]


def update(engine: Engine, drawing_id: str, d: DrawingUpdate) -> dict[str, Any]:
    t = db.chart_drawings
    with engine.begin() as conn:
        cur = conn.execute(select(t).where(t.c.drawing_id == drawing_id)).mappings().first()
        if cur is None:
            raise KeyError(drawing_id)
        if cur["version"] != d.base_version:
            raise Conflict(row_out(cur))
        v = cur["version"] + 1
        conn.execute(t.update().where((t.c.drawing_id == drawing_id) & (t.c.version == d.base_version)).values(**_values(d), updated_at=db.now_ist_iso(), version=v))
        _history(conn, drawing_id, v, "update", d.model_dump(exclude={"base_version"}))
    return get(engine, drawing_id)  # type: ignore[return-value]


def remove(engine: Engine, drawing_id: str, version: int) -> None:
    t = db.chart_drawings
    with engine.begin() as conn:
        cur = conn.execute(select(t).where(t.c.drawing_id == drawing_id)).mappings().first()
        if cur is None:
            raise KeyError(drawing_id)
        if cur["version"] != version:
            raise Conflict(row_out(cur))
        conn.execute(delete(t).where(t.c.drawing_id == drawing_id))
        _history(conn, drawing_id, version, "delete", row_out(cur))


def history(engine: Engine, drawing_id: str) -> list[dict[str, Any]]:
    t = db.chart_drawing_history
    with engine.connect() as conn:
        rows = conn.execute(select(t).where(t.c.drawing_id == drawing_id).order_by(t.c.id)).mappings().all()
    return [{"version": r["version"], "action": r["action"], "at": r["at"], "payload": db.loads(r["payload_json"])} for r in rows]


def export_all(engine: Engine) -> dict[str, Any]:
    t, tt = db.chart_drawings, db.drawing_templates
    with engine.connect() as conn:
        rows = [row_out(r) for r in conn.execute(select(t).order_by(t.c.instrument, t.c.symbol, t.c.created_at)).mappings()]
        tpl = [template_out(r) for r in conn.execute(select(tt).order_by(tt.c.name)).mappings()]
    return {"format": "pas-drawings-v2", "exported_at": db.now_ist_iso(), "count": len(rows), "drawings": rows, "templates": tpl}


def _legacy_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Browser-only backups (E1 localStorage format ``pas-drawings-v1``):
    ``{"drawings": {"pas.drawings.v1:<chart>:<SYMBOL>:<tf>": [ {id, type, points, color, width, dashed} ]}}``.
    Each keeps its original timeframe as its only visible timeframe."""
    out = []
    for key, items in (payload.get("drawings") or {}).items():
        parts = str(key).split(":")
        if len(parts) < 4 or not isinstance(items, list):
            continue
        chart, tf = parts[1], parts[-1]
        symbol = ":".join(parts[2:-1])
        for d in items:
            if not isinstance(d, dict) or "points" not in d:
                continue
            typ = {"fib": "fib_retracement"}.get(d.get("type"), d.get("type"))
            out.append(
                {
                    "drawing_id": re.sub(r"[^A-Za-z0-9_\-]", "_", str(d.get("id", "")))[:64].ljust(6, "0"),
                    "instrument": "index" if chart == "index" else "stock",
                    "symbol": symbol,
                    "drawing_type": typ,
                    "anchors": [{"t": int(p["t"]), "p": float(p["p"])} for p in d["points"]],
                    "color": d.get("color", "#19C3DA"),
                    "line_style": "dashed" if d.get("dashed") else "solid",
                    "line_width": int(d.get("width", 2)),
                    "timeframes": [tf] if tf in CHART_TIMEFRAMES else None,
                    "settings": {"levels": [{"ratio": r} for r in (0, 0.236, 0.382, 0.5, 0.618, 0.786, 1)]} if typ == "fib_retracement" else None,
                }
            )
    return out


def import_payload(engine: Engine, payload: dict[str, Any]) -> dict[str, Any]:
    """Restore a backup. Unknown drawings are inserted; a drawing that exists is
    replaced only when the backup copy is newer (higher version, or the same
    version updated later); otherwise it is skipped. Invalid entries are reported."""
    fmt = payload.get("format")
    items = _legacy_rows(payload) if fmt == "pas-drawings-v1" or isinstance(payload.get("drawings"), dict) else (payload.get("drawings") or [])
    res = {"inserted": 0, "updated": 0, "skipped": 0, "invalid": 0, "errors": []}
    t = db.chart_drawings
    for raw in items:
        try:
            d = DrawingIn(**{k: raw[k] for k in DrawingIn.model_fields if k in raw})
        except Exception as exc:
            res["invalid"] += 1
            if len(res["errors"]) < 10:
                res["errors"].append(f"{raw.get('drawing_id', '?')}: {exc}"[:300])
            continue
        cur = get(engine, d.drawing_id)
        if cur is None:
            create(engine, d, origin="legacy_browser" if fmt == "pas-drawings-v1" else "import")
            if raw.get("version") or raw.get("created_at"):
                with engine.begin() as conn:
                    conn.execute(t.update().where(t.c.drawing_id == d.drawing_id).values(version=int(raw.get("version") or 1), created_at=raw.get("created_at") or db.now_ist_iso(), updated_at=raw.get("updated_at") or db.now_ist_iso()))
            res["inserted"] += 1
            continue
        newer = int(raw.get("version") or 0) > cur["version"] or (int(raw.get("version") or 0) == cur["version"] and str(raw.get("updated_at") or "") > str(cur["updated_at"]))
        if not newer:
            res["skipped"] += 1
            continue
        update(engine, d.drawing_id, DrawingUpdate(**d.model_dump(), base_version=cur["version"]))
        res["updated"] += 1
    for tpl in payload.get("templates") or []:
        try:
            save_template(engine, TemplateIn(**{k: tpl[k] for k in TemplateIn.model_fields if k in tpl}))
        except Exception:
            res["invalid"] += 1
    return res


# ------------------------------------------------------------------ templates
class TemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: str = Field(pattern=ID_RE)
    name: str = Field(min_length=1, max_length=80)
    drawing_type: Literal["hline", "vline", "trend", "ray", "rect", "channel", "fib_retracement", "fib_extension"]
    scope: Literal["global", "symbol"] = "global"
    symbol: str | None = Field(default=None, max_length=80)
    settings: dict[str, Any]

    @model_validator(mode="after")
    def _check(self) -> "TemplateIn":
        if self.scope == "symbol" and not self.symbol:
            raise ValueError("a symbol template needs a symbol")
        if self.drawing_type.startswith("fib"):
            st = dict(self.settings)
            style = st.pop("style", None)
            line = {k: st.pop(k) for k in ("color", "line_style", "line_width") if k in st}
            self.settings = {**validate_fib_settings(st, [Anchor(t=0, p=1.0)]), **({"style": style} if style is not None else {}), **line}
        elif len(repr(self.settings)) > 4000:
            raise ValueError("settings too large")
        return self


def template_out(r: Any) -> dict[str, Any]:
    d = dict(r)
    d["settings"] = db.loads(d.pop("settings_json")) or {}
    return d


def save_template(engine: Engine, t_in: TemplateIn) -> dict[str, Any]:
    t = db.drawing_templates
    now = db.now_ist_iso()
    vals = {"name": t_in.name, "drawing_type": t_in.drawing_type, "scope": t_in.scope, "symbol": t_in.symbol, "settings_json": db.dumps(t_in.settings), "updated_at": now}
    with engine.begin() as conn:
        if conn.execute(select(t.c.template_id).where(t.c.template_id == t_in.template_id)).first():
            conn.execute(t.update().where(t.c.template_id == t_in.template_id).values(**vals))
        else:
            conn.execute(t.insert().values(template_id=t_in.template_id, created_at=now, **vals))
        r = conn.execute(select(t).where(t.c.template_id == t_in.template_id)).mappings().first()
    return template_out(r)


def list_templates(engine: Engine, drawing_type: str | None = None, symbol: str | None = None) -> list[dict[str, Any]]:
    t = db.drawing_templates
    q = select(t)
    if drawing_type:
        q = q.where(t.c.drawing_type == drawing_type)
    with engine.connect() as conn:
        rows = [template_out(r) for r in conn.execute(q.order_by(t.c.name)).mappings()]
    return [r for r in rows if r["scope"] == "global" or (symbol and r["symbol"] == symbol)]


def delete_template(engine: Engine, template_id: str) -> bool:
    t = db.drawing_templates
    with engine.begin() as conn:
        return bool(conn.execute(delete(t).where(t.c.template_id == template_id)).rowcount)
