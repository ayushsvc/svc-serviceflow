from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.deps import CurrentUser, DbSession, require_admin
from app.models import BusinessRule, NotificationTemplate, RecurringTicket, SLA, User
from app.schemas_automation import NotificationTemplateCreate, NotificationTemplateRead, RecurringTicketCreate, RecurringTicketRead, RuleCreate, RuleRead, SLACreate, SLARead
from app.services.content import sanitize_rich_text

router = APIRouter(tags=["automation"])


@router.get("/slas", response_model=list[SLARead])
async def list_slas(user: CurrentUser, db: DbSession) -> list[SLA]:
    return list((await db.scalars(select(SLA).where(SLA.entity_id == user.entity_id).order_by(SLA.name))).all())


@router.post("/slas", response_model=SLARead, status_code=status.HTTP_201_CREATED)
async def create_sla(payload: SLACreate, user: CurrentUser, db: DbSession) -> SLA:
    require_admin(user)
    sla = SLA(entity_id=user.entity_id, **payload.model_dump())
    db.add(sla)
    await db.commit()
    await db.refresh(sla)
    return sla


@router.get("/rules", response_model=list[RuleRead])
async def list_rules(user: CurrentUser, db: DbSession) -> list[BusinessRule]:
    return list((await db.scalars(select(BusinessRule).where(BusinessRule.entity_id == user.entity_id).order_by(BusinessRule.order))).all())


@router.post("/rules", response_model=RuleRead, status_code=status.HTTP_201_CREATED)
async def create_rule(payload: RuleCreate, user: CurrentUser, db: DbSession) -> BusinessRule:
    require_admin(user)
    rule = BusinessRule(entity_id=user.entity_id, **payload.model_dump())
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.get("/notification-templates", response_model=list[NotificationTemplateRead])
async def list_notification_templates(user: CurrentUser, db: DbSession) -> list[NotificationTemplate]:
    return list((await db.scalars(select(NotificationTemplate).where(NotificationTemplate.entity_id == user.entity_id).order_by(NotificationTemplate.event))).all())


@router.post("/notification-templates", response_model=NotificationTemplateRead, status_code=status.HTTP_201_CREATED)
async def create_notification_template(payload: NotificationTemplateCreate, user: CurrentUser, db: DbSession) -> NotificationTemplate:
    require_admin(user)
    values = payload.model_dump()
    values["body_jinja"] = sanitize_rich_text(values["body_jinja"])
    template = NotificationTemplate(entity_id=user.entity_id, **values)
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return template


@router.get("/recurring", response_model=list[RecurringTicketRead])
async def list_recurring(user: CurrentUser, db: DbSession) -> list[RecurringTicket]:
    return list((await db.scalars(select(RecurringTicket).where(RecurringTicket.entity_id == user.entity_id).order_by(RecurringTicket.next_run_at))).all())


@router.post("/recurring", response_model=RecurringTicketRead, status_code=status.HTTP_201_CREATED)
async def create_recurring(payload: RecurringTicketCreate, user: CurrentUser, db: DbSession) -> RecurringTicket:
    require_admin(user)
    requester = await db.scalar(select(User).where(User.id == payload.requester_id, User.entity_id == user.entity_id, User.is_active.is_(True)))
    if requester is None:
        raise HTTPException(status_code=422, detail="Requester is outside your entity or inactive")
    values = payload.model_dump()
    values["description"] = sanitize_rich_text(values["description"])
    recurring = RecurringTicket(entity_id=user.entity_id, **values)
    db.add(recurring)
    await db.commit()
    await db.refresh(recurring)
    return recurring
