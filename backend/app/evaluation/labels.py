"""Human chart labels (P3, spec 18.1).

Labels are the ground truth for evaluation and are never inferred from the
detector's output. A label describes one chart as of an IST date: symbol,
timeframe, pattern type, formation start/end (candle timestamps), optional
boundary lines and swing points, a verdict and notes.

Verdicts: ``valid`` (a real pattern the detector should report), ``invalid``
and ``near_miss`` (structures it should not report), ``false_detection`` (a
detector output judged wrong).

Dataset split: every label belongs to ``tuning`` or ``validation``. The default
is deterministic by symbol (``int(sha1(symbol), 16) % validation_modulus == 0``
-> validation, about one stock in five), so tuning and validation use
different stocks; it can be overridden per label (e.g. later time periods).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from typing import Any

from sqlalchemy import Engine, delete, select, update

from .. import database as db

VERDICTS = ("valid", "invalid", "near_miss", "false_detection")
DATASETS = ("tuning", "validation")
EXPORT_FIELDS = [
    "label_id", "symbol", "timeframe", "pattern", "verdict", "asof", "formation_start", "formation_end",
    "upper_line", "lower_line", "swing_points", "dataset", "detection_id", "notes", "created_at", "updated_at",
]


def default_dataset(symbol: str, modulus: int) -> str:
    h = int(hashlib.sha1(symbol.upper().encode("utf-8")).hexdigest(), 16)
    return "validation" if modulus > 0 and h % modulus == 0 else "tuning"


def _row(r: Any) -> dict[str, Any]:
    d = dict(r)
    for k in ("upper_line", "lower_line", "swing_points"):
        d[k] = db.loads(d.pop(f"{k}_json", None))
    return d


class LabelStore:
    def __init__(self, engine: Engine, validation_modulus: int):
        self.engine = engine
        self.modulus = validation_modulus

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        now = db.now_ist_iso()
        label_id = uuid.uuid4().hex[:16]
        values = self._values(data)
        values.update(label_id=label_id, created_at=now, updated_at=now)
        if not values.get("dataset"):
            values["dataset"] = default_dataset(values["symbol"], self.modulus)
        with self.engine.begin() as conn:
            conn.execute(db.labels.insert().values(**values))
        return self.get(label_id)

    def update(self, label_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        if self.get(label_id) is None:
            return None
        values = self._values(data)
        if not values.get("dataset"):
            values["dataset"] = default_dataset(values["symbol"], self.modulus)
        values["updated_at"] = db.now_ist_iso()
        with self.engine.begin() as conn:
            conn.execute(update(db.labels).where(db.labels.c.label_id == label_id).values(**values))
        return self.get(label_id)

    def delete(self, label_id: str) -> bool:
        with self.engine.begin() as conn:
            res = conn.execute(delete(db.labels).where(db.labels.c.label_id == label_id))
        return bool(res.rowcount)

    def get(self, label_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(db.labels).where(db.labels.c.label_id == label_id)).mappings().first()
        return _row(row) if row else None

    def list(self, symbol: str | None = None, timeframe: str | None = None, pattern: str | None = None, dataset: str | None = None, verdict: str | None = None) -> list[dict[str, Any]]:
        t = db.labels
        q = select(t)
        for col, val in (("symbol", symbol), ("timeframe", timeframe), ("pattern", pattern), ("dataset", dataset), ("verdict", verdict)):
            if val:
                q = q.where(getattr(t.c, col) == val)
        q = q.order_by(t.c.symbol, t.c.timeframe, t.c.asof, t.c.formation_start)
        with self.engine.connect() as conn:
            return [_row(r) for r in conn.execute(q).mappings()]

    @staticmethod
    def _values(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "symbol": data["symbol"].upper(),
            "timeframe": data["timeframe"],
            "pattern": data["pattern"],
            "verdict": data["verdict"],
            "asof": str(data["asof"]),
            "formation_start": data["formation_start"],
            "formation_end": data["formation_end"],
            "upper_line_json": db.dumps(data.get("upper_line")) if data.get("upper_line") else None,
            "lower_line_json": db.dumps(data.get("lower_line")) if data.get("lower_line") else None,
            "swing_points_json": db.dumps(data.get("swing_points")) if data.get("swing_points") else None,
            "dataset": data.get("dataset"),
            "detection_id": data.get("detection_id"),
            "notes": data.get("notes"),
        }


def to_csv(rows: list[dict[str, Any]], fields: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in r.items()})
    return buf.getvalue()
