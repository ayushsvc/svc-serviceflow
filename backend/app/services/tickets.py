from datetime import UTC, datetime

from fastapi import HTTPException, status

from app.models import TicketStatus

PRIORITY_MATRIX: tuple[tuple[int, ...], ...] = (
    (1, 1, 2, 2, 3),
    (1, 2, 2, 3, 3),
    (2, 2, 3, 3, 4),
    (2, 3, 3, 4, 5),
    (3, 3, 4, 5, 5),
)

PRIORITY_URGENCY_IMPACT: dict[int, tuple[int, int]] = {
    1: (1, 1),
    2: (2, 2),
    3: (3, 3),
    4: (4, 4),
    5: (5, 5),
}

PRIORITY_LABELS: dict[int, str] = {
    1: "Critical",
    2: "High",
    3: "Medium",
    4: "Low-Medium",
    5: "Low",
}

REQUESTER_PRIORITY_VALUES: dict[str, int] = {
    "critical": 1,
    "high": 2,
    "medium": 3,
    "low": 5,
}


def priority_urgency_impact(priority: int) -> tuple[int, int]:
    try:
        return PRIORITY_URGENCY_IMPACT[priority]
    except KeyError as exc:
        raise ValueError("priority must be between 1 and 5") from exc


def priority_label(priority: int, *, requester: bool = False) -> str:
    if priority not in PRIORITY_LABELS:
        raise ValueError("priority must be between 1 and 5")
    if requester and priority in {4, 5}:
        return "Low"
    return PRIORITY_LABELS[priority]


def priority_options(*, requester: bool = False) -> list[dict[str, int | str]]:
    return [
        {"value": value, "label": priority_label(value, requester=requester)}
        for value in range(1, 6)
    ]


def requester_priority_value(value: object) -> int:
    if not isinstance(value, str) or value not in REQUESTER_PRIORITY_VALUES:
        raise ValueError("Requester priority must be low, medium, high, or critical")
    return REQUESTER_PRIORITY_VALUES[value]


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    TicketStatus.NEW.value: {TicketStatus.ASSIGNED.value, TicketStatus.PENDING.value},
    TicketStatus.ASSIGNED.value: {TicketStatus.PLANNED.value, TicketStatus.PENDING.value, TicketStatus.SOLVED.value},
    TicketStatus.PLANNED.value: {TicketStatus.ASSIGNED.value, TicketStatus.PENDING.value, TicketStatus.SOLVED.value},
    TicketStatus.PENDING.value: {TicketStatus.ASSIGNED.value, TicketStatus.SOLVED.value},
    TicketStatus.SOLVED.value: {TicketStatus.CLOSED.value, TicketStatus.ASSIGNED.value},
    TicketStatus.CLOSED.value: set(),
}


def calculate_priority(urgency: int, impact: int) -> int:
    if not 1 <= urgency <= 5 or not 1 <= impact <= 5:
        raise ValueError("urgency and impact must be between 1 and 5")
    return PRIORITY_MATRIX[urgency - 1][impact - 1]


def calculate_matrix_priority(urgency: int, impact: int, matrix: list[list[int]]) -> int:
    if not 1 <= urgency <= 5 or not 1 <= impact <= 5:
        raise ValueError("urgency and impact must be between 1 and 5")
    if len(matrix) != 5 or any(len(row) != 5 or any(not isinstance(value, int) or not 1 <= value <= 5 for value in row) for row in matrix):
        raise ValueError("priority matrix must be a 5 by 5 matrix containing priorities from 1 to 5")
    return matrix[urgency - 1][impact - 1]


def transition_status(current: str, target: str, has_assignee: bool) -> dict[str, object]:
    if target not in {item.value for item in TicketStatus}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown ticket status")
    if target != current and target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Cannot transition ticket from {current} to {target}")
    if target in {TicketStatus.ASSIGNED.value, TicketStatus.PLANNED.value} and not has_assignee:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="An assignee is required")
    now = datetime.now(UTC)
    return {"status": target, "solved_at": now if target == TicketStatus.SOLVED.value else None, "closed_at": now if target == TicketStatus.CLOSED.value else None}
