from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models import Ticket


def _add_business_minutes(start: datetime, minutes: int, calendar: dict) -> datetime:
    if minutes < 0:
        raise ValueError("SLA duration cannot be negative")
    if not calendar:
        return start + timedelta(minutes=minutes)

    timezone_name = calendar.get("timezone", "UTC")
    try:
        timezone = ZoneInfo(timezone_name)
    except (TypeError, ZoneInfoNotFoundError) as exc:
        raise ValueError("SLA calendar timezone is invalid") from exc

    working_days = calendar.get("working_days", [1, 2, 3, 4, 5])
    if (
        not isinstance(working_days, list)
        or not working_days
        or any(type(day) is not int or not 1 <= day <= 7 for day in working_days)
    ):
        raise ValueError("SLA calendar working_days must contain ISO weekdays from 1 to 7")
    try:
        day_start = time.fromisoformat(calendar.get("start", "09:00"))
        day_end = time.fromisoformat(calendar.get("end", "17:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("SLA calendar start and end must be valid local times") from exc
    if day_start >= day_end:
        raise ValueError("SLA calendar end must be later than its start")

    holidays = calendar.get("holidays", [])
    if not isinstance(holidays, list):
        raise ValueError("SLA calendar holidays must be a list of ISO dates")
    try:
        holiday_dates = {date.fromisoformat(value) for value in holidays}
    except (TypeError, ValueError) as exc:
        raise ValueError("SLA calendar holidays must be a list of ISO dates") from exc

    local = start.replace(tzinfo=timezone) if start.tzinfo is None else start.astimezone(timezone)
    remaining = minutes
    for _ in range(3660):
        is_working_day = local.isoweekday() in working_days and local.date() not in holiday_dates
        opens = datetime.combine(local.date(), day_start, tzinfo=timezone)
        closes = datetime.combine(local.date(), day_end, tzinfo=timezone)
        if not is_working_day or local >= closes:
            next_date = local.date() + timedelta(days=1)
            local = datetime.combine(next_date, day_start, tzinfo=timezone)
            continue
        if local < opens:
            local = opens
        available = int((closes - local).total_seconds() // 60)
        if remaining <= available:
            return local + timedelta(minutes=remaining)
        remaining -= available
        next_date = local.date() + timedelta(days=1)
        local = datetime.combine(next_date, day_start, tzinfo=timezone)
    raise ValueError("SLA calendar has no reachable working time within ten years")


def calculate_sla_deadlines(
    ticket: Ticket,
    tto_minutes: int,
    ttr_minutes: int,
    calendar: dict | None = None,
) -> None:
    opened = ticket.opened_at or datetime.now(UTC)
    schedule = calendar or {}
    ticket.tto_due_at = _add_business_minutes(opened, tto_minutes, schedule)
    ticket.ttr_due_at = _add_business_minutes(opened, ttr_minutes, schedule)


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
