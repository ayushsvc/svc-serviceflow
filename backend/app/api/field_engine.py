from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, status
from sqlalchemy import and_, case, delete, desc, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.deps import CurrentUser, DbSession, require_admin, require_helpdesk
from app.models import BusinessRule, Category, CategoryTeam, FieldDefinition, Group, GroupMember, PriorityMatrix, SLA, TicketTypeDefinition, TicketTypeField, TicketTemplate, TicketTemplateUsage, User
from app.schemas_field_engine import AssignmentPreview, FieldDefinitionCreate, FieldDefinitionRead, FormField, FormSchemaRead, PriorityMatrixPayload, PriorityMatrixRead, SlaPreview, TemplateCreate, TemplateDetailRead, TemplateListItem, TemplateRead, TemplateTeamRead, TicketTypeCreate, TicketTypeRead
from app.services.audit import record_audit
from app.services.field_engine import get_type_fields, sanitize_field_values, validate_condition, validate_template_prefilled
from app.services.tickets import calculate_matrix_priority

router = APIRouter(tags=["field-engine"])


@router.get("/categories/{category_id}/teams", response_model=list[int])
async def category_teams(category_id: int, user: CurrentUser, db: DbSession) -> list[int]:
    require_admin(user)
    category = await db.scalar(select(Category).where(Category.id == category_id, Category.entity_id == user.entity_id))
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    return list((await db.scalars(select(CategoryTeam.group_id).where(CategoryTeam.category_id == category_id))).all())


@router.put("/categories/{category_id}/teams", response_model=list[int])
async def update_category_teams(category_id: int, user: CurrentUser, db: DbSession, team_ids: list[int] = Body(...)) -> list[int]:
    require_admin(user)
    category = await db.scalar(select(Category).where(Category.id == category_id, Category.entity_id == user.entity_id))
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    valid_ids = set((await db.scalars(select(Group.id).where(Group.id.in_(team_ids), Group.entity_id == user.entity_id))).all())
    if valid_ids != set(team_ids):
        raise HTTPException(status_code=422, detail="One or more teams are outside your entity")
    await db.execute(delete(CategoryTeam).where(CategoryTeam.category_id == category_id))
    for team_id in team_ids:
        db.add(CategoryTeam(category_id=category_id, group_id=team_id))
    await record_audit(db, entity="category", entity_id=category_id, action="teams_updated", actor_id=user.id, diff={"team_ids": team_ids})
    await db.commit()
    return team_ids


async def _ticket_type_output(db: DbSession, ticket_type: TicketTypeDefinition) -> dict[str, Any]:
    fields = list((await db.scalars(select(TicketTypeField).where(
        TicketTypeField.ticket_type_id == ticket_type.id,
    ).order_by(TicketTypeField.order))).all())
    return {
        "id": ticket_type.id,
        "name": ticket_type.name,
        "itil_class": ticket_type.itil_class,
        "group_id": ticket_type.group_id,
        "default_team_id": ticket_type.default_team_id,
        "default_priority": ticket_type.default_priority,
        "default_sla_id": ticket_type.default_sla_id,
        "icon": ticket_type.icon,
        "color": ticket_type.color,
        "is_active": ticket_type.is_active,
        "entity_id": ticket_type.entity_id,
        "fields": [field.__dict__ for field in fields],
    }


async def _entity_group(db: DbSession, group_id: int | None, entity_id: int) -> None:
    if group_id is not None and await db.scalar(select(Group).where(Group.id == group_id, Group.entity_id == entity_id)) is None:
        raise HTTPException(status_code=422, detail="Team is outside your entity")


async def _entity_sla(db: DbSession, sla_id: int | None, entity_id: int) -> None:
    if sla_id is not None and await db.scalar(select(SLA).where(SLA.id == sla_id, SLA.entity_id == entity_id, SLA.is_active.is_(True))) is None:
        raise HTTPException(status_code=422, detail="SLA is outside your entity or inactive")


