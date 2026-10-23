from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.deps import CurrentUser, DbSession, require_admin
from app.models import AuditLog, User

router = APIRouter(tags=["audit"])


class AuditRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    entity: str
    entity_id: int | None
    action: str
    actor_id: int | None
    diff: dict
    ts: datetime


@router.get("/audit", response_model=list[AuditRead])
async def list_audit(
    user: CurrentUser,
    db: DbSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    action: str | None = None,
) -> list[AuditLog]:
    require_admin(user)
    query = (
        select(AuditLog)
        .join(User, User.id == AuditLog.actor_id)
        .where(AuditLog.actor_id.is_not(None), User.entity_id == user.entity_id)
        .order_by(AuditLog.ts.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    if action:
        query = query.where(AuditLog.action == action)
    return list((await db.scalars(query)).all())
