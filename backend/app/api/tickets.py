import json
import math
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import String, and_, desc, false, func, or_, select, true, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.elements import ColumnElement
from app.deps import CurrentUser, DbSession, require_admin, require_helpdesk
from app.models import AuditLog, BusinessRule, Category, Entity, Group, GroupMember, ListProfile, PriorityMatrix, Profile, SLA, Ticket, TicketActor, TicketFollowup, TicketLink, TicketSolution, TicketTask, TicketTypeDefinition, TicketWorkflow, User, WorkflowApproval
from app.schemas_tickets import ActorCreate, ActorRead, AssignableUserRead, CategoryCreate, CategoryRead, FollowupCreate, FollowupRead, GroupRead, LinkCreate, LinkRead, ListProfileCreate, ListProfileRead, ListProfileUpdate, RequesterRead, SolutionCreate, SolutionRead, TaskCreate, TaskRead, TicketAssignRequest, TicketAssignResult, TicketBulkResult, TicketBulkUpdate, TicketCreate, TicketListResponse, TicketRead, TicketUpdate
from app.services.automation import calculate_sla_deadlines, conditions_match
from app.services.tickets import calculate_priority, priority_label, priority_options, priority_urgency_impact, requester_priority_value, transition_status
from app.workers.tasks import send_ticket_notification
from app.services.audit import record_audit
from app.services.field_engine import REQUESTER_FORM_FIELDS, field_role_for_user, store_field_values, validate_custom_fields
from app.services.content import sanitize_rich_text, sanitize_ticket_description
from app.services.bulk_tickets import apply_ticket_update, bulk_update_tickets as run_bulk_ticket_update, dispatch_ticket_update_effects

router = APIRouter(tags=["tickets"])
PROFILE_FILTER_KEYS = {"search", "status", "priority", "sort", "direction", "page_size"}
PROFILE_SORT_FIELDS = {"id", "opened_at", "priority", "status", "title"}
PROFILE_COLUMNS = {"id", "title", "status", "priority", "opened_at", "requester_name", "requester_active", "requester_vip", "flagged", "assignee_id", "ttr_due_at"}
PROFILE_STATUSES = {"NEW", "ASSIGNED", "PLANNED", "PENDING", "SOLVED", "CLOSED"}


def has_profile_right(user: User, right: str) -> bool:
    rights = getattr(user.profile, "rights", None) or {}
    return rights.get("*") is True or rights.get(right) is True


def can_assign_tickets(user: User) -> bool:
    return any(
        has_profile_right(user, right)
        for right in ("ticket:assign_any", "ticket:bulk_edit", "ticket:edit", "ticket:update", "ticket:assign")
    )


def can_update_tickets(user: User) -> bool:
    return any(
        has_profile_right(user, right)
        for right in ("ticket:edit", "ticket:update", "tickets:update", "ticket:bulk_edit")
    )


def ticket_route_team_id():
    assigned_group = (
        select(TicketActor.actor_id)
        .where(
            TicketActor.ticket_id == Ticket.id,
            TicketActor.actor_type == "GROUP",
            TicketActor.role == "ASSIGNEE",
        )
        .order_by(TicketActor.id)
        .limit(1)
        .correlate(Ticket)
        .scalar_subquery()
    )
    ticket_type_group = (
        select(func.coalesce(TicketTypeDefinition.default_team_id, TicketTypeDefinition.group_id))
        .where(TicketTypeDefinition.id == Ticket.ticket_type_id)
        .correlate(Ticket)
        .scalar_subquery()
    )
    return func.coalesce(assigned_group, ticket_type_group)


