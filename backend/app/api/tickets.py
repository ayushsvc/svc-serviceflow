import json
import math

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pathlib import Path
from uuid import uuid4
from sqlalchemy import String, and_, desc, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from app.deps import CurrentUser, DbSession, require_admin, require_helpdesk
from app.models import BusinessRule, Category, Group, ListProfile, PriorityMatrix, SLA, Ticket, TicketActor, TicketDocument, TicketFollowup, TicketLink, TicketSolution, TicketTask, TicketTypeDefinition, User
from app.schemas_tickets import ActorCreate, ActorRead, CategoryCreate, CategoryRead, FollowupCreate, FollowupRead, GroupRead, LinkCreate, LinkRead, ListProfileCreate, ListProfileRead, ListProfileUpdate, RequesterRead, SolutionCreate, SolutionRead, TaskCreate, TaskRead, TicketCreate, TicketListResponse, TicketRead, TicketUpdate
from app.services.automation import calculate_sla_deadlines, conditions_match
from app.services.tickets import calculate_priority, transition_status
from app.workers.tasks import send_ticket_notification
from app.services.audit import record_audit
from app.services.field_engine import store_field_values, validate_custom_fields
from app.services.content import sanitize_rich_text

router = APIRouter(tags=["tickets"])
UPLOAD_ROOT = Path("/app/data/documents")
ALLOWED_DOCUMENT_TYPES = {"application/pdf", "image/png", "image/jpeg", "text/plain"}
PROFILE_FILTER_KEYS = {"search", "status", "priority", "sort", "direction", "page_size"}
PROFILE_SORT_FIELDS = {"id", "opened_at", "priority", "status", "title"}
PROFILE_COLUMNS = {"id", "title", "status", "priority", "opened_at", "requester_name", "requester_active", "requester_vip", "flagged", "assignee_id", "ttr_due_at"}
PROFILE_STATUSES = {"NEW", "ASSIGNED", "PLANNED", "PENDING", "SOLVED", "CLOSED"}


