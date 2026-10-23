import pytest
from fastapi import HTTPException

from app.services.tickets import calculate_matrix_priority, calculate_priority, transition_status


def test_priority_matrix_is_configurable_by_inputs() -> None:
    assert calculate_priority(1, 1) == 1
    assert calculate_priority(5, 5) == 5
    assert calculate_priority(3, 3) == 3


def test_assignment_requires_assignee() -> None:
    with pytest.raises(HTTPException):
        transition_status("NEW", "ASSIGNED", False)


def test_solved_can_close() -> None:
    result = transition_status("SOLVED", "CLOSED", True)
    assert result["status"] == "CLOSED"
    assert result["closed_at"] is not None


def test_closed_ticket_cannot_reopen() -> None:
    with pytest.raises(HTTPException):
        transition_status("CLOSED", "ASSIGNED", True)


def test_custom_priority_matrix_is_used_and_validated() -> None:
    matrix = [[5] * 5, [4] * 5, [3] * 5, [2] * 5, [1] * 5]
    assert calculate_matrix_priority(1, 5, matrix) == 5
    assert calculate_matrix_priority(5, 1, matrix) == 1
    with pytest.raises(ValueError):
        calculate_matrix_priority(1, 1, [[1]])
