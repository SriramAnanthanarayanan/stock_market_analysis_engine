"""Chart drawings API: per-symbol persistent drawings with versions, conflict
detection, history, export / restore and drawing templates."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from .. import drawings as dr
from ..database import now_ist_iso
from ..services import Services

router = APIRouter(prefix="/api")


def svc(request: Request) -> Services:
    return request.app.state.services


def _conflict(c: dr.Conflict, what: str) -> HTTPException:
    msg = f"{what}: the drawing was changed in another window (server version {c.current['version']})" if c.current else f"{what}: a drawing with this id already exists"
    return HTTPException(status_code=409, detail={"message": msg, "current": c.current})


@router.get("/drawings")
def list_drawings(
    request: Request,
    symbol: str = Query(..., min_length=1, max_length=80),
    instrument: Literal["stock", "index"] = "stock",
    timeframe: str | None = Query(None, max_length=8, description="only drawings visible on this timeframe"),
) -> dict[str, Any]:
    items = dr.list_for(svc(request).engine, instrument, symbol, timeframe)
    return {"instrument": instrument, "symbol": dr.norm_symbol(instrument, symbol), "count": len(items), "items": items, "server_time": now_ist_iso()}


@router.post("/drawings", status_code=201)
def create_drawing(request: Request, body: dr.DrawingIn) -> dict[str, Any]:
    try:
        return dr.create(svc(request).engine, body)
    except dr.Conflict as c:
        raise _conflict(c, "create")


@router.get("/drawings/export")
def export_drawings(request: Request) -> JSONResponse:
    data = dr.export_all(svc(request).engine)
    return JSONResponse(content=data, headers={"Content-Disposition": 'attachment; filename="chart-drawings.json"'})


@router.post("/drawings/import")
def import_drawings(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    if not isinstance(payload.get("drawings"), (list, dict)):
        raise HTTPException(status_code=422, detail="not a drawings backup: expected a 'drawings' list (pas-drawings-v2) or object (pas-drawings-v1)")
    return dr.import_payload(svc(request).engine, payload)


@router.put("/drawings/{drawing_id}")
def update_drawing(request: Request, drawing_id: str, body: dr.DrawingUpdate) -> dict[str, Any]:
    if body.drawing_id != drawing_id:
        raise HTTPException(status_code=422, detail="drawing_id in the body must match the URL")
    try:
        return dr.update(svc(request).engine, drawing_id, body)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"drawing {drawing_id!r} not found (deleted in another window?)")
    except dr.Conflict as c:
        raise _conflict(c, "update")


@router.delete("/drawings/{drawing_id}", status_code=204)
def delete_drawing(request: Request, drawing_id: str, version: int = Query(..., ge=1)) -> Response:
    try:
        dr.remove(svc(request).engine, drawing_id, version)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"drawing {drawing_id!r} not found")
    except dr.Conflict as c:
        raise _conflict(c, "delete")
    return Response(status_code=204)


@router.get("/drawings/{drawing_id}/history")
def drawing_history(request: Request, drawing_id: str) -> dict[str, Any]:
    return {"drawing_id": drawing_id, "items": dr.history(svc(request).engine, drawing_id)}


@router.get("/drawing-templates")
def list_templates(request: Request, drawing_type: str | None = Query(None, max_length=30), symbol: str | None = Query(None, max_length=80)) -> dict[str, Any]:
    items = dr.list_templates(svc(request).engine, drawing_type, symbol)
    return {"count": len(items), "items": items}


@router.post("/drawing-templates")
def save_template(request: Request, body: dr.TemplateIn) -> dict[str, Any]:
    return dr.save_template(svc(request).engine, body)


@router.delete("/drawing-templates/{template_id}", status_code=204)
def delete_template(request: Request, template_id: str) -> Response:
    if not dr.delete_template(svc(request).engine, template_id):
        raise HTTPException(status_code=404, detail="template not found")
    return Response(status_code=204)