def ticket_visibility_scope(user: User, queue: str | None = None) -> ColumnElement[bool]:
    if has_profile_right(user, "ticket:view_all"):
        return true()
    if has_profile_right(user, "ticket:own_only"):
        return Ticket.requester_id == user.id
    if not has_profile_right(user, "ticket:view_queue"):
        return false()

    user_group_ids = (
        select(GroupMember.group_id)
        .join(Group, Group.id == GroupMember.group_id)
        .where(GroupMember.user_id == user.id, Group.entity_id == user.entity_id)
        .correlate(None)
    )
    route_team_id = ticket_route_team_id()
    assigned_to_team = route_team_id.in_(user_group_ids)
    assigned_to_user = Ticket.assignee_id == user.id
    unassigned = and_(Ticket.assignee_id.is_(None), route_team_id.is_(None))
    if queue == "mine":
        return and_(assigned_to_user, or_(route_team_id.is_(None), assigned_to_team))
    if queue == "teams":
        return assigned_to_team
    if queue == "unassigned":
        return unassigned
    return or_(
        and_(assigned_to_user, or_(route_team_id.is_(None), assigned_to_team)),
        assigned_to_team,
        unassigned,
    )


async def get_scoped_ticket(ticket_id: int, user: User, db: DbSession) -> Ticket | None:
    return await db.scalar(
        select(Ticket).where(
            Ticket.id == ticket_id,
            Ticket.entity_id == user.entity_id,
            Ticket.deleted_at.is_(None),
            ticket_visibility_scope(user),
        )
    )


def escape_ticket_search(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def ticket_response_data(ticket: Ticket, user: User) -> dict[str, Any]:
    data = {
        field: getattr(ticket, field)
        for field in TicketRead.model_fields
        if field != "priority_label" and hasattr(ticket, field)
    }
    data["priority_label"] = priority_label(
        ticket.priority,
        requester=field_role_for_user(user) == "Requester",
    )
    return data


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


@router.get("/agents", response_model=list[AssignableUserRead])
async def agents(user: CurrentUser, db: DbSession) -> list[AssignableUserRead]:
    page = await assignable_users(user, db, ticket_id=None, team_id=None, q=None, limit=50, offset=0)
    return page["items"]


@router.get("/users/assignable", response_model=dict)
async def assignable_users(
    user: CurrentUser,
    db: DbSession,
    ticket_id: int | None = Query(default=None, gt=0),
    team_id: int | None = Query(default=None, gt=0),
    q: str | None = Query(default=None, max_length=160),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
) -> dict:
    if not can_assign_tickets(user):
        raise HTTPException(status_code=403, detail="Ticket assignment permission required")

    ticket = None
    if ticket_id is not None:
        ticket = await get_scoped_ticket(ticket_id, user, db)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
    if team_id is not None and await db.scalar(
        select(Group.id).where(Group.id == team_id, Group.entity_id == user.entity_id)
    ) is None:
        raise HTTPException(status_code=422, detail="Team is outside your entity")

    candidate_right = or_(
        Profile.rights["ticket:view_queue"].as_boolean().is_(True),
        Profile.rights["ticket:view_all"].as_boolean().is_(True),
        Profile.rights["*"].as_boolean().is_(True),
    )
    query = (
        select(User)
        .join(Profile, Profile.id == User.profile_id)
        .where(User.entity_id == user.entity_id, User.is_active.is_(True), candidate_right)
    )
    own_groups = select(GroupMember.group_id).where(GroupMember.user_id == user.id)
    if not has_profile_right(user, "ticket:assign_any"):
        query = query.where(
            or_(
                User.id == user.id,
                User.id.in_(select(GroupMember.user_id).where(GroupMember.group_id.in_(own_groups))),
            )
        )
    if team_id is not None:
        query = query.where(
            User.id.in_(select(GroupMember.user_id).where(GroupMember.group_id == team_id))
        )
    if ticket is not None:
        route_team_id = team_id
        if route_team_id is None:
            route_team_id = await db.scalar(select(ticket_route_team_id()).where(Ticket.id == ticket.id))
        can_see_all = or_(
            Profile.rights["ticket:view_all"].as_boolean().is_(True),
            Profile.rights["*"].as_boolean().is_(True),
        )
        can_see_queue = Profile.rights["ticket:view_queue"].as_boolean().is_(True)
        route_membership = (
            select(GroupMember.group_id)
            .join(Group, Group.id == GroupMember.group_id)
            .where(
                GroupMember.user_id == User.id,
                GroupMember.group_id == route_team_id,
                Group.entity_id == user.entity_id,
            )
            .exists()
            if route_team_id is not None
            else true()
        )
        query = query.where(or_(can_see_all, and_(can_see_queue, route_membership)))
    if q:
        escaped = escape_ticket_search(q.strip())
        pattern = f"%{escaped}%"
        query = query.where(or_(User.name.ilike(pattern, escape="\\"), User.email.ilike(pattern, escape="\\")))

    total = await db.scalar(select(func.count()).select_from(query.subquery())) or 0
    candidates = list(
        (
            await db.scalars(query.order_by(User.name, User.id).offset(offset).limit(limit))
        ).all()
    )
    candidate_ids = [candidate.id for candidate in candidates]
    if not candidate_ids:
        return {"items": [], "total": total, "limit": limit, "offset": offset}

    team_rows = await db.execute(
        select(GroupMember.user_id, Group.name)
        .join(Group, Group.id == GroupMember.group_id)
        .where(GroupMember.user_id.in_(candidate_ids), Group.entity_id == user.entity_id)
        .order_by(Group.name)
    )
    teams_by_user: dict[int, list[str]] = {candidate_id: [] for candidate_id in candidate_ids}
    for candidate_id, team_name in team_rows.all():
        teams_by_user[candidate_id].append(team_name)

    open_counts = await db.execute(
        select(Ticket.assignee_id, func.count(Ticket.id))
        .where(
            Ticket.entity_id == user.entity_id,
            Ticket.assignee_id.in_(candidate_ids),
            Ticket.deleted_at.is_(None),
            Ticket.status.not_in(("SOLVED", "CLOSED")),
        )
        .group_by(Ticket.assignee_id)
    )
    counts_by_user = {assignee_id: count for assignee_id, count in open_counts.all()}
    items = [
        {
            "id": candidate.id,
            "name": candidate.name,
            "email": candidate.email,
            "teams": teams_by_user[candidate.id],
            "open_ticket_count": counts_by_user.get(candidate.id, 0),
            "agent_status": None,
        }
        for candidate in candidates
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


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
    queue: str | None = Query(default=None, pattern="^(mine|teams|unassigned)$"),
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
    if queue is not None and not has_profile_right(user, "ticket:view_queue"):
        raise HTTPException(status_code=403, detail="Ticket queue access required")
    conditions = [Ticket.entity_id == user.entity_id, Ticket.deleted_at.is_(None)]
    conditions.append(ticket_visibility_scope(user, queue))
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
            "priority_label": priority_label(
                ticket.priority,
                requester=field_role_for_user(user) == "Requester",
            ),
        }
        for ticket, requester in result.all()
    ]
    return {
        "items": rows,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": max(1, (total + page_size - 1) // page_size),
        "priority_options": priority_options(
            requester=field_role_for_user(user) == "Requester",
        ),
    }


