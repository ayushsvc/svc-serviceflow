import re
from urllib.parse import unquote
from uuid import uuid4

import pytest

from app.api import auth
from app.core.security import verify_password
from app.models import Group, GroupMember, User
from tests.test_release1_part4 import act_as, part4  # noqa: F401


class MemoryRedis:
    values: dict[str, str] = {}

    @classmethod
    def from_url(cls, *_args, **_kwargs):
        return cls()

    async def setex(self, key: str, _ttl: int, value: str) -> None:
        self.values[key] = value

    async def eval(self, _script: str, _key_count: int, key: str, value: str) -> int:
        if self.values.get(key) != value:
            return 0
        del self.values[key]
        return 1

    async def aclose(self) -> None:
        return None


@pytest.fixture
def invitation_mail(monkeypatch):
    sent = []
    MemoryRedis.values = {}
    monkeypatch.setattr(auth, "Redis", MemoryRedis)
    monkeypatch.setattr(auth, "send_email", sent.append)
    return sent


def _token(sent: list) -> str:
    match = re.search(r"\?invite=([^\s]+)", sent[-1].get_content())
    assert match is not None
    return unquote(match.group(1))


async def test_invitation_creates_inactive_user_and_acceptance_activates_once(part4, invitation_mail):
    act_as(part4, "A_admin")
    email = f"invite-{uuid4().hex}@example.com"
    response = await part4["client"].post(
        "/api/v1/auth/invitations",
        json={
            "name": "Invited Technician",
            "email": email,
            "profile_id": part4["users"]["A_tech"].profile_id,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["is_active"] is False
    assert len(invitation_mail) == 1

    invited = await part4["db"].get(User, body["id"])
    assert invited is not None and invited.is_active is False
    accepted = await part4["client"].post(
        "/api/v1/auth/invitations/accept",
        json={"token": _token(invitation_mail), "password": "Strong-password-2026"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["email"] == email
    await part4["db"].refresh(invited)
    assert invited.is_active is True
    assert verify_password("Strong-password-2026", invited.password_hash)

    reused = await part4["client"].post(
        "/api/v1/auth/invitations/accept",
        json={"token": _token(invitation_mail), "password": "Another-password-2026"},
    )
    assert reused.status_code == 400


async def test_invitation_assigns_only_teams_in_the_admin_entity(part4, invitation_mail):
    group = Group(name="Invite team A", entity_id=part4["entities"]["A"].id)
    foreign_group = Group(name="Invite team B", entity_id=part4["entities"]["B"].id)
    part4["db"].add_all([group, foreign_group])
    await part4["db"].flush()
    act_as(part4, "A_admin")

    accepted_team = await part4["client"].post(
        "/api/v1/auth/invitations",
        json={
            "name": "Team member",
            "email": f"team-{uuid4().hex}@example.com",
            "profile_id": part4["users"]["A_tech"].profile_id,
            "team_ids": [group.id],
        },
    )
    assert accepted_team.status_code == 201
    member = await part4["db"].get(GroupMember, (group.id, accepted_team.json()["id"]))
    assert member is not None

    rejected_team = await part4["client"].post(
        "/api/v1/auth/invitations",
        json={
            "name": "Foreign team member",
            "email": f"foreign-{uuid4().hex}@example.com",
            "profile_id": part4["users"]["A_tech"].profile_id,
            "team_ids": [foreign_group.id],
        },
    )
    assert rejected_team.status_code == 422


async def test_resending_invitation_invalidates_the_previous_link(part4, invitation_mail):
    act_as(part4, "A_admin")
    created = await part4["client"].post(
        "/api/v1/auth/invitations",
        json={
            "name": "Resend user",
            "email": f"resend-{uuid4().hex}@example.com",
            "profile_id": part4["users"]["A_requester"].profile_id,
        },
    )
    assert created.status_code == 201
    first_token = _token(invitation_mail)
    resent = await part4["client"].post(
        f"/api/v1/auth/invitations/{created.json()['id']}/resend"
    )
    assert resent.status_code == 200
    second_token = _token(invitation_mail)
    assert first_token != second_token

    old_link = await part4["client"].post(
        "/api/v1/auth/invitations/accept",
        json={"token": first_token, "password": "Strong-password-2026"},
    )
    assert old_link.status_code == 400
    current_link = await part4["client"].post(
        "/api/v1/auth/invitations/accept",
        json={"token": second_token, "password": "Strong-password-2026"},
    )
    assert current_link.status_code == 200


async def test_only_admins_can_manage_users_and_access_is_entity_scoped(part4, invitation_mail):
    act_as(part4, "A_requester")
    assert (await part4["client"].get("/api/v1/auth/users")).status_code == 403
    denied = await part4["client"].post(
        "/api/v1/auth/invitations",
        json={
            "name": "Should not exist",
            "email": f"denied-{uuid4().hex}@example.com",
            "profile_id": part4["users"]["A_requester"].profile_id,
        },
    )
    assert denied.status_code == 403
    assert invitation_mail == []

    act_as(part4, "A_admin")
    foreign_user = part4["users"]["B_tech"]
    result = await part4["client"].patch(
        f"/api/v1/auth/users/{foreign_user.id}",
        json={"is_active": False},
    )
    assert result.status_code == 404
    assert foreign_user.is_active is True


async def test_admin_can_deactivate_local_users_but_not_themselves(part4, invitation_mail):
    act_as(part4, "A_admin")
    requester = part4["users"]["A_requester"]
    response = await part4["client"].patch(
        f"/api/v1/auth/users/{requester.id}",
        json={"is_active": False},
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False

    self_deactivation = await part4["client"].patch(
        f"/api/v1/auth/users/{part4['users']['A_admin'].id}",
        json={"is_active": False},
    )
    assert self_deactivation.status_code == 409
