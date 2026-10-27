from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

from fastapi import HTTPException, status
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import get_settings
from app.models import (
    BusinessRule,
    Entity,
    Group,
    GroupMember,
    PriorityMatrix,
    SLA,
    Ticket,
    TicketActor,
    TicketFollowup,
    TicketWorkflow,
    TicketTypeDefinition,
    User,
    WorkflowApproval,
)
from app.schemas_tickets import TicketBulkUpdate
from app.services.audit import record_audit
from app.services.automation import calculate_sla_deadlines, conditions_match
from app.services.content import sanitize_rich_text, sanitize_ticket_description
from app.services.field_engine import field_role_for_user, get_type_fields, store_field_values, validate_custom_fields
from app.services.tickets import calculate_priority, priority_urgency_impact, transition_status
from app.workers.tasks import send_ticket_notification, send_user_ticket_notification

BULK_RATE_LIMIT = 20
BULK_RATE_WINDOW_SECONDS = 60


@dataclass
class TicketUpdateEffects:
    changed: bool
    notifications: list[str]
    changed_values: dict[str, dict[str, Any]]
    assignee_notifications: list[tuple[int, str]]
    team_notifications: list[int]


class BulkActionFailure(Exception):
    def __init__(self, reason: str) -> None:
        if reason not in {
            "forbidden_action",
            "invalid_transition",
            "approval_required",
            "invalid_value",
        }:
            raise ValueError("Unsupported bulk failure reason")
        self.reason = reason


async def enforce_bulk_rate_limit(user_id: int) -> None:
    redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        key = f"tickets:bulk:{user_id}"
        attempts = await redis.incr(key)
        if attempts == 1:
            await redis.expire(key, BULK_RATE_WINDOW_SECONDS)
        if attempts > BULK_RATE_LIMIT:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Bulk ticket rate limit exceeded",
            )
    except RedisError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bulk ticket service temporarily unavailable",
        ) from exc
    finally:
        await redis.aclose()


async def _apply_status(
    db: AsyncSession,
    ticket: Ticket,
    user: User,
    values: dict[str, Any],
) -> None:
    target = values["status"]
    workflow = await db.scalar(
        select(TicketWorkflow).where(
            TicketWorkflow.entity_id == user.entity_id,
            TicketWorkflow.record_type == "ticket",
            TicketWorkflow.is_active.is_(True),
        )
    )
    if workflow is None:
        values.update(
            transition_status(
                ticket.status,
                target,
                values.get("assignee_id", ticket.assignee_id) is not None,
            )
        )
        return
    if ticket.status not in workflow.states or target not in workflow.states:
        raise HTTPException(status_code=422, detail="Ticket status is not configured")
    if target != ticket.status and target not in workflow.transitions.get(ticket.status, []):
        raise HTTPException(status_code=409, detail="Invalid ticket transition")
    if target in {"ASSIGNED", "PLANNED"} and values.get("assignee_id", ticket.assignee_id) is None:
        raise HTTPException(status_code=422, detail="An assignee is required")
    required_approvals = workflow.approval_requirements.get(target, 0)
    if required_approvals:
        approved = await db.scalar(
            select(func.count(func.distinct(WorkflowApproval.decided_by)))
            .select_from(WorkflowApproval)
            .where(
                WorkflowApproval.entity_id == user.entity_id,
                WorkflowApproval.record_type == "ticket",
                WorkflowApproval.record_id == ticket.id,
                WorkflowApproval.transition_to == target,
                WorkflowApproval.status == "APPROVED",
            )
        )
        if (approved or 0) < required_approvals:
            raise HTTPException(status_code=409, detail="Required approvals are incomplete")
    from datetime import UTC, datetime

    if target == "SOLVED":
        values["solved_at"] = ticket.solved_at or datetime.now(UTC)
    elif target == "CLOSED":
        values["closed_at"] = datetime.now(UTC)