@router.get("/tickets/team-counts")
async def ticket_team_counts(user: CurrentUser, db: DbSession) -> list[dict[str, int | str | None]]:
    conditions = [Ticket.entity_id == user.entity_id, Ticket.deleted_at.is_(None), ticket_visibility_scope(user)]
    route_team_id = ticket_route_team_id()
    rows = await db.execute(
        select(Group.id, Group.name, func.count(Ticket.id))
        .join(Group, and_(Group.id == route_team_id, Group.entity_id == user.entity_id))
        .where(*conditions)
        .group_by(Group.id, Group.name)
    )
    result = [
        {"team_id": team_id, "team_name": team_name, "count": count}
        for team_id, team_name, count in rows.all()
    ]
    unassigned = await db.scalar(
        select(func.count(Ticket.id)).where(
            Ticket.entity_id == user.entity_id,
            Ticket.deleted_at.is_(None),
            Ticket.assignee_id.is_(None),
            route_team_id.is_(None),
            ticket_visibility_scope(user, "unassigned") if has_profile_right(user, "ticket:view_queue") else ticket_visibility_scope(user),
        )
    ) or 0
    result.append({"team_id": None, "team_name": "Unassigned", "count": unassigned})
    return result


@router.get("/tickets/queue-counts")
async def ticket_queue_counts(user: CurrentUser, db: DbSession) -> dict[str, int]:
    if not has_profile_right(user, "ticket:view_queue"):
        raise HTTPException(status_code=403, detail="Ticket queue access required")
    counts = {
        "all": await db.scalar(
            select(func.count(Ticket.id)).where(
                Ticket.entity_id == user.entity_id,
                Ticket.deleted_at.is_(None),
                ticket_visibility_scope(user),
            )
        ) or 0
    }
    for queue in ("mine", "teams", "unassigned"):
        counts[queue] = await db.scalar(
            select(func.count(Ticket.id)).where(
                Ticket.entity_id == user.entity_id,
                Ticket.deleted_at.is_(None),
                ticket_visibility_scope(user, queue),
            )
        ) or 0
    return counts


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
async def get_ticket(ticket_id: int, user: CurrentUser, db: DbSession) -> dict:
    ticket = await get_scoped_ticket(ticket_id, user, db)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    assigned_name = await db.scalar(select(User.name).where(User.id == ticket.assignee_id)) if ticket.assignee_id else None
    routed_team_id = await db.scalar(select(ticket_route_team_id()).where(Ticket.id == ticket.id))
    routed_team_name = await db.scalar(select(Group.name).where(Group.id == routed_team_id)) if routed_team_id else None
    return {
        **ticket_response_data(ticket, user),
        "assignee_name": assigned_name,
        "team_id": routed_team_id,
        "team_name": routed_team_name,
    }


