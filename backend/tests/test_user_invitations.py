import asyncio
import re
from datetime import timedelta
from urllib.parse import unquote
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from redis.exceptions import RedisError
from sqlalchemy import select

from app.api import auth
from app import deps as app_deps
from app.core.security import create_token, decode_token, hash_password, verify_password
from app.deps import current_user
from app.main import app
from app.models import AuditLog, Group, GroupMember, Profile, User
from app.schemas import InvitationAccept
from tests.test_release1_part4 import act_as, part4  # noqa: F401


class MemoryRedis:
    values: dict[str, str] = {}
    fail = False

    @classmethod
    def from_url(cls, *_args, **_kwargs):
        return cls()

    async def get(self, key: str) -> str | None:
        if self.fail:
            raise RedisError("redis unavailable")
        return self.values.get(key)

    async def incr(self, key: str) -> int:
        if self.fail:
            raise RedisError("redis unavailable")
        value = int(self.values.get(key, "0")) + 1
        self.values[key] = str(value)
        return value

    async def expire(self, *_args) -> bool:
        if self.fail:
            raise RedisError("redis unavailable")
        return True

    async def setex(self, key: str, _ttl: int, value: str) -> None:
        if self.fail:
            raise RedisError("redis unavailable")
        self.values[key] = value

    async def delete(self, *keys: str) -> int:
        if self.fail:
            raise RedisError("redis unavailable")
        return sum(self.values.pop(key, None) is not None for key in keys)

    async def ttl(self, key: str) -> int:
        if self.fail:
            raise RedisError("redis unavailable")
        return 86400 if key in self.values else -2

    async def eval(self, script: str, key_count: int, *args) -> int:
        if self.fail:
            raise RedisError("redis unavailable")
        keys = args[:key_count]
        values = args[key_count:]
        if key_count == 3 and "SET" in script and "EX" in script:
            invitation_key, claim_key, epoch_key = keys
            self.values.pop(invitation_key, None)
            self.values.pop(claim_key, None)
            self.values[epoch_key] = values[0]
            return 1
        if "SET" in script and "NX" in script:
            invitation_key, claim_key = keys
            token_id, ttl = values
            if self.values.get(invitation_key) != token_id or claim_key in self.values:
                return 0
            self.values[claim_key] = token_id
            return 1
        if key_count == 2 and "TTL" in script:
            invitation_key, claim_key = keys
            token_id = values[0]
            if self.values.get(invitation_key) != token_id or self.values.get(claim_key) != token_id:
                return 0
            del self.values[invitation_key]
            return 86400
        if key_count == 2 and "EXISTS" in script:
            invitation_key, claim_key = keys
            token_id, _ttl = values
            if self.values.get(claim_key) != token_id:
                return 0
            if invitation_key not in self.values:
                self.values[invitation_key] = token_id
            del self.values[claim_key]
            return 1
        if key_count == 2 and "SET" in script and "DEL" in script:
            invitation_key, claim_key = keys
            token_id, _ttl = values
            self.values[invitation_key] = token_id
            self.values.pop(claim_key, None)
            return 1
        if key_count == 2 and "return 1" in script and "KEYS[2]" in script and "DEL" not in script:
            invitation_key, claim_key = keys
            token_id = values[0]
            return int(self.values.get(invitation_key) == token_id and self.values.get(claim_key) == token_id)
        if key_count == 2 and "KEYS[2]" in script:
            invitation_key, claim_key = keys
            token_id = values[0]
            if self.values.get(invitation_key) != token_id or self.values.get(claim_key) != token_id:
                return 0
            del self.values[invitation_key]
            del self.values[claim_key]
            return 1
        if key_count == 1 and "DEL" in script:
            claim_key = keys[0]
            token_id = values[0]
            if self.values.get(claim_key) == token_id:
                del self.values[claim_key]
                return 1
            return 0
        key, value = keys[0], values[0]
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
    MemoryRedis.fail = False
    monkeypatch.setattr(auth, "Redis", MemoryRedis)
    monkeypatch.setattr(app_deps, "Redis", MemoryRedis)
    monkeypatch.setattr(auth, "send_email", sent.append)
    return sent


