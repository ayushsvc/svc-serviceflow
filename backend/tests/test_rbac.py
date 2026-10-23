import pytest
from fastapi import HTTPException

from app.deps import require_admin


class Profile:
    def __init__(self, name: str) -> None:
        self.name = name


class User:
    def __init__(self, profile_name: str) -> None:
        self.profile = Profile(profile_name)


def test_only_administrator_can_manage_automation() -> None:
    assert require_admin(User("Administrator")).profile.name == "Administrator"
    with pytest.raises(HTTPException) as error:
        require_admin(User("Technician"))
    assert error.value.status_code == 403
