from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


async def record_audit(
    db: AsyncSession,
    *,
    entity: str,
    action: str,
    actor_id: int | None,
    entity_id: int | None = None,
    diff: dict | None = None,
) -> None:
    db.add(AuditLog(entity=entity, entity_id=entity_id, action=action, actor_id=actor_id, diff=diff or {}))
