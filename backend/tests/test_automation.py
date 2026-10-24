from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.models import Ticket
from app.services.automation import calculate_sla_deadlines, conditions_match


def test_sla_deadlines_are_calculated_from_open_time() -> None:
    opened = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    ticket = Ticket(opened_at=opened)

    calculate_sla_deadlines(ticket, 60, 480)

    assert ticket.tto_due_at == datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    assert ticket.ttr_due_at == datetime(2026, 1, 1, 17, 0, tzinfo=UTC)


def test_sla_deadlines_use_business_hours_and_skip_weekends() -> None:
    opened = datetime(2026, 10, 23, 15, 30, tzinfo=UTC)
    ticket = Ticket(opened_at=opened)
    calendar = {
        "timezone": "UTC",
        "working_days": [1, 2, 3, 4, 5],
        "start": "09:00",
        "end": "17:00",
    }

    calculate_sla_deadlines(ticket, 120, 600, calendar)

    assert ticket.tto_due_at == datetime(2026, 10, 26, 9, 30, tzinfo=UTC)
    assert ticket.ttr_due_at == datetime(2026, 10, 27, 9, 30, tzinfo=UTC)


def test_sla_deadlines_skip_configured_holidays_and_convert_timezone() -> None:
    opened = datetime(2026, 12, 24, 15, 0, tzinfo=UTC)
    ticket = Ticket(opened_at=opened)
    calendar = {
        "timezone": "Asia/Kolkata",
        "working_days": [1, 2, 3, 4, 5],
        "start": "09:00",
        "end": "17:00",
        "holidays": ["2026-12-25"],
    }

    calculate_sla_deadlines(ticket, 60, 60, calendar)

    assert ticket.tto_due_at == datetime(2026, 12, 28, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert ticket.ttr_due_at == ticket.tto_due_at


@pytest.mark.parametrize(
    "calendar",
    [
        {"timezone": "Not/A_Timezone"},
        {"working_days": []},
        {"start": "17:00", "end": "09:00"},
        {"holidays": ["not-a-date"]},
    ],
)
def test_invalid_sla_calendars_fail_clearly(calendar: dict) -> None:
    ticket = Ticket(opened_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC))
    with pytest.raises(ValueError, match="SLA calendar"):
        calculate_sla_deadlines(ticket, 60, 120, calendar)


def test_rule_conditions_support_all_any_and_contains() -> None:
    ticket = Ticket(status="NEW", priority=4, title="VPN access request")

    assert conditions_match(
        ticket,
        {"all": [{"field": "status", "op": "eq", "value": "NEW"}, {"field": "priority", "op": "in", "value": [4, 5]}]},
    )
    assert conditions_match(ticket, {"any": [{"field": "status", "op": "eq", "value": "SOLVED"}, {"field": "title", "op": "contains", "value": "VPN"}]})
