from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import attachments, audit, auth, automation, entities, field_engine, notifications, tickets, workflows
from app.core.config import get_settings


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield


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


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": get_settings().app_name}