def _patch_settings(monkeypatch, **updates):
    settings = auth.get_settings().model_copy(update=updates)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)


def _token(sent: list) -> str:
    match = re.search(r"[?#]invite=([^\s]+)", sent[-1].get_content())
    assert match is not None
    return unquote(match.group(1))


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/invitations/accept",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
    )


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


async def test_invitation_email_uses_fragment_token_link(part4, invitation_mail):
    created = await _create_invitation(part4)

    assert created.status_code == 201
    body = invitation_mail[-1].get_content()
    link = next(line for line in body.splitlines() if "invite=" in line)
    assert link.startswith(f"{auth.get_settings().app_base_url}/#invite=")
    assert "?" not in link


async def test_invitation_audit_events_are_scoped_to_inviter_tenant(part4, invitation_mail):
    administrator_profile = await part4["db"].scalar(
        select(Profile).where(Profile.name == "Administrator")
    )
    if administrator_profile is None:
        administrator_profile = Profile(
            name="Administrator",
            interface="helpdesk",
            rights={"*": True},
        )
        part4["db"].add(administrator_profile)
        await part4["db"].flush()
    part4["users"]["A_admin"].profile = administrator_profile
    part4["users"]["B_admin"].profile = administrator_profile

    created = await _create_invitation(part4)
    assert created.status_code == 201
    invited_id = created.json()["id"]

    act_as(part4, "A_admin")
    accepted = await part4["client"].post(
        "/api/v1/auth/invitations/accept",
        json={"token": _token(invitation_mail), "password": "Secure-password-2026"},
    )
    assert accepted.status_code == 200
    deactivated = await part4["client"].patch(
        f"/api/v1/auth/users/{invited_id}",
        json={"is_active": False},
    )
    assert deactivated.status_code == 200
    activated = await part4["client"].patch(
        f"/api/v1/auth/users/{invited_id}",
        json={"is_active": True},
    )
    assert activated.status_code == 200
    part4["db"].add(
        AuditLog(
            entity="user",
            entity_id=invited_id,
            action="invited",
            actor_id=None,
            diff={"marker": "null actor must not be visible"},
        )
    )
    await part4["db"].flush()

    inviter_tenant_audit = (await part4["client"].get("/api/v1/audit")).json()
    invitation_events = [
        item for item in inviter_tenant_audit
        if item["entity"] == "user"
        and item["entity_id"] == invited_id
        and item["action"] in {"invited", "invitation_accepted", "activated", "deactivated"}
    ]
    assert {item["action"] for item in invitation_events} == {
        "invited",
        "invitation_accepted",
        "activated",
        "deactivated",
    }
    assert len([item for item in invitation_events if item["action"] == "invited"]) == 1
    assert next(item for item in invitation_events if item["action"] == "invited")["actor_id"] == part4["users"]["A_admin"].id
    assert next(item for item in invitation_events if item["action"] == "invitation_accepted")["actor_id"] == invited_id

    act_as(part4, "B_admin")
    other_tenant_audit = (await part4["client"].get("/api/v1/audit")).json()
    assert not any(
        item["entity"] == "user"
        and item["entity_id"] == invited_id
        and item["action"] in {"invited", "invitation_accepted", "activated", "deactivated"}
        for item in other_tenant_audit
    )
    assert not any(
        item["diff"].get("marker") == "null actor must not be visible"
        for item in other_tenant_audit
    )


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


async def _create_invitation(part4, email=None, profile_id=None):
    act_as(part4, "A_admin")
    response = await part4["client"].post(
        "/api/v1/auth/invitations",
        json={
            "name": "Security test invite",
            "email": email or f"security-{uuid4().hex}@example.com",
            "profile_id": profile_id or part4["users"]["A_requester"].profile_id,
        },
    )
    return response


