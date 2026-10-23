import pytest

from app.services.content import sanitize_rich_text
from app.services.field_engine import validate_condition


def test_rich_text_allowlist_removes_script_and_event_handlers() -> None:
    value = sanitize_rich_text('<p onclick="alert(1)">Safe</p><script>alert(2)</script><a href="javascript:alert(3)">bad</a>')
    assert value == "<p>Safe</p>alert(2)<a>bad</a>"
    assert "onclick" not in value
    assert "javascript:" not in value


def test_condition_validation_rejects_unknown_operators() -> None:
    with pytest.raises(ValueError):
        validate_condition({"field": "urgency", "op": "contains", "value": "1"})


def test_condition_validation_rejects_deep_nesting() -> None:
    condition: dict[str, object] = {"field": "flag", "op": "eq", "value": True}
    for _ in range(6):
        condition = {"all": [condition]}
    with pytest.raises(ValueError):
        validate_condition(condition)