@router.get("/field-definitions", response_model=list[FieldDefinitionRead])
async def list_field_definitions(user: CurrentUser, db: DbSession) -> list[FieldDefinition]:
    require_helpdesk(user)
    return list((await db.scalars(select(FieldDefinition).order_by(FieldDefinition.key))).all())


@router.post("/field-definitions", response_model=FieldDefinitionRead, status_code=status.HTTP_201_CREATED)
async def create_field_definition(payload: FieldDefinitionCreate, user: CurrentUser, db: DbSession) -> FieldDefinition:
    require_admin(user)
    field = FieldDefinition(**payload.model_dump())
    db.add(field)
    await db.flush()
    await record_audit(db, entity="field_definition", entity_id=field.id, action="created", actor_id=user.id, diff=payload.model_dump())
    await db.commit()
    await db.refresh(field)
    return field


@router.patch("/field-definitions/{field_id}", response_model=FieldDefinitionRead)
async def update_field_definition(field_id: int, payload: FieldDefinitionCreate, user: CurrentUser, db: DbSession) -> FieldDefinition:
    require_admin(user)
    field = await db.get(FieldDefinition, field_id)
    if field is None:
        raise HTTPException(status_code=404, detail="Field definition not found")
    changes = payload.model_dump()
    for key, value in changes.items():
        setattr(field, key, value)
    await record_audit(db, entity="field_definition", entity_id=field.id, action="updated", actor_id=user.id, diff=changes)
    await db.commit()
    await db.refresh(field)
    return field