async def test_deactivating_pending_invitation_cancels_key_and_acceptance(part4, invitation_mail):
    created = await _create_invitation(part4)
    assert created.status_code == 201
    token = _token(invitation_mail)
    user_id = created.json()["id"]
    token_id = decode_token(token)["jti"]
    assert decode_token(token)["type"] == "invitation"
    assert MemoryRedis.values[f"invitation:user:{user_id}"] == token_id

    act_as(part4, "A_admin")
    deactivated = await part4["client"].patch(
        f"/api/v1/auth/users/{user_id}",
        json={"is_active": False},
    )
    assert deactivated.status_code == 200
    assert f"invitation:user:{user_id}" not in MemoryRedis.values
    cancelled = await part4["db"].scalar(
        select(AuditLog.id).where(
            AuditLog.entity == "user",
            AuditLog.entity_id == user_id,
            AuditLog.action == "invitation_cancelled",
        )
    )
    assert cancelled is not None

    invalid = await part4["client"].post(
        "/api/v1/auth/invitations/accept",
        json={"token": "not-a-valid-invitation-token", "password": "Secure-password-2026"},
    )
    cancelled_attempt = await part4["client"].post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "password": "Secure-password-2026"},
    )
    assert invalid.status_code == cancelled_attempt.status_code == 400
    assert invalid.json()["detail"] == cancelled_attempt.json()["detail"]
    resend = await part4["client"].post(
        f"/api/v1/auth/invitations/{user_id}/resend"
    )
    assert resend.status_code == 409


async def test_acceptance_rejects_an_invited_user_with_a_prior_login(part4, invitation_mail):
    created = await _create_invitation(part4)
    invited = await part4["db"].get(User, created.json()["id"])
    assert invited is not None
    part4["db"].add(
        AuditLog(
            entity="user",
            entity_id=invited.id,
            action="login",
            actor_id=invited.id,
            diff={},
        )
    )
    await part4["db"].flush()

    response = await part4["client"].post(
        "/api/v1/auth/invitations/accept",
        json={"token": _token(invitation_mail), "password": "Secure-password-2026"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invitation is invalid, expired, or cancelled"


async def test_invitation_key_survives_database_commit_failure(part4, invitation_mail, monkeypatch):
    created = await _create_invitation(part4)
    token = _token(invitation_mail)
    token_id = decode_token(token)["jti"]
    user_id = created.json()["id"]
    original_commit = part4["db"].commit

    async def fail_commit():
        assert f"invitation:user:{user_id}" not in MemoryRedis.values
        raise RuntimeError("forced commit failure")

    monkeypatch.setattr(part4["db"], "commit", fail_commit)
    with pytest.raises(RuntimeError, match="forced commit failure"):
        await auth.accept_invitation(
            InvitationAccept(token=token, password="Secure-password-2026"),
            part4["db"],
            _request(),
        )
    assert MemoryRedis.values[f"invitation:user:{user_id}"] == token_id

    monkeypatch.setattr(part4["db"], "commit", original_commit)
    await part4["db"].rollback()
    accepted = await part4["client"].post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "password": "Secure-password-2026"},
    )
    assert accepted.status_code == 200


async def test_invitation_checks_user_before_claiming_token(part4, invitation_mail):
    missing_user_id = 999999999
    token = create_token(
        str(missing_user_id),
        "Requester",
        "invitation",
        timedelta(hours=1),
    )
    token_id = decode_token(token)["jti"]
    MemoryRedis.values[f"invitation:user:{missing_user_id}"] = token_id

    response = await part4["client"].post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "password": "Secure-password-2026"},
    )

    assert response.status_code == 400
    assert MemoryRedis.values[f"invitation:user:{missing_user_id}"] == token_id
    assert f"invitation:claim:user:{missing_user_id}" not in MemoryRedis.values