async def _apply_priority_sla(
    db: AsyncSession,
    ticket: Ticket,
    user: User,
    priority: int,
) -> None:
    entity_name = await db.scalar(select(Entity.name).where(Entity.id == user.entity_id))
    sla = await db.scalar(
        select(SLA).where(
            SLA.entity_id == user.entity_id,
            SLA.name == f"{entity_name} Priority {priority} SLA",
            SLA.is_active.is_(True),
        )
    )
    if sla is not None:
        ticket.sla_id = sla.id
        calculate_sla_deadlines(ticket, sla.tto_minutes, sla.ttr_minutes, sla.calendar)


async def apply_ticket_update(
    db: AsyncSession,
    ticket: Ticket,
    user: User,
    values: dict[str, Any],
    *,
    audit_action: str = "updated",
    bulk_request_id: str | None = None,
    visibility_scope: ColumnElement[bool] | None = None,
    expected_unassigned: bool = False,
    visibility_scope_factory: Callable[[User], ColumnElement[bool]] | None = None,
) -> TicketUpdateEffects:
    locked_ticket = await db.scalar(
        select(Ticket)
        .where(
            Ticket.id == ticket.id,
            Ticket.entity_id == user.entity_id,
            Ticket.deleted_at.is_(None),
            *(
                [visibility_scope]
                if visibility_scope is not None and not expected_unassigned
                else []
            ),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked_ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket = locked_ticket
    if expected_unassigned and ticket.assignee_id is not None:
        raise HTTPException(status_code=409, detail="Ticket is already assigned")
    if expected_unassigned and visibility_scope is not None and await db.scalar(
        select(Ticket.id).where(Ticket.id == ticket.id, visibility_scope)
    ) is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    previous_status = ticket.status
    previous_assignee_id = ticket.assignee_id
    previous_team_id = await db.scalar(
        select(TicketActor.actor_id)
        .where(
            TicketActor.ticket_id == ticket.id,
            TicketActor.actor_type == "GROUP",
            TicketActor.role == "ASSIGNEE",
        )
        .order_by(TicketActor.id)
        .limit(1)
    )
    tracked_fields = {
        "title",
        "description",
        "status",
        "urgency",
        "impact",
        "priority",
        "ticket_type_id",
        "type",
        "category_id",
        "assignee_id",
        "flagged",
        "sla_id",
    }
    original_values = {field: getattr(ticket, field) for field in tracked_fields}
    original_custom_fields = dict(ticket.custom_fields or {})
    changes = dict(values)
    explicit_priority = changes.get("priority") if "priority" in changes else None
    unassign_reset = False
    if "assignee_id" in changes and changes["assignee_id"] != ticket.assignee_id:
        if changes["assignee_id"] is None and ticket.assignee_id is not None:
            if "status" not in changes:
                if ticket.status == "ASSIGNED":
                    # Returning to the queue is an assignment action, not a manual status transition.
                    unassign_reset = True
                elif ticket.status != "NEW":
                    raise HTTPException(status_code=409, detail="A ticket can only be unassigned while its status is Assigned")
        elif changes["assignee_id"] is not None and ticket.status == "NEW":
            changes.setdefault("status", "ASSIGNED")
    if "description" in changes and isinstance(changes["description"], str):
        try:
            changes["description"] = sanitize_ticket_description(changes["description"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if "ticket_type_id" in changes:
        if "sla" in changes.get("custom_fields", {}) and not has_right(user, "override_sla"):
            raise HTTPException(status_code=403, detail="SLA override permission required")
        new_type, new_fields = await get_type_fields(db, changes["ticket_type_id"], user.entity_id)
        new_field_keys = {definition.key for definition, _ in new_fields}
        retained_fields = {
            key: value
            for key, value in {
                **original_custom_fields,
                "summary": ticket.title,
                "details": ticket.description,
                "user": ticket.requester_id,
                "organisation": ticket.entity_id,
            }.items()
            if key in new_field_keys and key not in {"user", "organisation"}
        }
        retained_fields.update(changes.get("custom_fields", {}))
        changes["custom_fields"] = await validate_custom_fields(
            db,
            changes["ticket_type_id"],
            user.entity_id,
            field_role_for_user(user),
            retained_fields,
            trusted_values={"user": ticket.requester_id, "organisation": ticket.entity_id},
        )
        changes["type"] = new_type.itil_class
    elif "custom_fields" in changes and ticket.ticket_type_id is not None:
        if "sla" in changes["custom_fields"] and not has_right(user, "override_sla"):
            raise HTTPException(status_code=403, detail="SLA override permission required")
        changes["custom_fields"] = await validate_custom_fields(
            db,
            ticket.ticket_type_id,
            user.entity_id,
            field_role_for_user(user),
            changes["custom_fields"],
            ticket.custom_fields,
        )
    if "category_id" in changes and changes["category_id"] is not None:
        from app.models import Category

        if await db.scalar(
            select(Category).where(Category.id == changes["category_id"], Category.entity_id == user.entity_id)
        ) is None:
            raise HTTPException(status_code=422, detail="Category is outside your entity")
    if "assignee_id" in changes and changes["assignee_id"] is not None:
        assignee = await db.scalar(
            select(User)
            .options(selectinload(User.profile))
            .where(
                User.id == changes["assignee_id"],
                User.entity_id == user.entity_id,
                User.is_active.is_(True),
            )
        )
        if assignee is None:
            raise HTTPException(status_code=422, detail="Assignee is outside your entity or inactive")
        if not has_right(user, "ticket:assign_any") and assignee.id != user.id:
            actor_groups = select(GroupMember.group_id).where(GroupMember.user_id == user.id)
            team_member = await db.scalar(
                select(GroupMember.group_id)
                .join(Group, Group.id == GroupMember.group_id)
                .where(
                    GroupMember.user_id == assignee.id,
                    GroupMember.group_id.in_(actor_groups),
                    Group.entity_id == user.entity_id,
                )
            )
            if team_member is None:
                raise HTTPException(status_code=403, detail="Assignee must be you or a member of your team")
    if "team_id" in changes and changes["team_id"] is not None:
        if await db.scalar(
            select(Group).where(Group.id == changes["team_id"], Group.entity_id == user.entity_id)
        ) is None:
            raise HTTPException(status_code=422, detail="Team is outside your entity")

    if "assignee_id" in changes and changes["assignee_id"] != ticket.assignee_id and ticket.status == "NEW" and changes["assignee_id"] is not None:
        changes["status"] = "ASSIGNED"
    if changes.get("status") == ticket.status:
        changes.pop("status")
    skip_status_rules = False
    if unassign_reset:
        changes["status"] = "NEW"
        configured_workflow = await db.scalar(
            select(TicketWorkflow.id).where(
                TicketWorkflow.entity_id == user.entity_id,
                TicketWorkflow.record_type == "ticket",
                TicketWorkflow.is_active.is_(True),
            )
        )
        # Without a configured workflow the default transition table does not list ASSIGNED -> NEW.
        # Unassigning is an assignment action, so the reset is applied directly in that case only.
        skip_status_rules = configured_workflow is None
    if "status" in changes and not skip_status_rules:
        await _apply_status(db, ticket, user, changes)

    if explicit_priority is not None:
        changes["urgency"], changes["impact"] = priority_urgency_impact(explicit_priority)
    elif "urgency" in changes or "impact" in changes:
        matrix = await db.scalar(select(PriorityMatrix).where(PriorityMatrix.entity_id == user.entity_id))
        changes["priority"] = (
            calculate_priority(changes.get("urgency", ticket.urgency), changes.get("impact", ticket.impact))
            if matrix is None
            else matrix.matrix[changes.get("urgency", ticket.urgency) - 1][changes.get("impact", ticket.impact) - 1]
        )
    if "priority" in changes and changes["priority"] != ticket.priority:
        await _apply_priority_sla(db, ticket, user, changes["priority"])
    custom_fields = changes.pop("custom_fields", None)
    team_id = changes.pop("team_id", None) if "team_id" in changes else None
    for key, value in changes.items():
        setattr(ticket, key, value)
    if custom_fields is not None:
        ticket.custom_fields = {**ticket.custom_fields, **custom_fields}
        await store_field_values(db, ticket, custom_fields)
    if team_id != previous_team_id and ("team_id" in values):
        await db.execute(
            delete(TicketActor).where(
                TicketActor.ticket_id == ticket.id,
                TicketActor.actor_type == "GROUP",
                TicketActor.role == "ASSIGNEE",
            )
        )
        if team_id is not None:
            db.add(TicketActor(ticket_id=ticket.id, actor_type="GROUP", actor_id=team_id, role="ASSIGNEE"))
    if "assignee_id" in values and values["assignee_id"] != previous_assignee_id:
        await db.execute(
            delete(TicketActor).where(
                TicketActor.ticket_id == ticket.id,
                TicketActor.actor_type == "USER",
                TicketActor.role == "ASSIGNEE",
            )
        )
        if ticket.assignee_id is not None:
            db.add(TicketActor(ticket_id=ticket.id, actor_type="USER", actor_id=ticket.assignee_id, role="ASSIGNEE"))

    rules = await db.scalars(
        select(BusinessRule)
        .where(
            BusinessRule.entity_id == user.entity_id,
            BusinessRule.trigger == "ON_UPDATE",
            BusinessRule.is_active.is_(True),
        )
        .order_by(BusinessRule.order)
    )
    for rule in rules:
        if conditions_match(ticket, rule.conditions):
            actions = rule.actions
            if "set_priority" in actions:
                ticket.priority = int(actions["set_priority"])
            if "set_sla" in actions:
                sla = await db.scalar(
                    select(SLA).where(
                        SLA.id == int(actions["set_sla"]),
                        SLA.entity_id == user.entity_id,
                        SLA.is_active.is_(True),
                    )
                )
                if sla:
                    ticket.sla_id = sla.id
                    calculate_sla_deadlines(ticket, sla.tto_minutes, sla.ttr_minutes, sla.calendar)

    if explicit_priority is not None:
        ticket.priority = explicit_priority
        ticket.urgency, ticket.impact = priority_urgency_impact(explicit_priority)
        if explicit_priority != original_values["priority"]:
            await _apply_priority_sla(db, ticket, user, explicit_priority)

    await db.flush()
    if ticket.assignee_id is not None and visibility_scope_factory is not None:
        candidate = await db.scalar(
            select(User)
            .options(selectinload(User.profile))
            .where(
                User.id == ticket.assignee_id,
                User.entity_id == ticket.entity_id,
                User.is_active.is_(True),
            )
        )
        candidate_rights = getattr(getattr(candidate, "profile", None), "rights", None) or {}
        candidate_can_work = (
            candidate_rights.get("*") is True
            or candidate_rights.get("ticket:view_all") is True
            or candidate_rights.get("ticket:view_queue") is True
        )
        if candidate is None or not candidate_can_work or await db.scalar(
            select(Ticket.id).where(Ticket.id == ticket.id, visibility_scope_factory(candidate))
        ) is None:
            raise HTTPException(
                status_code=422,
                detail="assignee_cannot_view_ticket",
            )
    changed_values: dict[str, dict[str, Any]] = {}
    for key, old_value in original_values.items():
        new_value = getattr(ticket, key)
        if old_value != new_value:
            changed_values[key] = {"old": old_value, "new": new_value}
    if "team_id" in values and previous_team_id != team_id:
        changed_values["team_id"] = {"old": previous_team_id, "new": team_id}
    if custom_fields is not None and ticket.custom_fields != original_custom_fields:
        changed_values["custom_fields"] = {"old": original_custom_fields, "new": dict(ticket.custom_fields)}
    actual_change = bool(changed_values)
    notifications: list[str] = []
    assignee_notifications: list[tuple[int, str]] = []
    if ticket.assignee_id is not None and ticket.assignee_id != previous_assignee_id:
        notifications.append("assigned")
        assignee_notifications.append((ticket.assignee_id, "assigned_to_you"))
    if previous_assignee_id is not None and previous_assignee_id != ticket.assignee_id:
        assignee_notifications.append((previous_assignee_id, "assignment_removed"))
    if ticket.status == "SOLVED" and previous_status != "SOLVED":
        notifications.append("solved")
    if not actual_change:
        return TicketUpdateEffects(False, [], [], [], [])

    if bulk_request_id is not None:
        diff = {"bulk_request_id": bulk_request_id, **changed_values}
    else:
        diff = {**values, **changed_values}
    await record_audit(
        db,
        entity="ticket",
        entity_id=ticket.id,
        action=audit_action,
        actor_id=user.id,
        diff=diff,
    )
    if any(field in changed_values for field in ("status", "assignee_id", "team_id")):
        event_labels = ", ".join(
            f"{field}: {record['old']} -> {record['new']}"
            for field, record in changed_values.items()
            if field in {"status", "assignee_id", "team_id"}
        )
        db.add(
            TicketFollowup(
                ticket_id=ticket.id,
                author_id=user.id,
                body=sanitize_rich_text(f"Ticket updated ({event_labels})."),
                is_private=True,
            )
        )
    team_notifications: list[int] = []
    if previous_team_id != team_id and "team_id" in values and team_id is not None:
        team_notifications = list(
            (
                await db.scalars(
                    select(GroupMember.user_id)
                    .join(User, User.id == GroupMember.user_id)
                    .where(
                        GroupMember.group_id == team_id,
                        User.entity_id == user.entity_id,
                        User.is_active.is_(True),
                    )
                )
            ).all()
        )
    return TicketUpdateEffects(
        True,
        notifications,
        changed_values,
        assignee_notifications,
        team_notifications,
    )


def dispatch_ticket_update_effects(ticket_id: int, effects: TicketUpdateEffects) -> None:
    if not effects.changed:
        return
    for event in effects.notifications:
        send_ticket_notification.delay(ticket_id, event)
    for recipient_id, event in effects.assignee_notifications:
        send_user_ticket_notification.delay(ticket_id, recipient_id, event)
    for recipient_id in effects.team_notifications:
        send_user_ticket_notification.delay(ticket_id, recipient_id, "team_assigned")


def has_right(user: User, right: str) -> bool:
    rights = getattr(user.profile, "rights", None) or {}
    return rights.get("*") is True or rights.get(right) is True


def _reason_from_exception(exc: HTTPException) -> str:
    if exc.status_code == 403:
        return "forbidden_action"
    if exc.status_code == 409:
        if exc.detail == "Required approvals are incomplete":
            return "approval_required"
        return "invalid_transition"
    return "invalid_value"


async def _validate_action_value(
    db: AsyncSession,
    ticket: Ticket,
    user: User,
    payload: TicketBulkUpdate,
) -> dict[str, Any]:
    value = payload.value
    if payload.action == "status":
        if not isinstance(value, str) or value not in {"NEW", "ASSIGNED", "PLANNED", "PENDING", "SOLVED", "CLOSED"}:
            raise BulkActionFailure("invalid_value")
        return {"status": value}
    if payload.action == "priority":
        if type(value) is not int or not 1 <= value <= 5:
            raise BulkActionFailure("invalid_value")
        return {"priority": value}
    if payload.action == "flag":
        if type(value) is not bool:
            raise BulkActionFailure("invalid_value")
        return {"flagged": value}
    if payload.action == "team":
        if value is not None and (type(value) is not int or value <= 0):
            raise BulkActionFailure("invalid_value")
        if value is not None and await db.scalar(
            select(Group.id).where(Group.id == value, Group.entity_id == user.entity_id)
        ) is None:
            raise BulkActionFailure("invalid_value")
        return {"team_id": value}

    if type(value) is not int or value <= 0:
        raise BulkActionFailure("invalid_value")
    assignee = await db.scalar(
        select(User).where(User.id == value, User.entity_id == user.entity_id, User.is_active.is_(True))
    )
    if assignee is None:
        raise BulkActionFailure("invalid_value")
    if not has_right(user, "ticket:assign_any") and assignee.id != user.id:
        actor_group_ids = select(GroupMember.group_id).where(GroupMember.user_id == user.id)
        is_team_member = await db.scalar(
            select(GroupMember.group_id)
            .join(Group, Group.id == GroupMember.group_id)
            .where(
                GroupMember.user_id == assignee.id,
                GroupMember.group_id.in_(actor_group_ids),
                Group.entity_id == user.entity_id,
            )
        )
        if is_team_member is None:
            raise BulkActionFailure("forbidden_action")
    return {"assignee_id": value}


async def bulk_update_tickets(
    db: AsyncSession,
    user: User,
    payload: TicketBulkUpdate,
    visibility_scope: ColumnElement[bool],
    visibility_scope_factory: Callable[[User], ColumnElement[bool]] | None = None,
) -> dict[str, Any]:
    if not has_right(user, "ticket:bulk_edit"):
        raise HTTPException(status_code=403, detail="Bulk ticket permission required")
    await enforce_bulk_rate_limit(user.id)
    ticket_ids = sorted(set(payload.ticket_ids))
    bulk_request_id = str(uuid4())
    locked_tickets = list(
        (
            await db.scalars(
                select(Ticket)
                .where(
                    Ticket.id.in_(ticket_ids),
                    Ticket.entity_id == user.entity_id,
                    Ticket.deleted_at.is_(None),
                    visibility_scope,
                )
                .order_by(Ticket.id)
                .with_for_update()
            )
        ).all()
    )
    tickets_by_id = {ticket.id: ticket for ticket in locked_tickets}
    results: list[dict[str, Any]] = []
    side_effects: list[tuple[int, list[str], list[tuple[int, str]], list[int]]] = []
    for ticket_id in ticket_ids:
        ticket = tickets_by_id.get(ticket_id)
        if ticket is None:
            results.append({"ticket_id": ticket_id, "ok": False, "reason": "not_found"})
            continue
        try:
            async with db.begin_nested():
                values = await _validate_action_value(db, ticket, user, payload)
                effects = await apply_ticket_update(
                    db,
                    ticket,
                    user,
                    values,
                    audit_action=f"bulk_{payload.action}",
                    bulk_request_id=bulk_request_id,
                    visibility_scope_factory=visibility_scope_factory,
                )
                await db.flush()
        except BulkActionFailure as exc:
            results.append({"ticket_id": ticket_id, "ok": False, "reason": exc.reason})
        except HTTPException as exc:
            results.append({"ticket_id": ticket_id, "ok": False, "reason": _reason_from_exception(exc)})
        except (IntegrityError, ValueError):
            results.append({"ticket_id": ticket_id, "ok": False, "reason": "invalid_value"})
        else:
            if effects.changed:
                side_effects.append(
                    (
                        ticket_id,
                        effects.notifications,
                        effects.assignee_notifications,
                        effects.team_notifications,
                    )
                )
                results.append({"ticket_id": ticket_id, "ok": True, "reason": None})
            else:
                results.append({"ticket_id": ticket_id, "ok": True, "reason": "unchanged"})
    await db.commit()
    for ticket_id, events, assignee_events, team_users in side_effects:
        dispatch_ticket_update_effects(
            ticket_id,
            TicketUpdateEffects(True, events, {}, assignee_events, team_users),
        )
    succeeded = sum(1 for result in results if result["ok"])
    return {
        "results": results,
        "summary": {"ok": succeeded, "failed": len(results) - succeeded},
    }
