from fastapi import APIRouter
from sqlalchemy import select

from app.deps import CurrentUser, DbSession
from app.models import Entity
from app.schemas import EntityRead

router = APIRouter(prefix="/entities", tags=["entities"])


@router.get("", response_model=list[EntityRead])
async def list_entities(user: CurrentUser, db: DbSession) -> list[Entity]:
    return list((await db.scalars(select(Entity).order_by(Entity.name))).all())