async def test_only_one_simultaneous_acceptance_succeeds(part4, invitation_mail):
    created = await _create_invitation(part4)
    payload = InvitationAccept(token=_token(invitation_mail), password="Secure-password-2026")
    results = await asyncio.gather(
        auth.accept_invitation(payload, part4["db"], _request()),
        auth.accept_invitation(payload, part4["db"], _request()),
        return_exceptions=True,
    )
    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, HTTPException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].status_code == 400
    invited = await part4["db"].get(User, created.json()["id"])
    assert invited is not None and invited.is_active


async def test_successful_login_is_recorded_for_invitation_acceptance_guard(part4, invitation_mail):
    requester = part4["users"]["A_requester"]
    requester.email = f"login-{uuid4().hex}@example.com"
    requester.password_hash = hash_password("Existing-password-2026")
    await part4["db"].flush()
    response = await part4["client"].post(
        "/api/v1/auth/login",
        json={"email": requester.email, "password": "Existing-password-2026"},
    )
    assert response.status_code == 200
    login_audit = await part4["db"].scalar(
        select(AuditLog.id).where(
            AuditLog.entity == "user",
            AuditLog.entity_id == requester.id,
            AuditLog.action == "login",
        )
    )
    assert login_audit is not None


async def test_deactivation_revokes_access_and_refresh_tokens(part4, invitation_mail):
    requester = part4["users"]["A_requester"]
    requester.email = f"requester-{uuid4().hex}@example.com"
    requester.password_hash = hash_password("Existing-password-2026")
    part4["db"].add(
        AuditLog(
            entity="user",
            entity_id=requester.id,
            action="invitation_accepted",
            actor_id=requester.id,
            diff={},
        )
    )
    await part4["db"].flush()
    family = str(uuid4())
    access = create_token(str(requester.id), requester.profile.name, "access", timedelta(minutes=30), family)
    refresh = create_token(str(requester.id), requester.profile.name, "refresh", timedelta(days=14), family)

    act_as(part4, "A_admin")
    deactivated = await part4["client"].patch(
        f"/api/v1/auth/users/{requester.id}",
        json={"is_active": False},
    )
    assert deactivated.status_code == 200
    refresh_response = await part4["client"].post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh},
    )
    assert refresh_response.status_code == 401

    reactivated = await part4["client"].patch(
        f"/api/v1/auth/users/{requester.id}",
        json={"is_active": True},
    )
    assert reactivated.status_code == 200
    still_revoked = await part4["client"].post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh},
    )
    assert still_revoked.status_code == 401
    app_user_override = app.dependency_overrides.pop(current_user, None)
    try:
        access_response = await part4["client"].get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {access}"},
        )
    finally:
        if app_user_override is not None:
            app.dependency_overrides[current_user] = app_user_override
    assert access_response.status_code == 401


async def test_profile_options_and_invite_enforce_rights_ceiling(part4, invitation_mail):
    inviter_profile = Profile(
        name=f"Invite manager {uuid4().hex}",
        interface="helpdesk",
        rights={"users:invite": True, "tickets:read": True},
    )
    lower_profile = Profile(
        name=f"Lower rights {uuid4().hex}",
        interface="helpdesk",
        rights={"tickets:read": True},
    )
    higher_profile = Profile(
        name=f"Higher rights {uuid4().hex}",
        interface="helpdesk",
        rights={"tickets:read": True, "tickets:delete": True},
    )
    wildcard_profile = Profile(
        name=f"Wildcard rights {uuid4().hex}",
        interface="helpdesk",
        rights={"*": True},
    )
    part4["db"].add_all([inviter_profile, lower_profile, higher_profile, wildcard_profile])
    await part4["db"].flush()
    inviter = User(
        email=f"inviter-{uuid4().hex}@example.com",
        password_hash="test-only",
        name="Limited invitation manager",
        profile=inviter_profile,
        entity=part4["entities"]["A"],
    )
    part4["db"].add(inviter)
    await part4["db"].flush()
    part4["state"]["user"] = inviter

    options = await part4["client"].get("/api/v1/auth/invitations/options")
    assert options.status_code == 200
    offered = {profile["id"] for profile in options.json()["profiles"]}
    assert lower_profile.id in offered
    assert higher_profile.id not in offered
    assert wildcard_profile.id not in offered

    low_invite = await part4["client"].post(
        "/api/v1/auth/invitations",
        json={
            "name": "Lower rights user",
            "email": f"lower-{uuid4().hex}@example.com",
            "profile_id": lower_profile.id,
        },
    )
    assert low_invite.status_code == 201
    high_invite = await part4["client"].post(
        "/api/v1/auth/invitations",
        json={
            "name": "Higher rights user",
            "email": f"higher-{uuid4().hex}@example.com",
            "profile_id": higher_profile.id,
        },
    )
    assert high_invite.status_code == 422


