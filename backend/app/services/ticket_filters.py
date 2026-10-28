"""Combinable ticket list filters (Release 1 Part 4).

Filters are ANDed together and always added on top of the caller's visibility predicate,
so a filter can only narrow what a user is already allowed to see.
"""

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from fastapi import HTTPException, Query
from sqlalchemy import String, func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from app.models import AuditLog, Ticket, TicketFollowup, TicketWorkflow
from app.services.attachments import live_followup_filter

# The single status mapping: (display label, stored value). Stored values never change.
STATUS_LABELS: tuple[tuple[str, str], ...] = (
    ("Open", "NEW"),
    ("Assigned", "ASSIGNED"),
    ("In Progress", "PLANNED"),
    ("On Hold", "PENDING"),
    ("Resolved", "SOLVED"),
    ("Closed", "CLOSED"),
)
STATUS_VALUE_TO_LABEL = {value: label for label, value in STATUS_LABELS}
STATUS_LABEL_KEYS = {label.lower(): label for label, _ in STATUS_LABELS}
STATUS_LABEL_TO_VALUE = dict(STATUS_LABELS)
FALLBACK_STATUS_LABEL = "In Progress"

# Low matches both stored values 4 (Low-Medium) and 5 (Low).
PRIORITY_FILTERS: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("Low", (4, 5)),
    ("Medium", (3,)),
    ("High", (2,)),
    ("Critical", (1,)),
)
PRIORITY_FILTER_KEYS = {label.lower(): values for label, values in PRIORITY_FILTERS}

MAX_FILTER_VALUES = 20
MAX_ID = 2_147_483_647
STAFF_ONLY_FILTERS = ("assignee_id", "requester_id", "ticket_type_id", "category_id", "updated_from", "updated_to")


def status_label(status: str) -> str:
    """Nearest display label for a stored status; workflow-defined states are matched by name."""
    if status in STATUS_VALUE_TO_LABEL:
        return STATUS_VALUE_TO_LABEL[status]
    key = re.sub(r"[\s_\-]+", " ", status.strip().lower())
    if key in STATUS_LABEL_KEYS:
        return STATUS_LABEL_KEYS[key]
    for words, label in (
        (("hold", "wait", "pend", "block"), "On Hold"),
        (("clos", "cancel", "reject"), "Closed"),
        (("resolv", "solv", "fix", "complet", "done"), "Resolved"),
        (("assign",), "Assigned"),
        (("new", "open"), "Open"),
    ):
        if any(word in key for word in words):
            return label
    return FALLBACK_STATUS_LABEL


def status_options() -> list[dict[str, str]]:
    return [{"value": label, "label": label, "stored_value": value} for label, value in STATUS_LABELS]


def priority_filter_options() -> list[dict[str, Any]]:
    return [{"value": label, "label": label, "matches": list(values)} for label, values in PRIORITY_FILTERS]


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _invalid(message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=message)


def parse_priority_tokens(tokens: list[Any]) -> set[int]:
    if not tokens or len(tokens) > MAX_FILTER_VALUES:
        raise _invalid("Invalid priority filter")
    values: set[int] = set()
    for token in tokens:
        if type(token) is int:
            text = str(token)
        elif isinstance(token, str):
            text = token.strip().lower()
        else:
            raise _invalid("Invalid priority filter")
        if text in PRIORITY_FILTER_KEYS:
            values.update(PRIORITY_FILTER_KEYS[text])
        elif text in {"1", "2", "3", "4", "5"}:
            values.add(int(text))
        else:
            raise _invalid("Priority must be Low, Medium, High, Critical or 1-5")
    return values


def resolve_status_tokens(tokens: list[Any], workflow_states: list[str] | None) -> set[str]:
    """Stored status values matched by labels, stored values or the entity's workflow states."""
    if not tokens or len(tokens) > MAX_FILTER_VALUES:
        raise _invalid("Invalid status filter")
    states = [state for state in (workflow_states or []) if isinstance(state, str)]
    values: set[str] = set()
    for token in tokens:
        if not isinstance(token, str) or not token.strip() or len(token) > 40:
            raise _invalid("Invalid status filter")
        text = token.strip()
        if text.lower() in STATUS_LABEL_KEYS:
            label = STATUS_LABEL_KEYS[text.lower()]
            values.add(STATUS_LABEL_TO_VALUE[label])
            values.update(state for state in states if status_label(state) == label)
        elif text.upper() in STATUS_VALUE_TO_LABEL:
            values.add(text.upper())
        elif text in states:
            values.add(text)
        else:
            raise _invalid("Unknown status filter")
    return values


def parse_aware_datetime(name: str, value: Any) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise _invalid(f"{name} must be an ISO 8601 date-time with a timezone") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise _invalid(f"{name} must be an ISO 8601 date-time with a timezone")
    return value


def parse_id_values(name: str, value: Any) -> list[int]:
    items = value if isinstance(value, list) else [value]
    if not items or len(items) > MAX_FILTER_VALUES:
        raise _invalid(f"Invalid {name} filter")
    for item in items:
        if type(item) is not int or not 1 <= item <= MAX_ID:
            raise _invalid(f"Invalid {name} filter")
    return list(items)


@dataclass
class TicketFilters:
    status: list[str] | None = None
    priority: list[str] | None = None
    search: str | None = None
    assignee_id: list[int] | None = None
    requester_id: list[int] | None = None
    ticket_type_id: list[int] | None = None
    category_id: list[int] | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    updated_from: datetime | None = None
    updated_to: datetime | None = None

    def with_basics(self, status: list[str] | None, priority: list[str] | None, search: str | None) -> "TicketFilters":
        return replace(self, status=status, priority=priority, search=search)


