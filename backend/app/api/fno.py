"""F&O OI activity API (docs/fno-activity.md): status / refresh of the NSE
bhavcopy data, the daily futures buildup selection (the user's own rules,
unchanged), the watchlist of existing positions and the configuration.
Screening output, not trade recommendations."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request

from ..services import Services

router = APIRouter(prefix="/api")
_SYMBOL = re.compile(r"^[A-Z0-9&\-]{1,20}$")


def svc(request: Request) -> Services:
    return request.app.state.services


@router.get("/fno/status")
def fno_status(request: Request) -> dict[str, Any]:
    """Stored NSE sessions, the running download job and the data sources."""
    return svc(request).fno.status()


@router.post("/fno/refresh")
def fno_refresh(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Download missing sessions (default: the backfill depth) and compute the daily runs in the background."""
    sessions = body.get("sessions")
    if sessions is not None and (not isinstance(sessions, int) or not 1 <= sessions <= 1000):
        raise HTTPException(status_code=422, detail="sessions must be an integer between 1 and 1000")
    recompute = bool(body.get("recompute", False))
    return svc(request).fno.start_update(background=True, sessions=sessions, recompute=recompute, trigger="manual")


@router.get("/fno/dates")
def fno_dates(request: Request) -> dict[str, Any]:
    return {"dates": svc(request).fno.dates()}


@router.get("/fno/activity")
def fno_activity(request: Request, day: date | None = Query(None, alias="date")) -> dict[str, Any]:
    """Selected futures of one session (default: latest) and the watchlist status."""
    try:
        return svc(request).fno.activity(day)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc


@router.get("/fno/config")
def fno_config(request: Request) -> dict[str, Any]:
    return svc(request).fno.config_payload()


def _clean(symbols: Any) -> list[str]:
    if not isinstance(symbols, list):
        raise HTTPException(status_code=422, detail="symbols must be a list")
    out = []
    for s in symbols:
        if not isinstance(s, str):
            raise HTTPException(status_code=422, detail="symbols must be strings")
        s = s.strip().upper()
        if not s:
            continue
        if not _SYMBOL.match(s):
            raise HTTPException(status_code=422, detail=f"invalid symbol {s!r}")
        out.append(s)
    if len(out) > 200:
        raise HTTPException(status_code=422, detail="at most 200 watchlist symbols")
    return sorted(set(out))


@router.get("/fno/watchlist")
def fno_watchlist(request: Request) -> dict[str, Any]:
    f = svc(request).fno
    from ..fno import store

    return {"symbols": store.watchlist(f.engine), "known": f.known_symbols()}


@router.put("/fno/watchlist")
def fno_set_watchlist(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Replace the watchlist. Symbols must be F&O underlyings of the latest stored session (when one exists)."""
    f = svc(request).fno
    symbols = _clean(body.get("symbols"))
    known = set(f.known_symbols())
    unknown = [s for s in symbols if known and s not in known]
    if unknown:
        raise HTTPException(status_code=422, detail=f"not F&O underlyings in the latest NSE session: {', '.join(unknown)}")
    return {"symbols": f.set_watchlist(symbols), "known": sorted(known)}
