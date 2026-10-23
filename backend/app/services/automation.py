from datetime import UTC, datetime, timedelta

from app.models import Ticket


def calculate_sla_deadlines(ticket: Ticket, tto_minutes: int, ttr_minutes: int) -> None:
    opened = ticket.opened_at or datetime.now(UTC)
    ticket.tto_due_at = opened + timedelta(minutes=tto_minutes)
    ticket.ttr_due_at = opened + timedelta(minutes=ttr_minutes)


def condition_matches(ticket: Ticket, condition: dict) -> bool:
    field = condition.get("field")
    op = condition.get("op", "eq")
    expected = condition.get("value")
    actual = getattr(ticket, field, None)
    if op == "eq":
        return actual == expected
    if op == "in":
        return actual in expected
    if op == "contains":
        return expected in (actual or "")
    return False


def conditions_match(ticket: Ticket, conditions: dict) -> bool:
    if not conditions:
        return True
    if "all" in conditions:
        return all(condition_matches(ticket, item) for item in conditions["all"])
    if "any" in conditions:
        return any(condition_matches(ticket, item) for item in conditions["any"])
    return condition_matches(ticket, conditions)
