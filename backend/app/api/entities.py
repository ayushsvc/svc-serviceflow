from fastapi import APIRouter
from sqlalchemy import select

from app.deps import CurrentUser, DbSession
from app.models import Entity
from app.schemas import EntityRead

router = APIRouter(prefix="/entities", tags=["entities"])


@router.get("", response_model=list[EntityRead])
async def list_entities(user: CurrentUser, db: DbSession) -> list[Entity]:
    visible = {user.entity_id}
    # Only the existing admin-level right ("*") widens the list, and only to descendants of the caller's own entity.
    if (user.profile.rights or {}).get("*") is True:
        frontier = {user.entity_id}
        while frontier:
            children = set((await db.scalars(select(Entity.id).where(Entity.parent_id.in_(frontier)))).all()) - visible
            visible |= children
            frontier = children
    return list((await db.scalars(select(Entity).where(Entity.id.in_(visible)).order_by(Entity.name))).all())
