from datetime import UTC, datetime

from app.models import Ticket
from app.services.automation import calculate_sla_deadlines, conditions_match


def test_sla_deadlines_are_calculated_from_open_time() -> None:
    opened = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    ticket = Ticket(opened_at=opened)

    calculate_sla_deadlines(ticket, 60, 480)

    assert ticket.tto_due_at == datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    assert ticket.ttr_due_at == datetime(2026, 1, 1, 17, 0, tzinfo=UTC)


def test_rule_conditions_support_all_any_and_contains() -> None:
    ticket = Ticket(status="NEW", priority=4, title="VPN access request")

    assert conditions_match(
        ticket,
        {"all": [{"field": "status", "op": "eq", "value": "NEW"}, {"field": "priority", "op": "in", "value": [4, 5]}]},
    )
    assert conditions_match(ticket, {"any": [{"field": "status", "op": "eq", "value": "SOLVED"}, {"field": "title", "op": "contains", "value": "VPN"}]})
