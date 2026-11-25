import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import attachments, audit, auth, automation, entities, field_engine, notifications, sla, tickets, workflows
from app.core.config import get_settings

logger = logging.getLogger(__name__)


async def _periodic_scheduler():
    """In-process periodic scheduler replacing Celery Beat.
    Periodically checks SLA breaches, recurring tickets, solved ticket auto-closure,
    and pending ticket nudges every 60 seconds.
    """
    from app.workers.tasks import (
        _auto_close_solved_tickets,
        _check_sla_breaches,
        _create_recurring_tickets,
        _nudge_pending_tickets,
    )

    # Initial delay on boot before starting background checks
    await asyncio.sleep(5)
    while True:
        try:
            await _check_sla_breaches()
        except Exception as exc:
            logger.warning("Error running in-process SLA breach check: %s", exc)

        try:
            await _create_recurring_tickets()
        except Exception as exc:
            logger.warning("Error running in-process recurring tickets: %s", exc)

        try:
            await _auto_close_solved_tickets()
        except Exception as exc:
            logger.warning("Error running in-process auto close check: %s", exc)

        try:
            await _nudge_pending_tickets()
        except Exception as exc:
            logger.warning("Error running in-process pending nudge check: %s", exc)

        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            break


@asynccontextmanager
async def lifespan(_app: FastAPI):
    scheduler_task = asyncio.create_task(_periodic_scheduler())
    try:
        yield
    finally:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title=get_settings().app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response
app.include_router(auth.router, prefix="/api/v1")
app.include_router(entities.router, prefix="/api/v1")
app.include_router(notifications.router, prefix="/api/v1")
app.include_router(field_engine.router, prefix="/api/v1")
app.include_router(tickets.router, prefix="/api/v1")
app.include_router(attachments.router, prefix="/api/v1")
app.include_router(automation.router, prefix="/api/v1")
app.include_router(audit.router, prefix="/api/v1")
app.include_router(workflows.router, prefix="/api/v1")
app.include_router(sla.router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": get_settings().app_name}