@router.post("/tickets", response_model=TicketRead, status_code=status.HTTP_201_CREATED)
async def create_ticket(payload: TicketCreate, user: CurrentUser, db: DbSession) -> Ticket:
    is_requester = field_role_for_user(user) == "Requester"
    field_values = dict(payload.custom_fields)
    trusted_field_values: dict[str, object] = {}
    if is_requester:
        if {"urgency", "impact"} & payload.model_fields_set or {"urgency", "impact"} & set(field_values):
            raise HTTPException(status_code=422, detail="Requesters cannot set urgency or impact")
        try:
            selected_priority = requester_priority_value(payload.priority if payload.priority is not None else "medium")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Choose a valid priority") from exc
        field_values.pop("user", None)
        field_values.pop("organisation", None)
        disallowed_fields = set(field_values) - REQUESTER_FORM_FIELDS
        if disallowed_fields:
            raise HTTPException(
                status_code=422,
                detail=f"Fields are not editable by Requesters: {', '.join(sorted(disallowed_fields))}",
            )
        if payload.category_id is not None:
            raise HTTPException(status_code=422, detail="Requesters cannot set a category")
        field_values["priority"] = selected_priority
        trusted_field_values = {"user": user.id, "organisation": user.entity_id}
    elif payload.priority is not None:
        if type(payload.priority) is not int or not 1 <= payload.priority <= 5:
            raise HTTPException(status_code=422, detail="Staff priority must be a numeric level from 1 to 5")
        selected_priority = payload.priority
    else:
        selected_priority = None
    field_values["summary"] = payload.title
    field_values["details"] = payload.description
    try:
        payload.description = sanitize_ticket_description(payload.description)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ticket_type_id = payload.ticket_type_id or await db.scalar(select(TicketTypeDefinition.id).where(
        TicketTypeDefinition.itil_class == ("INCIDENT" if payload.type == "INCIDENT" else "REQUEST"),
        (TicketTypeDefinition.entity_id == user.entity_id) | (TicketTypeDefinition.entity_id.is_(None)),
        TicketTypeDefinition.is_active.is_(True),
    ))
    if ticket_type_id is None:
        raise HTTPException(status_code=422, detail="A valid ticket type is required")
    if is_requester:
        if payload.type not in {"INCIDENT", "REQUEST"}:
            raise HTTPException(status_code=422, detail="Requesters can create incidents or service requests only")
        ticket_type = await db.scalar(select(TicketTypeDefinition).where(
            TicketTypeDefinition.id == ticket_type_id,
            (TicketTypeDefinition.entity_id == user.entity_id) | (TicketTypeDefinition.entity_id.is_(None)),
            TicketTypeDefinition.is_active.is_(True),
        ))
        if ticket_type is None or ticket_type.itil_class != payload.type:
            raise HTTPException(status_code=422, detail="Ticket type is not available in the self-service portal")
    requester_id = user.id
    if not is_requester and payload.requester_id is not None:
        requested_user = await db.scalar(select(User).where(
            User.id == payload.requester_id,
            User.entity_id == user.entity_id,
            User.is_active.is_(True),
        ))
        if requested_user is None:
            raise HTTPException(status_code=422, detail="Requester is outside your entity or inactive")
        requester_id = requested_user.id
    if "sla" in field_values and not has_profile_right(user, "override_sla"):
        raise HTTPException(status_code=403, detail="SLA override permission required")
    trusted_field_values = {"user": requester_id, "organisation": user.entity_id}
    field_values = await validate_custom_fields(
        db,
        ticket_type_id,
        user.entity_id,
        field_role_for_user(user),
        field_values,
        trusted_values=trusted_field_values,
    )
    if payload.category_id is not None and await db.scalar(select(Category).where(Category.id == payload.category_id, Category.entity_id == user.entity_id)) is None:
        raise HTTPException(status_code=422, detail="Category is outside your entity")
    requested_sla_id = payload.sla_id
    if requested_sla_id is not None and not has_profile_right(user, "override_sla"):
        raise HTTPException(status_code=403, detail="SLA override permission required")
    if payload.assignee_id is not None and await db.scalar(select(User).where(User.id == payload.assignee_id, User.entity_id == user.entity_id, User.is_active.is_(True))) is None:
        raise HTTPException(status_code=422, detail="Assignee is outside your entity or inactive")
    urgency = payload.urgency if payload.urgency is not None else 3
    impact = payload.impact if payload.impact is not None else 3
    if selected_priority is not None:
        urgency, impact = priority_urgency_impact(selected_priority)
        priority = selected_priority
    else:
        matrix = await db.scalar(select(PriorityMatrix).where(PriorityMatrix.entity_id == user.entity_id))
        priority = calculate_priority(urgency, impact) if matrix is None else matrix.matrix[urgency - 1][impact - 1]
    ticket = Ticket(
        **payload.model_dump(
            exclude={"type", "sla_id", "requester_id", "ticket_type_id", "custom_fields", "priority", "urgency", "impact"}
        ),
        type=payload.type,
        urgency=urgency,
        impact=impact,
        ticket_type_id=ticket_type_id,
        custom_fields=field_values,
        priority=priority,
        entity_id=user.entity_id,
        requester_id=requester_id,
    )
    if ticket.assignee_id:
        ticket.status = "ASSIGNED"
    db.add(ticket)
    await db.flush()
    if requested_sla_id:
        sla = await db.scalar(
            select(SLA).where(
                SLA.id == requested_sla_id,
                SLA.entity_id == user.entity_id,
                SLA.is_active.is_(True),
            )
        )
    else:
        entity_name = await db.scalar(select(Entity.name).where(Entity.id == user.entity_id))
        sla = await db.scalar(
            select(SLA).where(
                SLA.entity_id == user.entity_id,
                SLA.name == f"{entity_name} Priority {priority} SLA",
                SLA.is_active.is_(True),
            )
        )
        if sla is None:
            sla = await db.scalar(select(SLA).where(SLA.entity_id == user.entity_id, SLA.is_active.is_(True)).order_by(SLA.id))
    if requested_sla_id and sla is None:
        raise HTTPException(status_code=422, detail="SLA is outside your entity or inactive")
    if sla:
        ticket.sla_id = sla.id
        calculate_sla_deadlines(ticket, sla.tto_minutes, sla.ttr_minutes, sla.calendar)
    rules = await db.scalars(select(BusinessRule).where(BusinessRule.entity_id == user.entity_id, BusinessRule.trigger == "ON_CREATE", BusinessRule.is_active.is_(True)).order_by(BusinessRule.order))
    assigned_group_id: int | None = None
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
            if "set_team" in actions:
                group = await db.scalar(
                    select(Group).where(
                        Group.id == int(actions["set_team"]),
                        Group.entity_id == user.entity_id,
                    )
                )
                if group:
                    assigned_group_id = group.id
            if "set_sla" in actions:
                rule_sla = await db.scalar(select(SLA).where(SLA.id == int(actions["set_sla"]), SLA.entity_id == user.entity_id, SLA.is_active.is_(True)))
                if rule_sla:
                    ticket.sla_id = rule_sla.id
                    calculate_sla_deadlines(ticket, rule_sla.tto_minutes, rule_sla.ttr_minutes, rule_sla.calendar)
    if selected_priority is not None:
        ticket.priority = selected_priority
        ticket.urgency, ticket.impact = priority_urgency_impact(selected_priority)
        entity_name = await db.scalar(select(Entity.name).where(Entity.id == user.entity_id))
        priority_sla = await db.scalar(
            select(SLA).where(
                SLA.entity_id == user.entity_id,
                SLA.name == f"{entity_name} Priority {selected_priority} SLA",
                SLA.is_active.is_(True),
            )
        )
        if priority_sla is not None:
            ticket.sla_id = priority_sla.id
            calculate_sla_deadlines(
                ticket,
                priority_sla.tto_minutes,
                priority_sla.ttr_minutes,
                priority_sla.calendar,
            )
    db.add(TicketActor(ticket_id=ticket.id, actor_type="USER", actor_id=requester_id, role="REQUESTER"))
    if assigned_group_id is not None:
        db.add(TicketActor(ticket_id=ticket.id, actor_type="GROUP", actor_id=assigned_group_id, role="ASSIGNEE"))
    await store_field_values(db, ticket, field_values)
    await db.commit()
    await db.refresh(ticket)
    await record_audit(db, entity="ticket", entity_id=ticket.id, action="created", actor_id=user.id, diff={"status": ticket.status})
    await db.commit()
    send_ticket_notification.delay(ticket.id, "created")
    if ticket.assignee_id is not None:
        send_ticket_notification.delay(ticket.id, "assigned")
    return ticket_response_data(ticket, user)


