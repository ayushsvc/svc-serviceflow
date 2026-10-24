from datetime import date, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FieldDefinition, Ticket, TicketFieldValue, TicketTypeDefinition, TicketTypeField
from app.services.content import sanitize_rich_text

MAX_CONDITION_DEPTH = 5


def validate_condition(condition: Any, depth: int = 0) -> None:
    if depth > MAX_CONDITION_DEPTH or not isinstance(condition, dict):
        raise ValueError("show_condition is too deeply nested or invalid")
    if not condition:
        return
    keys = set(condition)
    if keys & {"all", "any"}:
        if keys - {"all", "any"}:
            raise ValueError("show_condition groups may only contain all or any")
        groups = [condition[key] for key in ("all", "any") if key in condition]
        if any(not isinstance(group, list) or not group for group in groups):
            raise ValueError("show_condition groups must be non-empty lists")
        for group in groups:
            for child in group:
                validate_condition(child, depth + 1)
        return
    if not {"field", "op"} <= keys or keys - {"field", "op", "value"}:
        raise ValueError("show_condition must contain field and op")
    if not isinstance(condition["field"], str) or condition["op"] not in {"eq", "neq", "in", "exists"}:
        raise ValueError("show_condition uses an unsupported field or operator")
    if condition["op"] == "in" and not isinstance(condition.get("value"), list):
        raise ValueError("show_condition in requires a list value")


def _matches_condition(values: dict[str, Any], condition: dict[str, Any]) -> bool:
    validate_condition(condition)
    if not condition:
        return True
    if "all" in condition:
        return all(_matches_condition(values, item) for item in condition["all"])
    if "any" in condition:
        return any(_matches_condition(values, item) for item in condition["any"])
    actual = values.get(condition.get("field"))
    expected = condition.get("value")
    operation = condition.get("op", "eq")
    if operation == "eq":
        return actual == expected
    if operation == "neq":
        return actual != expected
    if operation == "in":
        return actual in expected
    if operation == "exists":
        return actual is not None
    return False


def _valid_value(field: FieldDefinition, value: Any) -> bool:
    if field.type in {"text", "textarea", "richtext", "url", "select"} and not isinstance(value, str):
        return False
    if field.type in {"user", "asset", "team", "agent", "category"} and not isinstance(value, int):
        return False
    if field.type == "number" and (not isinstance(value, int) or isinstance(value, bool)):
        return False
    choices = field.options.get("choices") if field.options else None
    if field.type == "select":
        return isinstance(choices, list) and bool(choices) and value in choices
    if field.type == "multiselect":
        return isinstance(value, list) and isinstance(choices, list) and bool(choices) and all(item in choices for item in value)
    if field.type == "checkbox" and not isinstance(value, bool):
        return False
    if field.type == "date":
        try:
            date.fromisoformat(str(value))
        except ValueError:
            return False
    if field.type == "datetime":
        try:
            datetime.fromisoformat(str(value))
        except ValueError:
            return False
    validation = field.validation or {}
    if isinstance(value, str) and "max_length" in validation and len(value) > int(validation["max_length"]):
        return False
    if field.type in {"richtext", "textarea"}:
        return True
    return True


async def get_type_fields(db: AsyncSession, ticket_type_id: int, entity_id: int) -> tuple[TicketTypeDefinition, list[tuple[FieldDefinition, TicketTypeField]]]:
    ticket_type = await db.scalar(select(TicketTypeDefinition).where(
        TicketTypeDefinition.id == ticket_type_id,
        (TicketTypeDefinition.entity_id == entity_id) | (TicketTypeDefinition.entity_id.is_(None)),
        TicketTypeDefinition.is_active.is_(True),
    ))
    if ticket_type is None:
        raise HTTPException(status_code=422, detail="Ticket type is outside your entity or inactive")
    rows = (await db.execute(
        select(FieldDefinition, TicketTypeField)
        .join(TicketTypeField, TicketTypeField.field_definition_id == FieldDefinition.id)
        .where(TicketTypeField.ticket_type_id == ticket_type_id)
        .order_by(TicketTypeField.order)
    )).all()
    return ticket_type, list(rows)


async def sanitize_field_values(
    db: AsyncSession,
    ticket_type_id: int,
    entity_id: int,
    values: dict[str, Any],
) -> dict[str, Any]:
    _, fields = await get_type_fields(db, ticket_type_id, entity_id)
    definitions = {definition.key: definition for definition, _ in fields}
    sanitized = dict(values)
    for key, value in sanitized.items():
        definition = definitions.get(key)
        if definition and definition.type in {"richtext", "textarea"} and isinstance(value, str):
            sanitized[key] = sanitize_rich_text(value)
    return sanitized


async def validate_custom_fields(
    db: AsyncSession,
    ticket_type_id: int,
    entity_id: int,
    role: str,
    values: dict[str, Any],
    existing: dict[str, Any] | None = None,
    trusted_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _, fields = await get_type_fields(db, ticket_type_id, entity_id)
    definitions = {definition.key: (definition, mapping) for definition, mapping in fields}
    unknown = set(values) - set(definitions)
    if unknown:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown ticket fields: {', '.join(sorted(unknown))}")
    derived_values = {key: value for key, value in (trusted_values or {}).items() if key in definitions}
    merged = {**(existing or {}), **values, **derived_values}
    for key, (definition, mapping) in definitions.items():
        active = _matches_condition(merged, mapping.show_condition)
        if not active and key in values:
            raise HTTPException(status_code=422, detail=f"Field '{key}' is not available for the selected values")
        if active and mapping.required and merged.get(key) in (None, "", []):
            raise HTTPException(status_code=422, detail=f"Field '{key}' is required")
        if key in values or key in derived_values:
            visible = role in mapping.visible_to_roles and (role != "Requester" or mapping.show_on_portal)
            if not visible and key not in derived_values:
                raise HTTPException(status_code=422, detail=f"Field '{key}' is not visible to your role")
            if role not in mapping.editable_by_roles:
                if key in derived_values:
                    continue
                raise HTTPException(status_code=422, detail=f"Field '{key}' is not editable by your role")
            if key in derived_values:
                continue
            if active and not _valid_value(definition, values[key]):
                raise HTTPException(status_code=422, detail=f"Invalid value for field '{key}'")
    return await sanitize_field_values(db, ticket_type_id, entity_id, merged)


async def store_field_values(db: AsyncSession, ticket: Ticket, values: dict[str, Any]) -> None:
    if not values or ticket.ticket_type_id is None:
        return
    definitions = {row.key: row.id for row in (await db.scalars(select(FieldDefinition).where(FieldDefinition.key.in_(values)))).all()}
    for key, value in values.items():
        if key in definitions:
            existing = await db.scalar(select(TicketFieldValue).where(
                TicketFieldValue.ticket_id == ticket.id,
                TicketFieldValue.field_definition_id == definitions[key],
            ))
            if existing:
                existing.value = value if isinstance(value, dict) else {"value": value}
            else:
                db.add(TicketFieldValue(ticket_id=ticket.id, field_definition_id=definitions[key], value=value if isinstance(value, dict) else {"value": value}))