async def test_cross_entity_existing_email_returns_generic_invitation_error(part4, invitation_mail):
    part4["users"]["B_tech"].email = f"foreign-{uuid4().hex}@example.com"
    await part4["db"].flush()
    response = await _create_invitation(
        part4,
        email=part4["users"]["B_tech"].email,
        profile_id=part4["users"]["A_requester"].profile_id,
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "This address cannot be invited"
    assert "entity" not in response.json()["detail"].lower()


async def test_invitation_email_domain_allowlist_is_enforced(part4, invitation_mail, monkeypatch):
    _patch_settings(monkeypatch, invite_allowed_email_domains="example.com, allowed.com")
    denied = await _create_invitation(part4, email=f"user@blocked.com")
    assert denied.status_code == 422
    allowed = await _create_invitation(part4, email=f"user-{uuid4().hex}@ALLOWED.COM")
    assert allowed.status_code == 201


async def test_invitation_rate_limit_returns_429(part4, invitation_mail):
    key = f"auth:invite:admin:{part4['users']['A_admin'].id}"
    MemoryRedis.values[key] = "30"
    response = await _create_invitation(part4)
    assert response.status_code == 429


async def test_resend_rate_limit_returns_429(part4, invitation_mail):
    created = await _create_invitation(part4)
    key = f"auth:invite:admin:{part4['users']['A_admin'].id}"
    MemoryRedis.values[key] = "30"
    act_as(part4, "A_admin")
    response = await part4["client"].post(
        f"/api/v1/auth/invitations/{created.json()['id']}/resend"
    )
    assert response.status_code == 429


async def test_accept_rate_limit_returns_429_for_ip_and_token(part4, invitation_mail):
    created = await _create_invitation(part4)
    token = _token(invitation_mail)
    token_id = decode_token(token)["jti"]
    MemoryRedis.values["auth:invite:accept:ip:127.0.0.1"] = "10"
    limited_ip = await part4["client"].post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "password": "Secure-password-2026"},
    )
    assert limited_ip.status_code == 429

    MemoryRedis.values["auth:invite:accept:ip:127.0.0.1"] = "0"
    MemoryRedis.values[f"auth:invite:accept:jti:{token_id}"] = "10"
    limited_token = await part4["client"].post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "password": "Secure-password-2026"},
    )
    assert limited_token.status_code == 429
    assert await part4["db"].get(User, created.json()["id"]) is not None


async def test_invitation_rate_limits_fail_closed_when_redis_is_unavailable(part4, invitation_mail):
    MemoryRedis.fail = True
    invited = await _create_invitation(part4)
    assert invited.status_code == 503
    MemoryRedis.fail = False
    created = await _create_invitation(part4)
    MemoryRedis.fail = True
    accepted = await part4["client"].post(
        "/api/v1/auth/invitations/accept",
        json={"token": _token(invitation_mail), "password": "Secure-password-2026"},
    )
    assert created.status_code == 201
    assert accepted.status_code == 503