@router.post("/tickets/{ticket_id}/claim", response_model=TicketRead)
async def claim_ticket(ticket_id: int, user: CurrentUser, db: DbSession) -> Ticket:
    if not has_profile_right(user, "ticket:claim") or not has_profile_right(user, "ticket:view_queue"):
        raise HTTPException(status_code=403, detail="Ticket claim permission required")
    ticket = await get_scoped_ticket(ticket_id, user, db)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    effects = await apply_ticket_update(
        db,
        ticket,
        user,
        {"assignee_id": user.id},
        audit_action="claimed",
        visibility_scope=ticket_visibility_scope(user),
        expected_unassigned=True,
        visibility_scope_factory=ticket_visibility_scope,
    )
    await db.commit()
    await db.refresh(ticket)
    dispatch_ticket_update_effects(ticket.id, effects)
    return ticket


@router.post("/tickets/{ticket_id}/assign", response_model=TicketAssignResult)
async def assign_ticket(
    ticket_id: int,
    payload: TicketAssignRequest,
    user: CurrentUser,
    db: DbSession,
) -> dict:
    if not can_assign_tickets(user):
        raise HTTPException(status_code=403, detail="Ticket assignment permission required")
    ticket = await get_scoped_ticket(ticket_id, user, db)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    values: dict[str, Any] = {"assignee_id": payload.assignee_id}
    if "team_id" in payload.model_fields_set:
        values["team_id"] = payload.team_id
    effects = await apply_ticket_update(
        db,
        ticket,
        user,
        values,
        audit_action="assigned",
        visibility_scope=ticket_visibility_scope(user),
        visibility_scope_factory=ticket_visibility_scope,
    )
    await db.commit()
    await db.refresh(ticket)
    dispatch_ticket_update_effects(ticket.id, effects)
    return {
        "ticket": await get_ticket(ticket.id, user, db),
        "ok": True,
        "reason": None if effects.changed else "unchanged",
    }