def coerce_filters(value: object) -> TicketFilters:
    # Direct (non-HTTP) callers do not pass filters; FastAPI injects a TicketFilters.
    return value if isinstance(value, TicketFilters) else TicketFilters()


def extra_filters(
    assignee_id: list[int] | None = Query(default=None),
    requester_id: list[int] | None = Query(default=None),
    ticket_type_id: list[int] | None = Query(default=None),
    category_id: list[int] | None = Query(default=None),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    updated_from: datetime | None = Query(default=None),
    updated_to: datetime | None = Query(default=None),
) -> TicketFilters:
    return TicketFilters(
        assignee_id=assignee_id,
        requester_id=requester_id,
        ticket_type_id=ticket_type_id,
        category_id=category_id,
        created_from=created_from,
        created_to=created_to,
        updated_from=updated_from,
        updated_to=updated_to,
    )


def all_filters(
    status: list[str] | None = Query(default=None),
    priority: list[str] | None = Query(default=None),
    search: str | None = Query(default=None, max_length=160),
    assignee_id: list[int] | None = Query(default=None),
    requester_id: list[int] | None = Query(default=None),
    ticket_type_id: list[int] | None = Query(default=None),
    category_id: list[int] | None = Query(default=None),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    updated_from: datetime | None = Query(default=None),
    updated_to: datetime | None = Query(default=None),
) -> TicketFilters:
    return TicketFilters(
        status, priority, search, assignee_id, requester_id, ticket_type_id, category_id,
        created_from, created_to, updated_from, updated_to,
    )


def ticket_updated_at():
    """Last activity: latest of open/solve/close time, live followups and the ticket's audit rows."""
    last_followup = (
        select(func.max(TicketFollowup.created_at))
        .where(TicketFollowup.ticket_id == Ticket.id, live_followup_filter())
        .correlate(Ticket)
        .scalar_subquery()
    )
    last_audit = (
        select(func.max(AuditLog.ts))
        .where(AuditLog.entity == "ticket", AuditLog.entity_id == Ticket.id)
        .correlate(Ticket)
        .scalar_subquery()
    )
    return func.greatest(Ticket.opened_at, Ticket.solved_at, Ticket.closed_at, last_followup, last_audit)


def _date_range(column: Any, start: datetime | None, end: datetime | None, label: str) -> list[ColumnElement[bool]]:
    if start is not None and end is not None and start > end:
        raise _invalid(f"{label}_from must not be after {label}_to")
    conditions = []
    if start is not None:
        conditions.append(column >= start)
    if end is not None:
        conditions.append(column <= end)
    return conditions


async def ticket_filter_conditions(db: Any, user: Any, filters: TicketFilters) -> list[ColumnElement[bool]]:
    requester_view = user.profile.interface == "self_service"
    if requester_view and any(getattr(filters, name) is not None for name in STAFF_ONLY_FILTERS):
        raise HTTPException(status_code=403, detail="Filter not available")

    conditions: list[ColumnElement[bool]] = []
    if filters.status is not None:
        workflow_states = await db.scalar(
            select(TicketWorkflow.states).where(
                TicketWorkflow.entity_id == user.entity_id,
                TicketWorkflow.record_type == "ticket",
                TicketWorkflow.is_active.is_(True),
            )
        )
        conditions.append(Ticket.status.in_(sorted(resolve_status_tokens(filters.status, workflow_states))))
    if filters.priority is not None:
        conditions.append(Ticket.priority.in_(sorted(parse_priority_tokens(filters.priority))))
    for name, column in (
        ("assignee_id", Ticket.assignee_id),
        ("requester_id", Ticket.requester_id),
        ("ticket_type_id", Ticket.ticket_type_id),
        ("category_id", Ticket.category_id),
    ):
        value = getattr(filters, name)
        if value is not None:
            conditions.append(column.in_(parse_id_values(name, value)))
    created_from = parse_aware_datetime("created_from", filters.created_from) if filters.created_from is not None else None
    created_to = parse_aware_datetime("created_to", filters.created_to) if filters.created_to is not None else None
    updated_from = parse_aware_datetime("updated_from", filters.updated_from) if filters.updated_from is not None else None
    updated_to = parse_aware_datetime("updated_to", filters.updated_to) if filters.updated_to is not None else None
    conditions.extend(_date_range(Ticket.opened_at, created_from, created_to, "created"))
    if updated_from is not None or updated_to is not None:
        conditions.extend(_date_range(ticket_updated_at(), updated_from, updated_to, "updated"))
    if filters.search and filters.search.strip():
        pattern = f"%{escape_like(filters.search.strip())}%"
        conditions.append(or_(
            Ticket.title.ilike(pattern, escape="\\"),
            Ticket.description.ilike(pattern, escape="\\"),
            Ticket.id.cast(String).ilike(pattern, escape="\\"),
        ))
    return conditions


def validate_filter_profile(config: dict) -> None:
    """Shape checks for a saved filter profile; old profiles (single status/priority) stay valid."""
    if "status" in config:
        tokens = config["status"] if isinstance(config["status"], list) else [config["status"]]
        resolve_status_tokens(tokens, None)
    if "priority" in config:
        tokens = config["priority"] if isinstance(config["priority"], list) else [config["priority"]]
        parse_priority_tokens(tokens)
    for name in ("assignee_id", "requester_id", "ticket_type_id", "category_id"):
        if name in config:
            parse_id_values(name, config[name])
    ranges = {}
    for name in ("created_from", "created_to", "updated_from", "updated_to"):
        if name in config:
            ranges[name] = parse_aware_datetime(name, config[name])
    for label in ("created", "updated"):
        start, end = ranges.get(f"{label}_from"), ranges.get(f"{label}_to")
        if start is not None and end is not None and start > end:
            raise _invalid(f"{label}_from must not be after {label}_to")