def escape_ticket_search(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def validate_profile_config(kind: str, config: dict) -> dict:
    if kind not in {"filter", "column"} or not isinstance(config, dict):
        raise HTTPException(status_code=422, detail="Invalid profile configuration")
    try:
        encoded_config = json.dumps(config, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Profile config must contain valid JSON values") from exc
    if len(encoded_config) > 8192:
        raise HTTPException(status_code=422, detail="Profile config must not exceed 8 KB")

    def validate_values(value: object, depth: int = 1) -> None:
        if isinstance(value, dict):
            if depth > 3 or any(not isinstance(key, str) for key in value):
                raise HTTPException(status_code=422, detail="Profile config nesting must not exceed 3 levels")
            for nested in value.values():
                validate_values(nested, depth + 1)
        elif isinstance(value, list):
            if depth > 3:
                raise HTTPException(status_code=422, detail="Profile config nesting must not exceed 3 levels")
            for nested in value:
                validate_values(nested, depth + 1)
        elif type(value) not in {str, int, float, bool} or (type(value) is float and not math.isfinite(value)):
            raise HTTPException(status_code=422, detail="Profile config values must be strings, numbers, or booleans")

    validate_values(config)
    allowed = PROFILE_FILTER_KEYS if kind == "filter" else {"columns"}
    unknown = set(config) - allowed
    if unknown:
        raise HTTPException(status_code=422, detail="Unsupported profile config key")
    if kind == "column":
        columns = config.get("columns")
        if not isinstance(columns, list) or not columns or any(not isinstance(column, str) or column not in PROFILE_COLUMNS for column in columns):
            raise HTTPException(status_code=422, detail="Column profile must contain allowed columns")
        if len(columns) != len(set(columns)):
            raise HTTPException(status_code=422, detail="Column profile contains duplicate columns")
    if kind == "filter":
        if "search" in config and (not isinstance(config["search"], str) or len(config["search"]) > 160):
            raise HTTPException(status_code=422, detail="Filter search must be a string of at most 160 characters")
        if "status" in config and (not isinstance(config["status"], str) or config["status"] not in PROFILE_STATUSES):
            raise HTTPException(status_code=422, detail="Unsupported filter status")
        if "priority" in config and (type(config["priority"]) is not int or not 1 <= config["priority"] <= 5):
            raise HTTPException(status_code=422, detail="Filter priority must be between 1 and 5")
        if "page_size" in config and (type(config["page_size"]) is not int or not 1 <= config["page_size"] <= 100):
            raise HTTPException(status_code=422, detail="Filter page_size must be between 1 and 100")
        if "direction" in config and (not isinstance(config["direction"], str) or config["direction"] not in {"asc", "desc"}):
            raise HTTPException(status_code=422, detail="Filter direction must be asc or desc")
        if "sort" in config and (not isinstance(config["sort"], str) or config["sort"] not in PROFILE_SORT_FIELDS):
            raise HTTPException(status_code=422, detail="Unsupported filter sort")
    return config


async def lock_profile_owner(db: DbSession, user: CurrentUser) -> None:
    await db.scalar(
        select(User.id)
        .where(User.id == user.id, User.entity_id == user.entity_id)
        .with_for_update()
    )


async def ensure_profile_name_available(
    db: DbSession,
    *,
    user: CurrentUser,
    kind: str,
    name: str,
    exclude_id: int | None = None,
) -> None:
    query = select(ListProfile.id).where(
        ListProfile.user_id == user.id,
        ListProfile.kind == kind,
        ListProfile.name == name,
    )
    if exclude_id is not None:
        query = query.where(ListProfile.id != exclude_id)
    if await db.scalar(query) is not None:
        raise HTTPException(status_code=409, detail="A profile with this name already exists")


async def commit_profile_mutation(db: DbSession) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if "uq_list_profiles_user_kind_name" in str(exc.orig):
            raise HTTPException(status_code=409, detail="A profile with this name already exists") from exc
        raise


@router.get("/categories", response_model=list[CategoryRead])
async def categories(user: CurrentUser, db: DbSession) -> list[Category]:
    return list((await db.scalars(select(Category).where(Category.entity_id == user.entity_id).order_by(Category.name))).all())


@router.post("/categories", response_model=CategoryRead, status_code=status.HTTP_201_CREATED)
async def create_category(payload: CategoryCreate, user: CurrentUser, db: DbSession) -> Category:
    require_helpdesk(user)
    parent = await db.scalar(select(Category).where(Category.id == payload.parent_id, Category.entity_id == user.entity_id)) if payload.parent_id is not None else None
    if payload.parent_id is not None and parent is None:
        raise HTTPException(status_code=422, detail="Parent category is outside your entity")
    level = (parent.level + 1) if parent else 1
    if level > 4:
        raise HTTPException(status_code=422, detail="Category hierarchy cannot exceed four levels")
    category = Category(name=payload.name, parent_id=payload.parent_id, entity_id=user.entity_id, level=level)
    db.add(category)
    await db.commit()
    await db.refresh(category)
    return category


@router.get("/groups", response_model=list[GroupRead])
async def groups(user: CurrentUser, db: DbSession) -> list[Group]:
    return list((await db.scalars(select(Group).where(Group.entity_id == user.entity_id).order_by(Group.name))).all())


@router.get("/requesters", response_model=list[RequesterRead])
async def requesters(user: CurrentUser, db: DbSession, search: str | None = Query(default=None, min_length=1, max_length=160)) -> list[User]:
    require_helpdesk(user)
    query = select(User).join(User.profile).where(
        User.entity_id == user.entity_id,
        User.is_active.is_(True),
        User.profile.has(interface="self_service"),
    ).order_by(User.name).limit(50)
    if search:
        query = query.where((User.name.ilike(f"%{search}%")) | (User.email.ilike(f"%{search}%")))
    return list((await db.scalars(query)).all())


@router.get("/agents", response_model=list[RequesterRead])
async def agents(user: CurrentUser, db: DbSession) -> list[User]:
    require_helpdesk(user)
    query = select(User).where(
        User.entity_id == user.entity_id,
        User.is_active.is_(True),
        User.profile.has(interface="helpdesk"),
    ).order_by(User.name).limit(50)
    return list((await db.scalars(query)).all())


@router.get("/tickets", response_model=TicketListResponse, response_model_exclude_none=True)
async def list_tickets(
    user: CurrentUser,
    db: DbSession,
    ticket_status: str | None = Query(default=None, alias="status"),
    priority: int | None = Query(default=None, ge=1, le=5),
    search: str | None = Query(default=None, max_length=160),
    sort: str = Query(default="opened_at"),
    direction: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict:
    sort_columns = {
        "id": Ticket.id,
        "opened_at": Ticket.opened_at,
        "priority": Ticket.priority,
        "status": Ticket.status,
        "title": Ticket.title,
    }
    if sort not in sort_columns:
        raise HTTPException(status_code=422, detail="Unsupported ticket sort")
    conditions = [Ticket.entity_id == user.entity_id, Ticket.deleted_at.is_(None)]
    if user.profile.interface == "self_service":
        conditions.append(Ticket.requester_id == user.id)
    if ticket_status:
        conditions.append(Ticket.status == ticket_status)
    if priority is not None:
        conditions.append(Ticket.priority == priority)
    if search:
        escaped = escape_ticket_search(search.strip())
        pattern = f"%{escaped}%"
        conditions.append(or_(
            Ticket.title.ilike(pattern, escape="\\"),
            Ticket.description.ilike(pattern, escape="\\"),
            Ticket.id.cast(String).ilike(pattern, escape="\\"),
        ))
    base = select(Ticket, User).join(User, User.id == Ticket.requester_id).where(*conditions)
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    order_column = sort_columns[sort]
    order_expression = order_column.asc() if direction == "asc" else desc(order_column)
    result = await db.execute(base.order_by(order_expression, Ticket.id.desc()).offset((page - 1) * page_size).limit(page_size))
    rows = [
        {
            **{column: getattr(ticket, column) for column in TicketRead.model_fields if hasattr(ticket, column)},
            "requester_name": requester.name if user.profile.interface != "self_service" else None,
            "requester_active": requester.is_active if user.profile.interface != "self_service" else None,
            "requester_vip": False if user.profile.interface != "self_service" else None,
            "flagged": ticket.flagged if user.profile.interface != "self_service" else None,
        }
        for ticket, requester in result.all()
    ]
    return {
        "items": rows,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": max(1, (total + page_size - 1) // page_size),
    }


@router.get("/tickets/team-counts")
async def ticket_team_counts(user: CurrentUser, db: DbSession) -> list[dict[str, int | str | None]]:
    conditions = [Ticket.entity_id == user.entity_id, Ticket.deleted_at.is_(None)]
    if user.profile.interface == "self_service":
        conditions.append(Ticket.requester_id == user.id)
    rows = await db.execute(
        select(Group.id, func.count(Ticket.id))
        .outerjoin(
            TicketTypeDefinition,
            and_(
                TicketTypeDefinition.id == Ticket.ticket_type_id,
                or_(TicketTypeDefinition.entity_id.is_(None), TicketTypeDefinition.entity_id == user.entity_id),
            ),
        )
        .outerjoin(
            Group,
            and_(
                Group.id == TicketTypeDefinition.default_team_id,
                Group.entity_id == user.entity_id,
            ),
        )
        .where(*conditions)
        .group_by(Group.id)
    )
    return [{"team_id": team_id, "count": count} for team_id, count in rows.all()]


@router.get("/tickets/list-profiles", response_model=list[ListProfileRead])
async def list_profiles(user: CurrentUser, db: DbSession) -> list[ListProfile]:
    result = await db.scalars(
        select(ListProfile)
        .where(ListProfile.user_id == user.id, ListProfile.entity_id == user.entity_id)
        .order_by(ListProfile.kind, ListProfile.name)
    )
    return list(result.all())


@router.get("/tickets/list-profiles/{profile_id}", response_model=ListProfileRead)
async def get_list_profile(profile_id: int, user: CurrentUser, db: DbSession) -> ListProfile:
    profile = await db.scalar(
        select(ListProfile).where(
            ListProfile.id == profile_id,
            ListProfile.user_id == user.id,
            ListProfile.entity_id == user.entity_id,
        )
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="List profile not found")
    return profile


@router.post("/tickets/list-profiles", response_model=ListProfileRead, status_code=status.HTTP_201_CREATED)
async def create_list_profile(payload: ListProfileCreate, user: CurrentUser, db: DbSession) -> ListProfile:
    config = validate_profile_config(payload.kind, payload.config)
    await lock_profile_owner(db, user)
    await ensure_profile_name_available(db, user=user, kind=payload.kind, name=payload.name)
    if payload.is_default:
        await db.execute(
            update(ListProfile)
            .where(ListProfile.user_id == user.id, ListProfile.entity_id == user.entity_id, ListProfile.kind == payload.kind)
            .values(is_default=False)
        )
    profile = ListProfile(
        user_id=user.id,
        entity_id=user.entity_id,
        kind=payload.kind,
        name=payload.name,
        config=config,
        is_default=payload.is_default,
    )
    db.add(profile)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        if "uq_list_profiles_user_kind_name" in str(exc.orig):
            raise HTTPException(status_code=409, detail="A profile with this name already exists") from exc
        raise
    await record_audit(db, entity="list_profile", entity_id=profile.id, action="created", actor_id=user.id, diff=payload.model_dump())
    await commit_profile_mutation(db)
    await db.refresh(profile)
    return profile


@router.patch("/tickets/list-profiles/{profile_id}", response_model=ListProfileRead)
async def update_list_profile(profile_id: int, payload: ListProfileUpdate, user: CurrentUser, db: DbSession) -> ListProfile:
    await lock_profile_owner(db, user)
    profile = await db.scalar(
        select(ListProfile).where(
            ListProfile.id == profile_id,
            ListProfile.user_id == user.id,
            ListProfile.entity_id == user.entity_id,
        )
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="List profile not found")
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is None:
        raise HTTPException(status_code=422, detail="Profile name cannot be null")
    if "config" in changes:
        if changes["config"] is None:
            raise HTTPException(status_code=422, detail="Profile config cannot be null")
        changes["config"] = validate_profile_config(profile.kind, changes["config"])
    if "name" in changes:
        await ensure_profile_name_available(
            db,
            user=user,
            kind=profile.kind,
            name=changes["name"],
            exclude_id=profile.id,
        )
    for key, value in changes.items():
        setattr(profile, key, value)
    await record_audit(db, entity="list_profile", entity_id=profile.id, action="updated", actor_id=user.id, diff=changes)
    await commit_profile_mutation(db)
    await db.refresh(profile)
    return profile


@router.post("/tickets/list-profiles/{profile_id}/set-default", response_model=ListProfileRead)
async def set_default_list_profile(profile_id: int, user: CurrentUser, db: DbSession) -> ListProfile:
    await lock_profile_owner(db, user)
    profile = await db.scalar(
        select(ListProfile).where(
            ListProfile.id == profile_id,
            ListProfile.user_id == user.id,
            ListProfile.entity_id == user.entity_id,
        )
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="List profile not found")
    await db.execute(
        update(ListProfile)
        .where(
            ListProfile.user_id == user.id,
            ListProfile.entity_id == user.entity_id,
            ListProfile.kind == profile.kind,
            ListProfile.id != profile.id,
        )
        .values(is_default=False)
    )
    profile.is_default = True
    await record_audit(
        db,
        entity="list_profile",
        entity_id=profile.id,
        action="set_default",
        actor_id=user.id,
        diff={"kind": profile.kind, "is_default": True},
    )
    await commit_profile_mutation(db)
    await db.refresh(profile)
    return profile


@router.delete("/tickets/list-profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_list_profile(profile_id: int, user: CurrentUser, db: DbSession) -> None:
    await lock_profile_owner(db, user)
    profile = await db.scalar(
        select(ListProfile).where(
            ListProfile.id == profile_id,
            ListProfile.user_id == user.id,
            ListProfile.entity_id == user.entity_id,
        )
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="List profile not found")
    await record_audit(db, entity="list_profile", entity_id=profile.id, action="deleted", actor_id=user.id, diff={})
    await db.delete(profile)
    await commit_profile_mutation(db)


@router.get("/tickets/{ticket_id}", response_model=TicketRead)
async def get_ticket(ticket_id: int, user: CurrentUser, db: DbSession) -> Ticket:
    ticket = await db.scalar(select(Ticket).where(Ticket.id == ticket_id, Ticket.entity_id == user.entity_id, Ticket.deleted_at.is_(None)))
    if ticket is None or (user.profile.interface == "self_service" and ticket.requester_id != user.id):
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@router.post("/tickets", response_model=TicketRead, status_code=status.HTTP_201_CREATED)
async def create_ticket(payload: TicketCreate, user: CurrentUser, db: DbSession) -> Ticket:
    field_values = dict(payload.custom_fields)
    trusted_field_values: dict[str, object] = {}
    if user.profile.interface == "self_service":
        field_values.pop("user", None)
        field_values.pop("organisation", None)
        trusted_field_values = {"user": user.id, "organisation": user.entity_id}
    if isinstance(payload.description, str):
        payload.description = sanitize_rich_text(payload.description)
    ticket_type_id = payload.ticket_type_id or await db.scalar(select(TicketTypeDefinition.id).where(
        TicketTypeDefinition.itil_class == ("INCIDENT" if payload.type == "INCIDENT" else "REQUEST"),
        (TicketTypeDefinition.entity_id == user.entity_id) | (TicketTypeDefinition.entity_id.is_(None)),
        TicketTypeDefinition.is_active.is_(True),
    ))
    if ticket_type_id is None:
        raise HTTPException(status_code=422, detail="A valid ticket type is required")
    field_values = await validate_custom_fields(
        db,
        ticket_type_id,
        user.entity_id,
        user.profile.name,
        field_values,
        trusted_values=trusted_field_values,
    )
    if payload.category_id is not None and await db.scalar(select(Category).where(Category.id == payload.category_id, Category.entity_id == user.entity_id)) is None:
        raise HTTPException(status_code=422, detail="Category is outside your entity")
    requested_sla_id = payload.sla_id
    if requested_sla_id is not None and user.profile.name != "Administrator":
        raise HTTPException(status_code=403, detail="Only administrators may override the SLA")
    requester_id = user.id
    if user.profile.interface == "helpdesk" and payload.requester_id is not None:
        requester = await db.scalar(select(User).where(
            User.id == payload.requester_id,
            User.entity_id == user.entity_id,
            User.is_active.is_(True),
        ))
        if requester is None:
            raise HTTPException(status_code=422, detail="Requester is outside your entity or inactive")
        requester_id = requester.id
    if payload.assignee_id is not None and await db.scalar(select(User).where(User.id == payload.assignee_id, User.entity_id == user.entity_id, User.is_active.is_(True))) is None:
        raise HTTPException(status_code=422, detail="Assignee is outside your entity or inactive")
    matrix = await db.scalar(select(PriorityMatrix).where(PriorityMatrix.entity_id == user.entity_id))
    priority = calculate_priority(payload.urgency, payload.impact) if matrix is None else matrix.matrix[payload.urgency - 1][payload.impact - 1]
    ticket = Ticket(**payload.model_dump(exclude={"type", "sla_id", "requester_id", "ticket_type_id", "custom_fields"}), type=payload.type, ticket_type_id=ticket_type_id, custom_fields=field_values, priority=priority, entity_id=user.entity_id, requester_id=requester_id)
    if ticket.assignee_id:
        ticket.status = "ASSIGNED"
    db.add(ticket)
    await db.flush()
    sla = await db.scalar(select(SLA).where(SLA.id == requested_sla_id, SLA.entity_id == user.entity_id, SLA.is_active.is_(True))) if requested_sla_id else await db.scalar(select(SLA).where(SLA.entity_id == user.entity_id, SLA.is_active.is_(True)).order_by(SLA.id))
    if requested_sla_id and sla is None:
        raise HTTPException(status_code=422, detail="SLA is outside your entity or inactive")
    if sla:
        ticket.sla_id = sla.id
        calculate_sla_deadlines(ticket, sla.tto_minutes, sla.ttr_minutes)
    rules = await db.scalars(select(BusinessRule).where(BusinessRule.entity_id == user.entity_id, BusinessRule.trigger == "ON_CREATE", BusinessRule.is_active.is_(True)).order_by(BusinessRule.order))
    for rule in rules:
        if conditions_match(ticket, rule.conditions):
            actions = rule.actions
            if "set_priority" in actions:
                ticket.priority = int(actions["set_priority"])
            if "set_category" in actions:
                category = await db.scalar(select(Category).where(Category.id == int(actions["set_category"]), Category.entity_id == user.entity_id))
                if category:
                    ticket.category_id = category.id
            if "set_assignee" in actions:
                assignee = await db.scalar(select(User).where(User.id == int(actions["set_assignee"]), User.entity_id == user.entity_id, User.is_active.is_(True)))
                if assignee:
                    ticket.assignee_id = assignee.id
                    ticket.status = "ASSIGNED"
            if "set_sla" in actions:
                rule_sla = await db.scalar(select(SLA).where(SLA.id == int(actions["set_sla"]), SLA.entity_id == user.entity_id, SLA.is_active.is_(True)))
                if rule_sla:
                    ticket.sla_id = rule_sla.id
                    calculate_sla_deadlines(ticket, rule_sla.tto_minutes, rule_sla.ttr_minutes)
    db.add(TicketActor(ticket_id=ticket.id, actor_type="USER", actor_id=requester_id, role="REQUESTER"))
    await store_field_values(db, ticket, field_values)
    await db.commit()
    await db.refresh(ticket)
    await record_audit(db, entity="ticket", entity_id=ticket.id, action="created", actor_id=user.id, diff={"status": ticket.status})
    await db.commit()
    send_ticket_notification.delay(ticket.id, "created")
    return ticket


@router.patch("/tickets/{ticket_id}", response_model=TicketRead)
async def update_ticket(ticket_id: int, payload: TicketUpdate, user: CurrentUser, db: DbSession) -> Ticket:
    require_helpdesk(user)
    ticket = await db.scalar(select(Ticket).where(Ticket.id == ticket_id, Ticket.entity_id == user.entity_id, Ticket.deleted_at.is_(None)))
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    values = payload.model_dump(exclude_unset=True)
    if "description" in values and isinstance(values["description"], str):
        values["description"] = sanitize_rich_text(values["description"])
    if "ticket_type_id" in values:
        values["custom_fields"] = await validate_custom_fields(
            db, values["ticket_type_id"], user.entity_id, user.profile.name, values.get("custom_fields", {})
        )
    elif "custom_fields" in values and ticket.ticket_type_id is not None:
        values["custom_fields"] = await validate_custom_fields(
            db, ticket.ticket_type_id, user.entity_id, user.profile.name, values["custom_fields"], ticket.custom_fields
        )
    if "category_id" in values and values["category_id"] is not None and await db.scalar(select(Category).where(Category.id == values["category_id"], Category.entity_id == user.entity_id)) is None:
        raise HTTPException(status_code=422, detail="Category is outside your entity")
    if "assignee_id" in values and values["assignee_id"] is not None and await db.scalar(select(User).where(User.id == values["assignee_id"], User.entity_id == user.entity_id, User.is_active.is_(True))) is None:
        raise HTTPException(status_code=422, detail="Assignee is outside your entity or inactive")
    if "status" in values:
        values.update(transition_status(ticket.status, values["status"], values.get("assignee_id", ticket.assignee_id) is not None))
    if "urgency" in values or "impact" in values:
        matrix = await db.scalar(select(PriorityMatrix).where(PriorityMatrix.entity_id == user.entity_id))
        values["priority"] = calculate_priority(values.get("urgency", ticket.urgency), values.get("impact", ticket.impact)) if matrix is None else matrix.matrix[values.get("urgency", ticket.urgency) - 1][values.get("impact", ticket.impact) - 1]
    custom_fields = values.pop("custom_fields", None)
    values.pop("ticket_type_id", None)
    for key, value in values.items():
        setattr(ticket, key, value)
    if custom_fields is not None:
        ticket.custom_fields = {**ticket.custom_fields, **custom_fields}
        await store_field_values(db, ticket, custom_fields)
    if ticket.assignee_id and ticket.status == "NEW":
        ticket.status = "ASSIGNED"
    rules = await db.scalars(select(BusinessRule).where(BusinessRule.entity_id == user.entity_id, BusinessRule.trigger == "ON_UPDATE", BusinessRule.is_active.is_(True)).order_by(BusinessRule.order))
    for rule in rules:
        if conditions_match(ticket, rule.conditions):
            actions = rule.actions
            if "set_priority" in actions:
                ticket.priority = int(actions["set_priority"])
            if "set_sla" in actions:
                sla = await db.scalar(select(SLA).where(SLA.id == int(actions["set_sla"]), SLA.entity_id == user.entity_id, SLA.is_active.is_(True)))
                if sla:
                    ticket.sla_id = sla.id
                    calculate_sla_deadlines(ticket, sla.tto_minutes, sla.ttr_minutes)
    await db.commit()
    await record_audit(db, entity="ticket", entity_id=ticket.id, action="updated", actor_id=user.id, diff=values)
    await db.commit()
    await db.refresh(ticket)
    return ticket


@router.delete("/tickets/{ticket_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ticket(ticket_id: int, user: CurrentUser, db: DbSession) -> None:
    require_admin(user)
    ticket = await db.scalar(select(Ticket).where(Ticket.id == ticket_id, Ticket.entity_id == user.entity_id, Ticket.deleted_at.is_(None)))
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    from datetime import UTC, datetime
    ticket.deleted_at = datetime.now(UTC)
    await record_audit(db, entity="ticket", entity_id=ticket.id, action="deleted", actor_id=user.id, diff={"deleted_at": ticket.deleted_at.isoformat()})
    await db.commit()


@router.get("/tickets/{ticket_id}/actors", response_model=list[ActorRead])
async def actors(ticket_id: int, user: CurrentUser, db: DbSession) -> list[TicketActor]:
    ticket = await db.scalar(select(Ticket).where(Ticket.id == ticket_id, Ticket.entity_id == user.entity_id, Ticket.deleted_at.is_(None)))
    if ticket is None or (user.profile.interface == "self_service" and ticket.requester_id != user.id):
        raise HTTPException(status_code=404, detail="Ticket not found")
    return list((await db.scalars(select(TicketActor).where(TicketActor.ticket_id == ticket_id).order_by(TicketActor.id))).all())


@router.post("/tickets/{ticket_id}/actors", response_model=ActorRead, status_code=status.HTTP_201_CREATED)
async def add_actor(ticket_id: int, payload: ActorCreate, user: CurrentUser, db: DbSession) -> TicketActor:
    require_helpdesk(user)
    ticket = await db.scalar(select(Ticket).where(Ticket.id == ticket_id, Ticket.entity_id == user.entity_id, Ticket.deleted_at.is_(None)))
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if payload.actor_type == "USER":
        valid = await db.scalar(select(User).where(User.id == payload.actor_id, User.entity_id == user.entity_id, User.is_active.is_(True)))
    else:
        valid = await db.scalar(select(Group).where(Group.id == payload.actor_id, Group.entity_id == user.entity_id))
    if valid is None:
        raise HTTPException(status_code=422, detail="Actor is outside your entity or inactive")
    actor = TicketActor(ticket_id=ticket_id, **payload.model_dump())
    db.add(actor)
    if payload.role == "ASSIGNEE" and payload.actor_type == "USER":
        ticket.assignee_id = payload.actor_id
        if ticket.status == "NEW":
            ticket.status = "ASSIGNED"
    await db.commit()
    await db.refresh(actor)
    await record_audit(db, entity="ticket_actor", entity_id=actor.id, action="created", actor_id=user.id, diff={"ticket_id": ticket.id, "role": actor.role})
    await db.commit()
    return actor


@router.get("/tickets/{ticket_id}/followups", response_model=list[FollowupRead])
async def followups(ticket_id: int, user: CurrentUser, db: DbSession) -> list[TicketFollowup]:
    ticket = await db.scalar(select(Ticket).where(Ticket.id == ticket_id, Ticket.entity_id == user.entity_id, Ticket.deleted_at.is_(None)))
    if ticket is None or (user.profile.interface == "self_service" and ticket.requester_id != user.id):
        raise HTTPException(status_code=404, detail="Ticket not found")
    query = select(TicketFollowup).where(TicketFollowup.ticket_id == ticket_id)
    if user.profile.interface == "self_service":
        query = query.where(TicketFollowup.is_private.is_(False))
    return list((await db.scalars(query.order_by(TicketFollowup.created_at))).all())


@router.post("/tickets/{ticket_id}/followups", response_model=FollowupRead, status_code=status.HTTP_201_CREATED)
async def add_followup(ticket_id: int, payload: FollowupCreate, user: CurrentUser, db: DbSession) -> TicketFollowup:
    ticket = await db.scalar(select(Ticket).where(Ticket.id == ticket_id, Ticket.entity_id == user.entity_id, Ticket.deleted_at.is_(None)))
    if ticket is None or (user.profile.interface == "self_service" and ticket.requester_id != user.id):
        raise HTTPException(status_code=404, detail="Ticket not found")
    if payload.is_private:
        require_helpdesk(user)
    values = payload.model_dump()
    values["body"] = sanitize_rich_text(values["body"])
    followup = TicketFollowup(ticket_id=ticket_id, author_id=user.id, **values)
    db.add(followup)
    await db.commit()
    await db.refresh(followup)
    await record_audit(db, entity="ticket_followup", entity_id=followup.id, action="created", actor_id=user.id, diff={"ticket_id": ticket.id, "is_private": followup.is_private})
    await db.commit()
    return followup


async def scoped_ticket(ticket_id: int, user: CurrentUser, db: DbSession) -> Ticket:
    ticket = await db.scalar(select(Ticket).where(Ticket.id == ticket_id, Ticket.entity_id == user.entity_id, Ticket.deleted_at.is_(None)))
    if ticket is None or (user.profile.interface == "self_service" and ticket.requester_id != user.id):
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@router.get("/tickets/{ticket_id}/tasks", response_model=list[TaskRead])
async def list_tasks(ticket_id: int, user: CurrentUser, db: DbSession) -> list[TicketTask]:
    await scoped_ticket(ticket_id, user, db)
    return list((await db.scalars(select(TicketTask).where(TicketTask.ticket_id == ticket_id).order_by(TicketTask.created_at))).all())


@router.post("/tickets/{ticket_id}/tasks", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create_task(ticket_id: int, payload: TaskCreate, user: CurrentUser, db: DbSession) -> TicketTask:
    require_helpdesk(user)
    await scoped_ticket(ticket_id, user, db)
    if payload.assignee_id is not None and await db.scalar(select(User).where(User.id == payload.assignee_id, User.entity_id == user.entity_id, User.is_active.is_(True))) is None:
        raise HTTPException(status_code=422, detail="Task assignee is outside your entity")
    values = payload.model_dump()
    values["body"] = sanitize_rich_text(values["body"])
    task = TicketTask(ticket_id=ticket_id, **values)
    db.add(task)
    await db.commit()
    await db.refresh(task)
    await record_audit(db, entity="ticket_task", entity_id=task.id, action="created", actor_id=user.id, diff={"ticket_id": ticket_id, "state": task.state})
    await db.commit()
    return task


@router.get("/tickets/{ticket_id}/solutions", response_model=list[SolutionRead])
async def list_solutions(ticket_id: int, user: CurrentUser, db: DbSession) -> list[TicketSolution]:
    await scoped_ticket(ticket_id, user, db)
    return list((await db.scalars(select(TicketSolution).where(TicketSolution.ticket_id == ticket_id).order_by(TicketSolution.created_at))).all())


@router.post("/tickets/{ticket_id}/solutions", response_model=SolutionRead, status_code=status.HTTP_201_CREATED)
async def propose_solution(ticket_id: int, payload: SolutionCreate, user: CurrentUser, db: DbSession) -> TicketSolution:
    require_helpdesk(user)
    await scoped_ticket(ticket_id, user, db)
    values = payload.model_dump()
    values["body"] = sanitize_rich_text(values["body"])
    solution = TicketSolution(ticket_id=ticket_id, author_id=user.id, **values)
    db.add(solution)
    await db.commit()
    await db.refresh(solution)
    await record_audit(db, entity="ticket_solution", entity_id=solution.id, action="proposed", actor_id=user.id, diff={"ticket_id": ticket_id, "status": solution.status})
    await db.commit()
    return solution


@router.post("/tickets/{ticket_id}/solutions/{solution_id}/approve", response_model=SolutionRead)
async def approve_solution(ticket_id: int, solution_id: int, user: CurrentUser, db: DbSession) -> TicketSolution:
    ticket = await scoped_ticket(ticket_id, user, db)
    solution = await db.scalar(select(TicketSolution).where(TicketSolution.id == solution_id, TicketSolution.ticket_id == ticket.id))
    if solution is None:
        raise HTTPException(status_code=404, detail="Solution not found")
    if user.profile.interface == "self_service" and ticket.requester_id != user.id:
        raise HTTPException(status_code=403, detail="Only the requester can approve a solution")
    solution.status = "APPROVED"
    ticket.status = "CLOSED"
    from datetime import UTC, datetime
    ticket.solved_at = ticket.solved_at or datetime.now(UTC)
    ticket.closed_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(solution)
    await record_audit(db, entity="ticket_solution", entity_id=solution.id, action="approved", actor_id=user.id, diff={"ticket_id": ticket.id, "status": solution.status})
    await db.commit()
    return solution


@router.post("/tickets/{ticket_id}/documents", status_code=status.HTTP_201_CREATED)
async def upload_document(ticket_id: int, user: CurrentUser, db: DbSession, document: UploadFile = File(...)) -> dict[str, object]:
    ticket = await scoped_ticket(ticket_id, user, db)
    content = await document.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Document exceeds 10 MB limit")
    filename = Path(document.filename or "document").name
    if not document.filename or document.filename != filename or "\\" in document.filename or filename in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid document filename")
    detected_type = detect_document_type(content)
    if detected_type is None:
        raise HTTPException(status_code=415, detail="Unsupported document type")
    extension_types = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".txt": "text/plain"}
    if extension_types.get(Path(filename).suffix.lower()) != detected_type:
        raise HTTPException(status_code=415, detail="File extension does not match its content")
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid4().hex}-{filename}"
    path = UPLOAD_ROOT / stored_name
    path.write_bytes(content)
    record = TicketDocument(ticket_id=ticket_id, filename=filename, path=str(path), uploaded_by=user.id)
    db.add(record)
    await db.commit()
    await db.refresh(record)
    await record_audit(db, entity="ticket_document", entity_id=record.id, action="uploaded", actor_id=user.id, diff={"ticket_id": ticket.id, "filename": record.filename})
    await db.commit()
    return {"id": record.id, "filename": record.filename, "created_at": record.created_at}


def detect_document_type(content: bytes) -> str | None:
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if b"\x00" not in content:
        try:
            content.decode("utf-8")
            return "text/plain"
        except UnicodeDecodeError:
            return None
    return None


@router.get("/tickets/{ticket_id}/documents/{document_id}")
async def download_document(ticket_id: int, document_id: int, user: CurrentUser, db: DbSession) -> FileResponse:
    ticket = await scoped_ticket(ticket_id, user, db)
    document = await db.scalar(select(TicketDocument).where(TicketDocument.id == document_id, TicketDocument.ticket_id == ticket.id))
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    root = UPLOAD_ROOT.resolve()
    path = Path(document.path).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Document not found")
    response = FileResponse(path, filename=document.filename, media_type="application/octet-stream")
    response.headers["Content-Disposition"] = f'attachment; filename="{document.filename}"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.get("/tickets/{ticket_id}/links", response_model=list[LinkRead])
async def list_links(ticket_id: int, user: CurrentUser, db: DbSession) -> list[TicketLink]:
    await scoped_ticket(ticket_id, user, db)
    return list((await db.scalars(select(TicketLink).where(TicketLink.ticket_id == ticket_id).order_by(TicketLink.created_at))).all())


@router.post("/tickets/{ticket_id}/links", response_model=LinkRead, status_code=status.HTTP_201_CREATED)
async def link_ticket(ticket_id: int, payload: LinkCreate, user: CurrentUser, db: DbSession) -> TicketLink:
    require_helpdesk(user)
    ticket = await scoped_ticket(ticket_id, user, db)
    linked = await db.scalar(select(Ticket).where(Ticket.id == payload.linked_ticket_id, Ticket.entity_id == user.entity_id))
    if linked is None or linked.id == ticket.id:
        raise HTTPException(status_code=422, detail="Linked ticket is invalid")
    link = TicketLink(ticket_id=ticket.id, **payload.model_dump())
    db.add(link)
    await db.commit()
    await db.refresh(link)
    await record_audit(db, entity="ticket_link", entity_id=link.id, action="created", actor_id=user.id, diff={"ticket_id": ticket.id, "linked_ticket_id": linked.id})
    await db.commit()
    return link


@router.get("/tickets/{ticket_id}/timeline")
async def timeline(ticket_id: int, user: CurrentUser, db: DbSession) -> list[dict[str, object]]:
    await scoped_ticket(ticket_id, user, db)
    followups = await db.scalars(select(TicketFollowup).where(TicketFollowup.ticket_id == ticket_id).order_by(TicketFollowup.created_at))
    tasks = await db.scalars(select(TicketTask).where(TicketTask.ticket_id == ticket_id).order_by(TicketTask.created_at))
    solutions = await db.scalars(select(TicketSolution).where(TicketSolution.ticket_id == ticket_id).order_by(TicketSolution.created_at))
    events = [
        {"kind": "followup", "id": item.id, "body": item.body, "author_id": item.author_id, "private": item.is_private, "created_at": item.created_at}
        for item in followups if not item.is_private or user.profile.interface == "helpdesk"
    ]
    events += [{"kind": "task", "id": item.id, "body": item.body, "state": item.state, "created_at": item.created_at} for item in tasks]
    events += [{"kind": "solution", "id": item.id, "body": item.body, "state": item.status, "author_id": item.author_id, "created_at": item.created_at} for item in solutions]
    return sorted(events, key=lambda event: event["created_at"])