@router.patch("/tickets/{ticket_id}", response_model=TicketRead)
async def update_ticket(ticket_id: int, payload: TicketUpdate, user: CurrentUser, db: DbSession) -> Ticket:
    if not can_update_tickets(user):
        raise HTTPException(status_code=403, detail="Ticket update permission required")
    ticket = await get_scoped_ticket(ticket_id, user, db)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    values = payload.model_dump(exclude_unset=True)
    if any(values.get(key) is None for key in ("priority", "ticket_type_id") if key in values):
        raise HTTPException(status_code=422, detail="Priority and ticket type cannot be null")
    if any(key in values for key in ("assignee_id", "team_id")) and not can_assign_tickets(user):
        raise HTTPException(status_code=403, detail="Ticket assignment permission required")
    effects = await apply_ticket_update(
        db,
        ticket,
        user,
        values,
        visibility_scope=ticket_visibility_scope(user),
        visibility_scope_factory=ticket_visibility_scope,
    )
    await db.commit()
    await db.refresh(ticket)
    dispatch_ticket_update_effects(ticket.id, effects)
    return ticket_response_data(ticket, user)


@router.post("/tickets/bulk", response_model=TicketBulkResult)
async def bulk_ticket_update(
    payload: TicketBulkUpdate,
    user: CurrentUser,
    db: DbSession,
) -> dict:
    return await run_bulk_ticket_update(
        db,
        user,
        payload,
        ticket_visibility_scope(user),
        visibility_scope_factory=ticket_visibility_scope,
    )


