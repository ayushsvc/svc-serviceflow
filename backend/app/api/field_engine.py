from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, status
from sqlalchemy import delete, select

from app.deps import CurrentUser, DbSession, require_admin, require_helpdesk
from app.models import BusinessRule, Category, CategoryTeam, FieldDefinition, Group, PriorityMatrix, SLA, TicketTypeDefinition, TicketTypeField, TicketTemplate, User
from app.schemas_field_engine import AssignmentPreview, FieldDefinitionCreate, FieldDefinitionRead, FormField, FormSchemaRead, PriorityMatrixPayload, PriorityMatrixRead, SlaPreview, TemplateCreate, TemplateRead, TicketTypeCreate, TicketTypeRead
from app.services.audit import record_audit
from app.services.field_engine import get_type_fields, sanitize_field_values, validate_condition
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


@router.get("/templates", response_model=list[TemplateRead])
async def list_templates(user: CurrentUser, db: DbSession) -> list[TicketTemplate]:
    require_helpdesk(user)
    return list((await db.scalars(select(TicketTemplate).join(TicketTypeDefinition, TicketTemplate.ticket_type_id == TicketTypeDefinition.id).where(
        (TicketTypeDefinition.entity_id == user.entity_id) | (TicketTypeDefinition.entity_id.is_(None)),
    ).order_by(TicketTemplate.name))).all())


@router.post("/templates", response_model=TemplateRead, status_code=status.HTTP_201_CREATED)
async def create_template(payload: TemplateCreate, user: CurrentUser, db: DbSession) -> TicketTemplate:
    require_admin(user)
    ticket_type, _ = await get_type_fields(db, payload.ticket_type_id, user.entity_id)
    await _entity_group(db, payload.team_id, user.entity_id)
    values = payload.model_dump()
    values["prefilled"] = await sanitize_field_values(db, payload.ticket_type_id, user.entity_id, values["prefilled"])
    template = TicketTemplate(**values)
    db.add(template)
    await db.flush()
    await record_audit(db, entity="ticket_template", entity_id=template.id, action="created", actor_id=user.id, diff={"ticket_type_id": ticket_type.id})
    await db.commit()
    await db.refresh(template)
    return template


@router.patch("/templates/{template_id}", response_model=TemplateRead)
async def update_template(template_id: int, payload: TemplateCreate, user: CurrentUser, db: DbSession) -> TicketTemplate:
    require_admin(user)
    template = await db.get(TicketTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    await get_type_fields(db, payload.ticket_type_id, user.entity_id)
    await _entity_group(db, payload.team_id, user.entity_id)
    changes = payload.model_dump()
    changes["prefilled"] = await sanitize_field_values(db, payload.ticket_type_id, user.entity_id, changes["prefilled"])
    for key, value in changes.items():
        setattr(template, key, value)
    await record_audit(db, entity="ticket_template", entity_id=template.id, action="updated", actor_id=user.id, diff=changes)
    await db.commit()
    await db.refresh(template)
    return template


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(template_id: int, user: CurrentUser, db: DbSession) -> None:
    require_admin(user)
    template = await db.get(TicketTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    await db.delete(template)
    await record_audit(db, entity="ticket_template", entity_id=template_id, action="deleted", actor_id=user.id, diff={})
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