@router.delete("/field-definitions/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_field_definition(field_id: int, user: CurrentUser, db: DbSession) -> None:
    require_admin(user)
    field = await db.get(FieldDefinition, field_id)
    if field is None:
        raise HTTPException(status_code=404, detail="Field definition not found")
    if field.is_system:
        raise HTTPException(status_code=409, detail="System fields cannot be deleted")
    await db.delete(field)
    await record_audit(db, entity="field_definition", entity_id=field_id, action="deleted", actor_id=user.id, diff={})
    await db.commit()


@router.get("/ticket-types", response_model=list[TicketTypeRead])
async def list_ticket_types(user: CurrentUser, db: DbSession) -> list[Any]:
    query = select(TicketTypeDefinition).where(
        ((TicketTypeDefinition.entity_id == user.entity_id) | (TicketTypeDefinition.entity_id.is_(None))),
        TicketTypeDefinition.is_active.is_(True),
    )
    if user.profile.name == "Requester":
        query = query.where(TicketTypeDefinition.itil_class.in_(["INCIDENT", "REQUEST"]))
    types = list((await db.scalars(query.order_by(TicketTypeDefinition.name))).all())
    results = []
    for ticket_type in types:
        result = await _ticket_type_output(db, ticket_type)
        if user.profile.name == "Requester":
            result["fields"] = [
                field for field in result["fields"]
                if "Requester" in field["visible_to_roles"] and field["show_on_portal"]
            ]
        results.append(result)
    return results


@router.post("/ticket-types", response_model=TicketTypeRead, status_code=status.HTTP_201_CREATED)
async def create_ticket_type(payload: TicketTypeCreate, user: CurrentUser, db: DbSession) -> TicketTypeDefinition:
    require_admin(user)
    await _entity_group(db, payload.group_id, user.entity_id)
    await _entity_group(db, payload.default_team_id, user.entity_id)
    await _entity_sla(db, payload.default_sla_id, user.entity_id)
    values = payload.model_dump(exclude={"fields"})
    ticket_type = TicketTypeDefinition(**values, entity_id=user.entity_id)
    db.add(ticket_type)
    await db.flush()
    await _replace_type_fields(db, ticket_type.id, payload.fields, user.id)
    await record_audit(db, entity="ticket_type", entity_id=ticket_type.id, action="created", actor_id=user.id, diff=values)
    await db.commit()
    await db.refresh(ticket_type)
    return await _ticket_type_output(db, ticket_type)


async def _replace_type_fields(db: DbSession, ticket_type_id: int, fields: list[Any], actor_id: int) -> None:
    await db.execute(delete(TicketTypeField).where(TicketTypeField.ticket_type_id == ticket_type_id))
    for field in fields:
        try:
            validate_condition(field.show_condition)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if await db.get(FieldDefinition, field.field_definition_id) is None:
            raise HTTPException(status_code=422, detail=f"Unknown field definition {field.field_definition_id}")
        db.add(TicketTypeField(ticket_type_id=ticket_type_id, **field.model_dump()))


@router.patch("/ticket-types/{ticket_type_id}", response_model=TicketTypeRead)
async def update_ticket_type(ticket_type_id: int, payload: TicketTypeCreate, user: CurrentUser, db: DbSession) -> TicketTypeDefinition:
    require_admin(user)
    ticket_type = await db.scalar(select(TicketTypeDefinition).where(
        TicketTypeDefinition.id == ticket_type_id,
        (TicketTypeDefinition.entity_id == user.entity_id) | (TicketTypeDefinition.entity_id.is_(None)),
    ))
    if ticket_type is None:
        raise HTTPException(status_code=404, detail="Ticket type not found")
    await _entity_group(db, payload.group_id, user.entity_id)
    await _entity_group(db, payload.default_team_id, user.entity_id)
    await _entity_sla(db, payload.default_sla_id, user.entity_id)
    changes = payload.model_dump(exclude={"fields"})
    for key, value in changes.items():
        setattr(ticket_type, key, value)
    if ticket_type.entity_id is None:
        ticket_type.entity_id = user.entity_id
    await _replace_type_fields(db, ticket_type.id, payload.fields, user.id)
    await record_audit(db, entity="ticket_type", entity_id=ticket_type.id, action="updated", actor_id=user.id, diff=changes)
    await db.commit()
    await db.refresh(ticket_type)
    return await _ticket_type_output(db, ticket_type)


@router.delete("/ticket-types/{ticket_type_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ticket_type(ticket_type_id: int, user: CurrentUser, db: DbSession) -> None:
    require_admin(user)
    ticket_type = await db.scalar(select(TicketTypeDefinition).where(TicketTypeDefinition.id == ticket_type_id, TicketTypeDefinition.entity_id == user.entity_id))
    if ticket_type is None:
        raise HTTPException(status_code=404, detail="Ticket type not found")
    ticket_type.is_active = False
    await record_audit(db, entity="ticket_type", entity_id=ticket_type_id, action="deactivated", actor_id=user.id, diff={})
    await db.commit()


@router.get("/ticket-types/{ticket_type_id}/form", response_model=FormSchemaRead)
async def ticket_type_form(ticket_type_id: int, user: CurrentUser, db: DbSession) -> FormSchemaRead:
    ticket_type, fields = await get_type_fields(db, ticket_type_id, user.entity_id)
    if user.profile.name == "Requester" and ticket_type.itil_class not in {"INCIDENT", "REQUEST"}:
        raise HTTPException(status_code=404, detail="Ticket type not found")
    pages: dict[str, list[FormField]] = {}
    for definition, mapping in fields:
        if user.profile.name not in mapping.visible_to_roles:
            continue
        if user.profile.name == "Requester" and not mapping.show_on_portal:
            continue
        editable = user.profile.name in mapping.editable_by_roles
        if definition.key == "sla":
            rights = user.profile.rights or {}
            editable = editable and (rights.get("*") is True or rights.get("override_sla") is True)
        pages.setdefault(mapping.page, []).append(FormField(
            key=definition.key, label=definition.label, type=definition.type, options=definition.options or {},
            validation=definition.validation or {}, required=mapping.required, editable=editable,
            default_value=mapping.default_value, page=mapping.page, show_on_portal=mapping.show_on_portal,
            show_condition=mapping.show_condition or {},
        ))
    return FormSchemaRead(ticket_type_id=ticket_type.id, name=ticket_type.name, pages=pages)


def _template_role_key(user: User) -> str:
    if user.profile.interface == "self_service":
        return "Requester"
    rights = user.profile.rights or {}
    return "Administrator" if rights.get("*") is True or rights.get("ticket:view_all") is True else "Technician"


async def _template_team(db: DbSession, team_id: int | None) -> TemplateTeamRead | None:
    if team_id is None:
        return None
    team = await db.get(Group, team_id)
    return TemplateTeamRead(id=team.id, name=team.name) if team is not None else None


async def _template_prefill_for_user(
    db: DbSession, template: TicketTemplate, user: User
) -> dict[str, Any]:
    role = _template_role_key(user)
    _, fields = await get_type_fields(db, template.ticket_type_id, user.entity_id)
    allowed_keys = {
        definition.key
        for definition, mapping in fields
        if role in mapping.visible_to_roles
        and role in mapping.editable_by_roles
        and (role != "Requester" or mapping.show_on_portal)
        and definition.key not in {"user", "organisation"}
    }
    return {
        key: value
        for key, value in (template.prefilled or {}).items()
        if key in allowed_keys
    }


def _template_is_admin(user: User) -> bool:
    rights = user.profile.rights or {}
    return rights.get("*") is True or rights.get("ticket:view_all") is True


@router.get("/ticket-templates", response_model=list[TemplateListItem])
async def browse_ticket_templates(
    user: CurrentUser,
    db: DbSession,
    ticket_type_id: int | None = Query(default=None, gt=0),
    q: str | None = Query(default=None, max_length=160),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
) -> list[TemplateListItem]:
    requester = user.profile.interface == "self_service"
    if not requester:
        require_helpdesk(user)
    own_teams = select(GroupMember.group_id).join(
        Group, Group.id == GroupMember.group_id
    ).where(
        GroupMember.user_id == user.id,
        Group.entity_id == user.entity_id,
    )
    statement = (
        select(TicketTemplate, Group, TicketTemplateUsage.last_used_at)
        .join(TicketTypeDefinition, TicketTemplate.ticket_type_id == TicketTypeDefinition.id)
        .outerjoin(
            Group,
            and_(
                Group.id == TicketTemplate.team_id,
                Group.entity_id == user.entity_id,
            ),
        )
        .outerjoin(
            TicketTemplateUsage,
            and_(
                TicketTemplateUsage.template_id == TicketTemplate.id,
                TicketTemplateUsage.entity_id == user.entity_id,
                TicketTemplateUsage.user_id == user.id,
            ),
        )
        .where(
            TicketTemplate.entity_id == user.entity_id,
            TicketTemplate.is_active.is_(True),
            TicketTypeDefinition.is_active.is_(True),
            or_(
                TicketTypeDefinition.entity_id == user.entity_id,
                TicketTypeDefinition.entity_id.is_(None),
            ),
            or_(TicketTemplate.team_id.is_(None), Group.id.is_not(None)),
        )
    )
    if requester:
        statement = statement.where(
            TicketTemplate.show_on_portal.is_(True),
            TicketTypeDefinition.itil_class.in_(("INCIDENT", "REQUEST")),
        )
    elif not _template_is_admin(user):
        statement = statement.where(
            or_(
                TicketTemplate.team_id.is_(None),
                TicketTemplate.team_id.in_(own_teams),
            )
        )
    if ticket_type_id is not None:
        statement = statement.where(TicketTemplate.ticket_type_id == ticket_type_id)
    if q and q.strip():
        escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        statement = statement.where(
            or_(
                TicketTemplate.name.ilike(pattern, escape="\\"),
                TicketTemplate.description.ilike(pattern, escape="\\"),
            )
        )

    recent_statement = (
        select(TicketTemplateUsage.template_id)
        .join(TicketTemplate, TicketTemplate.id == TicketTemplateUsage.template_id)
        .join(TicketTypeDefinition, TicketTemplate.ticket_type_id == TicketTypeDefinition.id)
        .outerjoin(
            Group,
            and_(
                Group.id == TicketTemplate.team_id,
                Group.entity_id == user.entity_id,
            ),
        )
        .where(
            TicketTemplateUsage.entity_id == user.entity_id,
            TicketTemplateUsage.user_id == user.id,
            TicketTemplate.entity_id == user.entity_id,
            TicketTemplate.is_active.is_(True),
            TicketTypeDefinition.is_active.is_(True),
            or_(
                TicketTypeDefinition.entity_id == user.entity_id,
                TicketTypeDefinition.entity_id.is_(None),
            ),
            or_(TicketTemplate.team_id.is_(None), Group.id.is_not(None)),
        )
    )
    if requester:
        recent_statement = recent_statement.where(
            TicketTemplate.show_on_portal.is_(True),
            TicketTypeDefinition.itil_class.in_(("INCIDENT", "REQUEST")),
        )
    elif not _template_is_admin(user):
        recent_statement = recent_statement.where(
            or_(
                TicketTemplate.team_id.is_(None),
                TicketTemplate.team_id.in_(own_teams),
            )
        )
    recent_ids = list(
        (
            await db.scalars(
                recent_statement.order_by(
                    TicketTemplateUsage.last_used_at.desc(),
                    TicketTemplateUsage.id.desc(),
                )
                .limit(5)
            )
        ).all()
    )
    statement = statement.order_by(
        case((TicketTemplate.id.in_(recent_ids), 0), else_=1),
        TicketTemplateUsage.last_used_at.desc().nullslast(),
        TicketTemplate.sort_order,
        TicketTemplate.name,
        TicketTemplate.id,
    ).offset(offset).limit(limit)
    rows = (await db.execute(statement)).all()
    return [
        TemplateListItem(
            id=template.id,
            name=template.name,
            description=template.description or "",
            ticket_type_id=template.ticket_type_id,
            team=(
                TemplateTeamRead(id=team.id, name=team.name)
                if team is not None
                else None
            ),
            is_recent=template.id in recent_ids,
        )
        for template, team, _ in rows
    ]


@router.get("/ticket-templates/{template_id}", response_model=TemplateDetailRead)
async def get_ticket_template(
    template_id: int, user: CurrentUser, db: DbSession
) -> TemplateDetailRead:
    template = await db.scalar(
        select(TicketTemplate)
        .join(TicketTypeDefinition, TicketTemplate.ticket_type_id == TicketTypeDefinition.id)
        .outerjoin(
            Group,
            and_(
                Group.id == TicketTemplate.team_id,
                Group.entity_id == user.entity_id,
            ),
        )
        .where(
            TicketTemplate.id == template_id,
            TicketTemplate.entity_id == user.entity_id,
            TicketTemplate.is_active.is_(True),
            TicketTypeDefinition.is_active.is_(True),
            or_(
                TicketTypeDefinition.entity_id == user.entity_id,
                TicketTypeDefinition.entity_id.is_(None),
            ),
            or_(TicketTemplate.team_id.is_(None), Group.id.is_not(None)),
        )
    )
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    requester = user.profile.interface == "self_service"
    if requester:
        ticket_type = await db.get(TicketTypeDefinition, template.ticket_type_id)
        if (
            not template.show_on_portal
            or ticket_type is None
            or ticket_type.itil_class not in {"INCIDENT", "REQUEST"}
        ):
            raise HTTPException(status_code=404, detail="Template not found")
    elif not _template_is_admin(user) and template.team_id is not None:
        if await db.scalar(
            select(GroupMember.group_id)
            .join(Group, Group.id == GroupMember.group_id)
            .where(
                GroupMember.user_id == user.id,
                GroupMember.group_id == template.team_id,
                Group.entity_id == user.entity_id,
            )
        ) is None:
            raise HTTPException(status_code=404, detail="Template not found")

    usage_statement = pg_insert(TicketTemplateUsage).values(
        entity_id=user.entity_id,
        user_id=user.id,
        template_id=template.id,
        last_used_at=datetime.now(UTC),
        uses=1,
    )
    await db.execute(
        usage_statement.on_conflict_do_update(
            constraint="uq_ticket_template_usage_owner",
            set_={
                "last_used_at": datetime.now(UTC),
                "uses": TicketTemplateUsage.uses + 1,
            },
        )
    )
    prefilled = await _template_prefill_for_user(db, template, user)
    await db.commit()
    return TemplateDetailRead(
        id=template.id,
        name=template.name,
        description=template.description or "",
        ticket_type_id=template.ticket_type_id,
        team=await _template_team(db, template.team_id),
        prefilled=prefilled,
    )


@router.get("/templates", response_model=list[TemplateRead])
async def list_templates(user: CurrentUser, db: DbSession) -> list[TicketTemplate | dict[str, Any]]:
    requester = user.profile.interface == "self_service"
    if not requester:
        require_helpdesk(user)
    statement = (
        select(TicketTemplate)
        .join(TicketTypeDefinition, TicketTemplate.ticket_type_id == TicketTypeDefinition.id)
        .where(
            TicketTemplate.entity_id == user.entity_id,
            TicketTemplate.is_active.is_(True),
            TicketTypeDefinition.is_active.is_(True),
            or_(
                TicketTypeDefinition.entity_id == user.entity_id,
                TicketTypeDefinition.entity_id.is_(None),
            ),
        )
        .order_by(TicketTemplate.sort_order, TicketTemplate.name)
    )
    if requester:
        statement = statement.where(
            TicketTemplate.show_on_portal.is_(True),
            TicketTypeDefinition.itil_class.in_(("INCIDENT", "REQUEST")),
        )
    templates = list((await db.scalars(statement)).all())
    if not requester:
        return templates
    return [
        {
            "id": template.id,
            "name": template.name,
            "description": template.description or "",
            "ticket_type_id": template.ticket_type_id,
            "team_id": None,
            "prefilled": await _template_prefill_for_user(db, template, user),
            "is_active": template.is_active,
            "show_on_portal": template.show_on_portal,
            "sort_order": template.sort_order,
            "entity_id": template.entity_id,
        }
        for template in templates
    ]


@router.post("/ticket-templates", response_model=TemplateRead, status_code=status.HTTP_201_CREATED)
@router.post("/templates", response_model=TemplateRead, status_code=status.HTTP_201_CREATED)
async def create_template(payload: TemplateCreate, user: CurrentUser, db: DbSession) -> TicketTemplate:
    require_admin(user)
    await get_type_fields(db, payload.ticket_type_id, user.entity_id)
    await _entity_group(db, payload.team_id, user.entity_id)
    values = payload.model_dump()
    values["prefilled"] = await validate_template_prefilled(
        db, payload.ticket_type_id, user.entity_id, values["prefilled"]
    )
    template = TicketTemplate(**values, entity_id=user.entity_id)
    db.add(template)
    await db.flush()
    await record_audit(
        db,
        entity="ticket_template",
        entity_id=template.id,
        action="created",
        actor_id=user.id,
        diff=values,
    )
    await db.commit()
    await db.refresh(template)
    return template


@router.patch("/ticket-templates/{template_id}", response_model=TemplateRead)
@router.patch("/templates/{template_id}", response_model=TemplateRead)
async def update_template(template_id: int, payload: TemplateCreate, user: CurrentUser, db: DbSession) -> TicketTemplate:
    require_admin(user)
    template = await db.scalar(
        select(TicketTemplate).where(
            TicketTemplate.id == template_id,
            TicketTemplate.entity_id == user.entity_id,
        )
    )
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    await get_type_fields(db, payload.ticket_type_id, user.entity_id)
    await _entity_group(db, payload.team_id, user.entity_id)
    changes = payload.model_dump()
    changes["prefilled"] = await validate_template_prefilled(
        db, payload.ticket_type_id, user.entity_id, changes["prefilled"]
    )
    for key, value in changes.items():
        setattr(template, key, value)
    await record_audit(
        db,
        entity="ticket_template",
        entity_id=template.id,
        action="updated",
        actor_id=user.id,
        diff=changes,
    )
    await db.commit()
    await db.refresh(template)
    return template


@router.delete("/ticket-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(template_id: int, user: CurrentUser, db: DbSession) -> None:
    require_admin(user)
    template = await db.scalar(
        select(TicketTemplate).where(
            TicketTemplate.id == template_id,
            TicketTemplate.entity_id == user.entity_id,
        )
    )
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    await record_audit(db, entity="ticket_template", entity_id=template_id, action="deleted", actor_id=user.id, diff={})
    await db.delete(template)
    await db.commit()


@router.get("/priority-matrix", response_model=PriorityMatrixRead)
async def get_priority_matrix(user: CurrentUser, db: DbSession) -> PriorityMatrix:
    require_helpdesk(user)
    matrix = await _get_matrix(db, user.entity_id)
    return matrix


@router.put("/priority-matrix", response_model=PriorityMatrixRead)
async def update_priority_matrix(payload: PriorityMatrixPayload, user: CurrentUser, db: DbSession) -> PriorityMatrix:
    require_admin(user)
    calculate_matrix_priority(1, 1, payload.matrix)
    matrix = await _get_matrix(db, user.entity_id)
    matrix.matrix = payload.matrix
    await record_audit(db, entity="priority_matrix", entity_id=matrix.id, action="updated", actor_id=user.id, diff={"matrix": payload.matrix})
    await db.commit()
    await db.refresh(matrix)
    return matrix


async def _get_matrix(db: DbSession, entity_id: int) -> PriorityMatrix:
    matrix = await db.scalar(select(PriorityMatrix).where(PriorityMatrix.entity_id == entity_id))
    if matrix is None:
        matrix = PriorityMatrix(entity_id=entity_id, matrix=[[1, 1, 2, 2, 3], [1, 2, 2, 3, 3], [2, 2, 3, 3, 4], [2, 3, 3, 4, 5], [3, 3, 4, 5, 5]])
        db.add(matrix)
        await db.flush()
    return matrix


@router.get("/tickets/assignment-preview", response_model=AssignmentPreview)
async def assignment_preview(
    user: CurrentUser,
    db: DbSession,
    ticket_type_id: int = Query(gt=0),
    urgency: int = Query(default=3, ge=1, le=5),
    impact: int = Query(default=3, ge=1, le=5),
    category_id: int | None = Query(default=None, gt=0),
) -> AssignmentPreview:
    require_helpdesk(user)
    ticket_type, _ = await get_type_fields(db, ticket_type_id, user.entity_id)
    team_id = ticket_type.default_team_id
    agent_id = None
    rules = await db.scalars(select(BusinessRule).where(BusinessRule.entity_id == user.entity_id, BusinessRule.trigger == "ON_CREATE", BusinessRule.is_active.is_(True)).order_by(BusinessRule.order))
    for rule in rules:
        if category_id is not None and rule.conditions.get("category_id") not in (None, category_id):
            continue
        if "set_team" in rule.actions:
            group = await db.scalar(select(Group).where(Group.id == int(rule.actions["set_team"]), Group.entity_id == user.entity_id))
            if group:
                team_id = group.id
        if "set_assignee" in rule.actions:
            agent = await db.scalar(select(User).where(User.id == int(rule.actions["set_assignee"]), User.entity_id == user.entity_id, User.is_active.is_(True)))
            if agent:
                agent_id = agent.id
    team = await db.get(Group, team_id) if team_id else None
    agent = await db.get(User, agent_id) if agent_id else None
    matrix = await _get_matrix(db, user.entity_id)
    priority = calculate_matrix_priority(urgency, impact, matrix.matrix)
    sla = await db.scalar(select(SLA).where(SLA.id == ticket_type.default_sla_id, SLA.entity_id == user.entity_id, SLA.is_active.is_(True))) if ticket_type.default_sla_id else await db.scalar(select(SLA).where(SLA.entity_id == user.entity_id, SLA.is_active.is_(True)).order_by(SLA.id))
    now = datetime.now(UTC)
    return AssignmentPreview(team_id=team.id if team else None, team_name=team.name if team else None, agent_id=agent.id if agent else None, agent_name=agent.name if agent else None, priority=priority, sla=SlaPreview(
        sla_id=sla.id if sla else None, sla_name=sla.name if sla else None,
        response_target=(now + timedelta(minutes=sla.tto_minutes)).isoformat() if sla else None,
        fix_target=(now + timedelta(minutes=sla.ttr_minutes)).isoformat() if sla else None,
    ))