@router.delete("/tickets/{ticket_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ticket(ticket_id: int, user: CurrentUser, db: DbSession) -> None:
    require_admin(user)
    ticket = await get_scoped_ticket(ticket_id, user, db)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    from datetime import UTC, datetime
    ticket.deleted_at = datetime.now(UTC)
    await record_audit(db, entity="ticket", entity_id=ticket.id, action="deleted", actor_id=user.id, diff={"deleted_at": ticket.deleted_at.isoformat()})
    await db.commit()


@router.get("/tickets/{ticket_id}/actors", response_model=list[ActorRead])
async def actors(ticket_id: int, user: CurrentUser, db: DbSession) -> list[TicketActor]:
    ticket = await get_scoped_ticket(ticket_id, user, db)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return list((await db.scalars(select(TicketActor).where(TicketActor.ticket_id == ticket_id).order_by(TicketActor.id))).all())


@router.post("/tickets/{ticket_id}/actors", response_model=ActorRead, status_code=status.HTTP_201_CREATED)
async def add_actor(ticket_id: int, payload: ActorCreate, user: CurrentUser, db: DbSession) -> TicketActor:
    require_helpdesk(user)
    ticket = await get_scoped_ticket(ticket_id, user, db)
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
    ticket = await get_scoped_ticket(ticket_id, user, db)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    query = select(TicketFollowup).where(TicketFollowup.ticket_id == ticket_id)
    if user.profile.interface == "self_service":
        query = query.where(TicketFollowup.is_private.is_(False))
    return list((await db.scalars(query.order_by(TicketFollowup.created_at))).all())


@router.post("/tickets/{ticket_id}/followups", response_model=FollowupRead, status_code=status.HTTP_201_CREATED)
async def add_followup(ticket_id: int, payload: FollowupCreate, user: CurrentUser, db: DbSession) -> TicketFollowup:
    ticket = await get_scoped_ticket(ticket_id, user, db)
    if ticket is None:
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
    if not followup.is_private:
        send_ticket_notification.delay(ticket.id, "public_reply")
    return followup


async def scoped_ticket(ticket_id: int, user: CurrentUser, db: DbSession) -> Ticket:
    ticket = await get_scoped_ticket(ticket_id, user, db)
    if ticket is None:
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


@router.get("/tickets/{ticket_id}/links", response_model=list[LinkRead])
async def list_links(ticket_id: int, user: CurrentUser, db: DbSession) -> list[TicketLink]:
    await scoped_ticket(ticket_id, user, db)
    return list((await db.scalars(select(TicketLink).where(TicketLink.ticket_id == ticket_id).order_by(TicketLink.created_at))).all())


@router.post("/tickets/{ticket_id}/links", response_model=LinkRead, status_code=status.HTTP_201_CREATED)
async def link_ticket(ticket_id: int, payload: LinkCreate, user: CurrentUser, db: DbSession) -> TicketLink:
    require_helpdesk(user)
    ticket = await scoped_ticket(ticket_id, user, db)
    linked = await db.scalar(select(Ticket).where(
        Ticket.id == payload.linked_ticket_id,
        Ticket.entity_id == user.entity_id,
        Ticket.deleted_at.is_(None),
        ticket_visibility_scope(user),
    ))
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
