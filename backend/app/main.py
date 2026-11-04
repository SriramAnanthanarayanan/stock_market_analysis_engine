"""FastAPI application factory.

Run:  uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8031
 or:  python -m app
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import Settings, load_settings
from .net import configure_ssl
from .providers.base import MarketDataProvider

log = logging.getLogger("app")


def configure_logging() -> None:
    root = logging.getLogger()
    if not any(getattr(h, "_pas", False) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        handler._pas = True  # type: ignore[attr-defined]
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    for noisy in ("peewee", "urllib3", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # Per-symbol provider failures are recorded in download_errors and shown
    # in /api/health; yfinance's own ERROR lines would only duplicate them.
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def create_app(settings: Settings | None = None, provider: MarketDataProvider | None = None, background: bool = True, **services_kwargs) -> FastAPI:
    configure_logging()
    settings = settings or load_settings()
    configure_ssl(settings.ssl_cache_dir)  # before yfinance is imported

    from .api.p3 import router as p3_router
    from .api.technical import router as technical_router
    from .api.indices import router as indices_router
    from .api.stage import router as stage_router
    from .api.signals import router as signals_router
    from .api.routes import router
    from .services import Services

    services = Services(settings, provider=provider, **services_kwargs)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        services.startup(background=background)
        log.info("Indian Price Action Screener API ready on port %s", settings.port)
        try:
            yield
        finally:
            services.shutdown()

    app = FastAPI(
        title="Indian Price Action Screener",
        version="0.1.0",
        description="Rule-based NSE chart-structure screener. Not investment advice.",
        lifespan=lifespan,
    )
    app.state.services = services
    app.include_router(router)
    app.include_router(p3_router)
    app.include_router(technical_router)
    app.include_router(indices_router)
    app.include_router(stage_router)
    app.include_router(signals_router)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        error_id = uuid.uuid4().hex[:12]
        log.exception("unhandled error %s on %s %s", error_id, request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "internal server error", "error_id": error_id})

    return app