async def test_invitation_token_type_is_separate_from_access_and_refresh(part4, invitation_mail):
    token = create_token(
        str(part4["users"]["A_requester"].id),
        "Requester",
        "invitation",
        timedelta(hours=1),
    )
    assert decode_token(token)["type"] == "invitation"
    access = create_token(
        str(part4["users"]["A_requester"].id),
        "Requester",
        "access",
        timedelta(minutes=30),
    )
    refresh = create_token(
        str(part4["users"]["A_requester"].id),
        "Requester",
        "refresh",
        timedelta(days=1),
    )
    for rejected_token in (access, refresh):
        response = await part4["client"].post(
            "/api/v1/auth/invitations/accept",
            json={"token": rejected_token, "password": "Secure-password-2026"},
        )
        assert response.status_code == 400
    for rejected_token in (token, access):
        response = await part4["client"].post(
            "/api/v1/auth/refresh",
            json={"refresh_token": rejected_token},
        )
        assert response.status_code == 401
    user_override = app.dependency_overrides.pop(current_user, None)
    try:
        for rejected_token in (token, refresh):
            response = await part4["client"].get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {rejected_token}"},
            )
            assert response.status_code == 401
    finally:
        if user_override is not None:
            app.dependency_overrides[current_user] = user_override


@pytest.mark.parametrize(
    "password,email,expected_message",
    [
        ("shortpass", "user@example.com", "Password must contain at least 10 characters"),
        ("longpassword", "longpassword@example.com", "Password must not match the email username"),
        ("😀" * 19, "user@example.com", "Password must not exceed 72 UTF-8 bytes"),
    ],
)
async def test_invitation_accept_password_policy(part4, invitation_mail, password, email, expected_message):
    created = await _create_invitation(part4, email=email)
    assert created.status_code == 201
    response = await part4["client"].post(
        "/api/v1/auth/invitations/accept",
        json={"token": _token(invitation_mail), "password": password},
    )
    assert response.status_code == 422
    assert expected_message in response.text


async def test_invitation_accept_allows_a_ten_character_password(part4, invitation_mail):
    created = await _create_invitation(part4)
    response = await part4["client"].post(
        "/api/v1/auth/invitations/accept",
        json={"token": _token(invitation_mail), "password": "Secure1234"},
    )
    assert created.status_code == 201
    assert response.status_code == 200


async def test_user_list_distinguishes_pending_active_and_deactivated(part4, invitation_mail):
    pending = await _create_invitation(part4)
    pending_rows = (await part4["client"].get("/api/v1/auth/users")).json()
    assert next(row for row in pending_rows if row["id"] == pending.json()["id"])["status"] == "Pending"
    pending_token = _token(invitation_mail)
    accepted = await part4["client"].post(
        "/api/v1/auth/invitations/accept",
        json={"token": pending_token, "password": "Secure-password-2026"},
    )
    assert accepted.status_code == 200
    active_id = pending.json()["id"]

    deactivated_invite = await _create_invitation(part4)
    act_as(part4, "A_admin")
    await part4["client"].patch(
        f"/api/v1/auth/users/{deactivated_invite.json()['id']}",
        json={"is_active": False},
    )
    rows = (await part4["client"].get("/api/v1/auth/users")).json()
    statuses = {row["id"]: row["status"] for row in rows}
    assert statuses[pending.json()["id"]] == "Active"
    assert statuses[deactivated_invite.json()["id"]] == "Deactivated"
    assert statuses[part4["users"]["A_tech"].id] == "Active"


async def test_invitation_accept_allows_password_at_bcrypt_byte_limit(part4, invitation_mail):
    created = await _create_invitation(part4)
    password = "😀" * 18

    accepted = await part4["client"].post(
        "/api/v1/auth/invitations/accept",
        json={"token": _token(invitation_mail), "password": password},
    )

    assert len(password.encode("utf-8")) == 72
    assert accepted.status_code == 200
