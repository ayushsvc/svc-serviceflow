import asyncio
import logging
from uuid import uuid4

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.deps import current_user, get_db
from app.main import app
from app.models import (
    AuditLog,
    Entity,
    Group,
    GroupMember,
    Profile,
    Ticket,
    TicketActor,
    TicketDocument,
    TicketFollowup,
    User,
)
from app.seed import seed_profile_rights

ADMIN_RIGHTS = {
    "*": True,
    "ticket:view_all": True,
    "ticket:assign_any": True,
    "ticket:bulk_edit": True,
    "ticket:self_claim": True,
    "ticket:view_unassigned": True,
    "tickets:update": True,
}
# Seeded Technician defaults after Part 3: queue work, no self-claim, no unassigned tickets.
TECHNICIAN_RIGHTS = {
    "ticket:view_queue": True,
    "ticket:bulk_edit": True,
    "tickets:update": True,
    "ticket:self_claim": False,
    "ticket:view_unassigned": False,
}
CLAIMING_RIGHTS = {**TECHNICIAN_RIGHTS, "ticket:self_claim": True, "ticket:view_unassigned": True}
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 13 + b"\x00" * 4 + b"\x00\x00\x00\x00IEND\xaeB`\x82"


@pytest.fixture
def notifications(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr("app.services.bulk_tickets.send_ticket_notification.delay", lambda *a, **k: calls.append(("ticket", *a)))
    monkeypatch.setattr("app.services.bulk_tickets.send_user_ticket_notification.delay", lambda *a, **k: calls.append(("user", *a)))
    monkeypatch.setattr("app.api.tickets.send_ticket_notification.delay", lambda *a, **k: calls.append(("ticket", *a)))
    return calls


@pytest.fixture
async def part3_http(monkeypatch, tmp_path):
    monkeypatch.setattr("app.api.attachments.UPLOAD_ROOT", tmp_path)
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        db = AsyncSession(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        suffix = uuid4().hex
        profiles = {
            "requester": Profile(name=f"Part3 requester {suffix}", interface="self_service", rights={"ticket:own_only": True}),
            "admin": Profile(name=f"Part3 admin {suffix}", interface="helpdesk", rights=ADMIN_RIGHTS),
            "tech": Profile(name=f"Part3 technician {suffix}", interface="helpdesk", rights=TECHNICIAN_RIGHTS),
            "claimer": Profile(name=f"Part3 claimer {suffix}", interface="helpdesk", rights=CLAIMING_RIGHTS),
        }
        entities = {key: Entity(name=f"Part3 {key} {suffix}") for key in ("A", "B")}
        db.add_all([*profiles.values(), *entities.values()])
        await db.flush()
        users: dict[str, User] = {}

        def add_user(key: str, entity: str, profile: str, *, active: bool = True) -> None:
            users[key] = User(
                email=f"part3-{key.lower()}-{suffix}@example.test",
                password_hash="test-only",
                name=f"Name {key}",
                profile=profiles[profile],
                entity=entities[entity],
                is_active=active,
            )
            db.add(users[key])

        add_user("A_requester", "A", "requester")
        add_user("A_admin", "A", "admin")
        add_user("A_tech", "A", "tech")
        add_user("A_claimer", "A", "claimer")
        add_user("A_inactive", "A", "tech", active=False)
        add_user("B_requester", "B", "requester")
        add_user("B_admin", "B", "admin")
        add_user("B_tech", "B", "tech")
        await db.flush()
        state = {"user": users["A_admin"]}

        async def override_user() -> User:
            return state["user"]

        async def override_db():
            yield db

        app.dependency_overrides[current_user] = override_user
        app.dependency_overrides[get_db] = override_db
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                yield {"client": client, "db": db, "entities": entities, "users": users, "state": state, "root": tmp_path}
        finally:
            app.dependency_overrides.pop(current_user, None)
            app.dependency_overrides.pop(get_db, None)
            await db.close()
            if transaction.is_active:
                await transaction.rollback()
            await engine.dispose()


def act_as(context, key: str) -> None:
    context["state"]["user"] = context["users"][key]


async def make_ticket(context, *, entity: str = "A", assignee: str | None = None, status: str | None = None, title: str | None = None) -> int:
    ticket = Ticket(
        title=title or f"Part 3 ticket {uuid4().hex[:8]}",
        description="<p>The printer on floor two is not working.</p>",
        type="INCIDENT",
        status=status or ("ASSIGNED" if assignee else "NEW"),
        urgency=3,
        impact=3,
        priority=3,
        entity_id=context["entities"][entity].id,
        requester_id=context["users"][f"{entity}_requester"].id,
        assignee_id=context["users"][assignee].id if assignee else None,
    )
    context["db"].add(ticket)
    await context["db"].flush()
    return ticket.id


async def listed_ids(context, user: str, query: str = "") -> set[int]:
    act_as(context, user)
    response = await context["client"].get(f"/api/v1/tickets?page_size=100{query}")
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


async def assign(context, ticket_id: int, user: str, assignee: str | None):
    act_as(context, user)
    return await context["client"].post(
        f"/api/v1/tickets/{ticket_id}/assign",
        json={"assignee_id": context["users"][assignee].id if assignee else None},
    )


async def ticket_audit(context, ticket_id: int, action: str) -> list[AuditLog]:
    rows = await context["db"].scalars(
        select(AuditLog).where(AuditLog.entity == "ticket", AuditLog.entity_id == ticket_id, AuditLog.action == action).order_by(AuditLog.id)
    )
    return list(rows.all())


@pytest.mark.asyncio
async def test_unassigned_tab_excludes_assigned_tickets_and_includes_new_ones(part3_http) -> None:
    fresh = await make_ticket(part3_http)
    assigned = await make_ticket(part3_http, assignee="A_tech")
    other_entity = await make_ticket(part3_http, entity="B")
    unassigned = await listed_ids(part3_http, "A_admin", "&queue=unassigned")
    assert fresh in unassigned
    assert assigned not in unassigned
    assert other_entity not in unassigned


@pytest.mark.asyncio
async def test_unassigned_excludes_tickets_routed_to_a_team(part3_http) -> None:
    team = Group(name=f"Part3 team {uuid4().hex[:6]}", entity_id=part3_http["entities"]["A"].id)
    part3_http["db"].add(team)
    await part3_http["db"].flush()
    routed = await make_ticket(part3_http)
    part3_http["db"].add(TicketActor(ticket_id=routed, actor_type="GROUP", actor_id=team.id, role="ASSIGNEE"))
    await part3_http["db"].flush()
    free = await make_ticket(part3_http)
    unassigned = await listed_ids(part3_http, "A_admin", "&queue=unassigned")
    assert free in unassigned
    assert routed not in unassigned


@pytest.mark.asyncio
async def test_assigning_moves_ticket_out_of_unassigned_into_the_technician_list_and_all_tickets_shows_both(part3_http) -> None:
    moving = await make_ticket(part3_http)
    staying = await make_ticket(part3_http)
    response = await assign(part3_http, moving, "A_admin", "A_tech")
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert response.json()["ticket"]["assignee_name"] == "Name A_tech"

    unassigned = await listed_ids(part3_http, "A_admin", "&queue=unassigned")
    assert moving not in unassigned and staying in unassigned
    everything = await listed_ids(part3_http, "A_admin")
    assert {moving, staying} <= everything
    assert moving in await listed_ids(part3_http, "A_tech", "&queue=mine")
    assert moving in await listed_ids(part3_http, "A_tech")


@pytest.mark.asyncio
async def test_tab_counts_and_team_counts_use_the_same_unassigned_predicate(part3_http) -> None:
    await make_ticket(part3_http)
    await make_ticket(part3_http)
    await make_ticket(part3_http, assignee="A_tech")
    act_as(part3_http, "A_admin")
    client = part3_http["client"]
    counts = (await client.get("/api/v1/tickets/queue-counts")).json()
    team_counts = (await client.get("/api/v1/tickets/team-counts")).json()
    listed = await listed_ids(part3_http, "A_admin", "&queue=unassigned")
    team_unassigned = next(row["count"] for row in team_counts if row["team_id"] is None)
    assert counts["unassigned"] == team_unassigned == len(listed) == 2
    assert counts["all"] == 3


@pytest.mark.asyncio
async def test_technician_without_view_unassigned_sees_only_own_and_team_tickets(part3_http) -> None:
    free = await make_ticket(part3_http)
    mine = await make_ticket(part3_http, assignee="A_tech")
    someone_elses = await make_ticket(part3_http, assignee="A_claimer")
    visible = await listed_ids(part3_http, "A_tech")
    assert visible == {mine}
    assert free not in await listed_ids(part3_http, "A_tech", "&queue=unassigned")
    assert someone_elses not in visible
    counts = (await part3_http["client"].get("/api/v1/tickets/queue-counts")).json()
    assert counts["unassigned"] == 0
    act_as(part3_http, "A_tech")
    assert (await part3_http["client"].get(f"/api/v1/tickets/{free}")).status_code == 404
    assert free in await listed_ids(part3_http, "A_claimer")


@pytest.mark.asyncio
async def test_assignable_users_are_entity_scoped_and_exclude_inactive_users(part3_http) -> None:
    act_as(part3_http, "A_admin")
    response = await part3_http["client"].get("/api/v1/users/assignable?limit=50")
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    users = part3_http["users"]
    assert users["A_tech"].id in ids and users["A_claimer"].id in ids
    assert users["A_inactive"].id not in ids
    assert users["B_tech"].id not in ids and users["B_admin"].id not in ids
    assert users["A_requester"].id not in ids


@pytest.mark.asyncio
async def test_requester_cannot_assign_and_foreign_entity_gets_404(part3_http) -> None:
    ticket_id = await make_ticket(part3_http)
    denied = await assign(part3_http, ticket_id, "A_requester", "A_tech")
    assert denied.status_code == 403
    foreign = await assign(part3_http, ticket_id, "B_admin", "B_tech")
    assert foreign.status_code == 404
    act_as(part3_http, "A_requester")
    assert (await part3_http["client"].get("/api/v1/users/assignable")).status_code == 403
    assert (await part3_http["db"].get(Ticket, ticket_id)).assignee_id is None


@pytest.mark.asyncio
async def test_self_claim_is_denied_by_default_and_allowed_with_the_right(part3_http) -> None:
    ticket_id = await make_ticket(part3_http)
    act_as(part3_http, "A_tech")
    denied = await part3_http["client"].post(f"/api/v1/tickets/{ticket_id}/claim")
    assert denied.status_code == 403
    act_as(part3_http, "A_claimer")
    allowed = await part3_http["client"].post(f"/api/v1/tickets/{ticket_id}/claim")
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["assignee_id"] == part3_http["users"]["A_claimer"].id
    act_as(part3_http, "A_admin")
    second = await make_ticket(part3_http)
    assert (await part3_http["client"].post(f"/api/v1/tickets/{second}/claim")).status_code == 200


@pytest.mark.asyncio
async def test_claim_no_longer_depends_on_the_legacy_ticket_claim_right(part3_http) -> None:
    legacy = part3_http["users"]["A_tech"].profile
    legacy.rights = {**TECHNICIAN_RIGHTS, "ticket:claim": True}
    ticket_id = await make_ticket(part3_http)
    act_as(part3_http, "A_tech")
    assert (await part3_http["client"].post(f"/api/v1/tickets/{ticket_id}/claim")).status_code == 403
    claimer = part3_http["users"]["A_claimer"].profile
    claimer.rights = {key: value for key, value in CLAIMING_RIGHTS.items()}
    assert "ticket:claim" not in claimer.rights
    act_as(part3_http, "A_claimer")
    assert (await part3_http["client"].post(f"/api/v1/tickets/{ticket_id}/claim")).status_code == 200


@pytest.mark.asyncio
async def test_reassigning_the_same_user_is_unchanged_without_audit_or_email(part3_http, notifications) -> None:
    ticket_id = await make_ticket(part3_http)
    first = await assign(part3_http, ticket_id, "A_admin", "A_tech")
    assert first.status_code == 200 and first.json()["reason"] is None
    audits = len(await ticket_audit(part3_http, ticket_id, "assigned"))
    sent = len(notifications)
    assert audits == 1 and sent > 0
    again = await assign(part3_http, ticket_id, "A_admin", "A_tech")
    assert again.status_code == 200
    assert again.json()["reason"] == "unchanged"
    assert len(await ticket_audit(part3_http, ticket_id, "assigned")) == audits
    assert len(notifications) == sent


@pytest.mark.asyncio
async def test_unassign_is_allowed_only_while_assigned_otherwise_409(part3_http) -> None:
    open_ticket = await make_ticket(part3_http, assignee="A_tech", status="ASSIGNED")
    ok = await assign(part3_http, open_ticket, "A_admin", None)
    assert ok.status_code == 200, ok.text
    assert ok.json()["ticket"]["assignee_id"] is None
    assert ok.json()["ticket"]["status"] == "NEW"

    working = await make_ticket(part3_http, assignee="A_tech", status="PLANNED")
    blocked = await assign(part3_http, working, "A_admin", None)
    assert blocked.status_code == 409
    assert (await part3_http["db"].get(Ticket, working)).assignee_id == part3_http["users"]["A_tech"].id


@pytest.fixture
async def committed_part3(monkeypatch):
    monkeypatch.setattr("app.services.bulk_tickets.send_ticket_notification.delay", lambda *a, **k: None)
    monkeypatch.setattr("app.services.bulk_tickets.send_user_ticket_notification.delay", lambda *a, **k: None)
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    suffix = uuid4().hex
    async with AsyncSession(engine, expire_on_commit=False) as db:
        admin_profile = Profile(name=f"Part3 c-admin {suffix}", interface="helpdesk", rights=ADMIN_RIGHTS)
        claimer_profile = Profile(name=f"Part3 c-claimer {suffix}", interface="helpdesk", rights=CLAIMING_RIGHTS)
        entity = Entity(name=f"Part3 concurrent {suffix}")
        db.add_all([admin_profile, claimer_profile, entity])
        await db.flush()
        users = {
            key: User(
                email=f"part3-c-{key}-{suffix}@example.test",
                password_hash="test-only",
                name=f"Concurrent {key}",
                profile=profile,
                entity=entity,
            )
            for key, profile in (("admin", admin_profile), ("one", claimer_profile), ("two", claimer_profile))
        }
        db.add_all(users.values())
        await db.flush()
        ticket = Ticket(
            title=f"Concurrent {suffix}",
            description="<p>A ticket used to test concurrent assignment.</p>",
            type="INCIDENT",
            status="NEW",
            urgency=3,
            impact=3,
            priority=3,
            entity_id=entity.id,
            requester_id=users["admin"].id,
        )
        db.add(ticket)
        await db.commit()
        ids = {key: user.id for key, user in users.items()}
        ids.update({"entity": entity.id, "ticket": ticket.id, "profiles": [admin_profile.id, claimer_profile.id]})
    loaded: dict[int, User] = {}
    async with AsyncSession(engine, expire_on_commit=False) as db:
        for user in (await db.scalars(select(User).options(selectinload(User.profile)).where(User.entity_id == ids["entity"]))).all():
            loaded[user.id] = user

    async def override_user(request: Request) -> User:
        return loaded[int(request.headers["x-test-user"])]

    async def override_db():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[current_user] = override_user
    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            yield {"client": client, "engine": engine, "ids": ids}
    finally:
        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(get_db, None)
        async with AsyncSession(engine) as db:
            user_ids = [ids["admin"], ids["one"], ids["two"]]
            await db.execute(delete(TicketFollowup).where(TicketFollowup.ticket_id == ids["ticket"]))
            await db.execute(delete(TicketActor).where(TicketActor.ticket_id == ids["ticket"]))
            await db.execute(delete(AuditLog).where(AuditLog.entity == "ticket", AuditLog.entity_id == ids["ticket"]))
            await db.execute(delete(Ticket).where(Ticket.id == ids["ticket"]))
            await db.execute(delete(AuditLog).where(AuditLog.actor_id.in_(user_ids)))
            await db.execute(delete(GroupMember).where(GroupMember.user_id.in_(user_ids)))
            await db.execute(delete(User).where(User.id.in_(user_ids)))
            await db.execute(delete(Entity).where(Entity.id == ids["entity"]))
            await db.execute(delete(Profile).where(Profile.id.in_(ids["profiles"])))
            await db.commit()
        await engine.dispose()


async def assert_single_consistent_assignee(context, allowed: set[int]) -> None:
    ticket_id = context["ids"]["ticket"]
    async with AsyncSession(context["engine"]) as db:
        ticket = await db.get(Ticket, ticket_id)
        audits = list(
            (await db.scalars(select(AuditLog).where(AuditLog.entity == "ticket", AuditLog.entity_id == ticket_id).order_by(AuditLog.id))).all()
        )
    assert ticket.assignee_id in allowed
    previous = None
    for entry in audits:
        change = entry.diff.get("assignee_id")
        if not isinstance(change, dict):
            continue
        assert change["old"] == previous, "assignment history must be a single chain"
        previous = change["new"]
    assert previous == ticket.assignee_id


@pytest.mark.asyncio
async def test_concurrent_assign_and_assign_never_double_assign(committed_part3) -> None:
    ids = committed_part3["ids"]
    client = committed_part3["client"]

    def post(target: str):
        return client.post(
            f"/api/v1/tickets/{ids['ticket']}/assign",
            json={"assignee_id": ids[target]},
            headers={"x-test-user": str(ids["admin"])},
        )

    responses = await asyncio.wait_for(asyncio.gather(post("one"), post("two")), timeout=30)
    assert all(response.status_code == 200 for response in responses), [r.text for r in responses]
    await assert_single_consistent_assignee(committed_part3, {ids["one"], ids["two"]})


@pytest.mark.asyncio
async def test_concurrent_assign_and_claim_never_double_assign(committed_part3) -> None:
    ids = committed_part3["ids"]
    client = committed_part3["client"]
    assign_call = client.post(
        f"/api/v1/tickets/{ids['ticket']}/assign",
        json={"assignee_id": ids["one"]},
        headers={"x-test-user": str(ids["admin"])},
    )
    claim_call = client.post(f"/api/v1/tickets/{ids['ticket']}/claim", headers={"x-test-user": str(ids["two"])})
    assigned, claimed = await asyncio.wait_for(asyncio.gather(assign_call, claim_call), timeout=30)
    assert assigned.status_code == 200, assigned.text
    assert claimed.status_code in {200, 409}, claimed.text
    await assert_single_consistent_assignee(committed_part3, {ids["one"], ids["two"]})
    if claimed.status_code == 409:
        async with AsyncSession(committed_part3["engine"]) as db:
            assert (await db.get(Ticket, ids["ticket"])).assignee_id == ids["one"]


@pytest.mark.asyncio
async def test_detail_shows_names_to_the_right_roles_and_hides_internal_fields_from_requesters(part3_http) -> None:
    team = Group(name=f"Part3 detail team {uuid4().hex[:6]}", entity_id=part3_http["entities"]["A"].id)
    part3_http["db"].add(team)
    await part3_http["db"].flush()
    ticket_id = await make_ticket(part3_http, assignee="A_tech")
    part3_http["db"].add_all(
        [
            TicketActor(ticket_id=ticket_id, actor_type="GROUP", actor_id=team.id, role="ASSIGNEE"),
            GroupMember(group_id=team.id, user_id=part3_http["users"]["A_tech"].id),
        ]
    )
    await part3_http["db"].flush()

    for viewer in ("A_admin", "A_tech"):
        act_as(part3_http, viewer)
        detail = (await part3_http["client"].get(f"/api/v1/tickets/{ticket_id}")).json()
        assert detail["requester_name"] == "Name A_requester"
        assert detail["assignee_name"] == "Name A_tech"
        assert detail["team_name"] == team.name
        assert "The printer on floor two" in detail["description"]
        assert detail["opened_at"]

    act_as(part3_http, "A_requester")
    detail = (await part3_http["client"].get(f"/api/v1/tickets/{ticket_id}")).json()
    assert detail["requester_name"] == "Name A_requester"
    assert detail["assignee_name"] == "Name A_tech"
    for hidden in ("team_id", "team_name", "flagged", "requester_vip", "requester_active", "sla_id"):
        assert detail.get(hidden) is None, hidden

    unassigned = await make_ticket(part3_http)
    detail = (await part3_http["client"].get(f"/api/v1/tickets/{unassigned}")).json()
    assert detail["assignee_name"] is None and detail["assignee_id"] is None

    act_as(part3_http, "B_requester")
    assert (await part3_http["client"].get(f"/api/v1/tickets/{ticket_id}")).status_code == 404
    act_as(part3_http, "B_admin")
    assert (await part3_http["client"].get(f"/api/v1/tickets/{ticket_id}")).status_code == 404


@pytest.mark.asyncio
async def test_detail_attachment_metadata_comes_from_the_attachments_api_and_is_entity_isolated(part3_http) -> None:
    ticket_id = await make_ticket(part3_http, assignee="A_tech")
    act_as(part3_http, "A_requester")
    uploaded = await part3_http["client"].post(
        f"/api/v1/tickets/{ticket_id}/documents", files={"document": ("shot.png", PNG, "image/png")}
    )
    assert uploaded.status_code == 201, uploaded.text
    part3_http["db"].add(
        TicketDocument(ticket_id=ticket_id, filename="lost.txt", path=str(part3_http["root"] / "gone.txt"), uploaded_by=part3_http["users"]["A_requester"].id)
    )
    await part3_http["db"].flush()
    for viewer in ("A_admin", "A_tech", "A_requester"):
        act_as(part3_http, viewer)
        listing = await part3_http["client"].get(f"/api/v1/tickets/{ticket_id}/documents")
        assert listing.status_code == 200, listing.text
        items = {item["filename"]: item for item in listing.json()}
        shot = items["shot.png"]
        assert shot["content_type"] == "image/png" and shot["size_bytes"] == len(PNG)
        assert shot["uploaded_by_name"] == "Name A_requester" and shot["previewable"] is True
        assert shot["created_at"] and shot["file_missing"] is False
        assert items["lost.txt"]["file_missing"] is True
    act_as(part3_http, "B_admin")
    assert (await part3_http["client"].get(f"/api/v1/tickets/{ticket_id}/documents")).status_code == 404


@pytest.mark.asyncio
async def test_private_sourced_attachment_stays_staff_only_in_ticket_views(part3_http) -> None:
    ticket_id = await make_ticket(part3_http, assignee="A_tech")
    act_as(part3_http, "A_tech")
    followup = await part3_http["client"].post(
        f"/api/v1/tickets/{ticket_id}/followups", json={"body": "<p>Internal only</p>", "is_private": True}
    )
    assert followup.status_code in {200, 201}, followup.text
    uploaded = await part3_http["client"].post(
        f"/api/v1/tickets/{ticket_id}/documents",
        files={"document": ("private.png", PNG, "image/png")},
        data={"followup_id": str(followup.json()["id"])},
    )
    assert uploaded.status_code == 201, uploaded.text
    act_as(part3_http, "A_requester")
    names = {item["filename"] for item in (await part3_http["client"].get(f"/api/v1/tickets/{ticket_id}/documents")).json()}
    assert "private.png" not in names
    act_as(part3_http, "A_admin")
    names = {item["filename"] for item in (await part3_http["client"].get(f"/api/v1/tickets/{ticket_id}/documents")).json()}
    assert "private.png" in names


@pytest.mark.asyncio
async def test_description_with_script_onerror_and_javascript_url_is_sanitised_in_every_response(part3_http, notifications) -> None:
    payload = (
        "<p>Printer broken today</p><script>alert(1)</script>"
        '<img src=x onerror="alert(2)"><a href="javascript:alert(3)">click</a>'
        '<a href="https://example.com/ok" onclick="alert(4)">ok</a>'
    )
    act_as(part3_http, "A_requester")
    created = await part3_http["client"].post("/api/v1/tickets", json={"title": "XSS check", "description": payload})
    assert created.status_code == 201, created.text
    ticket_id = created.json()["id"]
    legacy_id = await make_ticket(part3_http)
    legacy = await part3_http["db"].get(Ticket, legacy_id)
    legacy.description = payload
    await part3_http["db"].flush()

    for target in (ticket_id, legacy_id):
        for viewer in ("A_requester", "A_admin"):
            act_as(part3_http, viewer)
            if viewer == "A_requester" and target == legacy_id:
                continue
            body = (await part3_http["client"].get(f"/api/v1/tickets/{target}")).text.lower()
            for forbidden in ("<script", "onerror", "onclick", "javascript:", "alert(1)", "<img"):
                assert forbidden not in body, (target, viewer, forbidden)
            assert "printer broken today" in body or target == legacy_id
    act_as(part3_http, "A_admin")
    listing = (await part3_http["client"].get("/api/v1/tickets?page_size=100")).text.lower()
    assert "<script" not in listing and "onerror" not in listing and "javascript:" not in listing


@pytest.mark.asyncio
async def test_listing_attachments_with_missing_files_does_not_log_warnings(part3_http, caplog) -> None:
    ticket_id = await make_ticket(part3_http)
    part3_http["db"].add(
        TicketDocument(ticket_id=ticket_id, filename="gone.txt", path=str(part3_http["root"] / "gone.txt"), uploaded_by=part3_http["users"]["A_requester"].id)
    )
    await part3_http["db"].flush()
    act_as(part3_http, "A_admin")
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            assert (await part3_http["client"].get(f"/api/v1/tickets/{ticket_id}/documents")).status_code == 200
    assert [record for record in caplog.records if record.levelno >= logging.WARNING] == []


@pytest.mark.asyncio
async def test_seed_adds_self_claim_and_view_unassigned_additively_and_never_overwrites() -> None:
    profiles = {
        "Requester": Profile(name="Requester", rights={"ticket:own_only": True}),
        "Technician": Profile(name="Technician", rights={"ticket:view_queue": True, "ticket:claim": True, "ticket:self_claim": True}),
        "Administrator": Profile(name="Administrator", rights={"*": True, "ticket:view_unassigned": False}),
    }
    await seed_profile_rights(profiles)
    assert profiles["Technician"].rights["ticket:self_claim"] is True
    assert profiles["Technician"].rights["ticket:view_unassigned"] is False
    assert profiles["Technician"].rights["ticket:claim"] is True
    assert profiles["Administrator"].rights["ticket:view_unassigned"] is False
    assert profiles["Administrator"].rights["ticket:self_claim"] is True
    assert "ticket:self_claim" not in profiles["Requester"].rights
    fresh = {name: Profile(name=name, rights={}) for name in ("Requester", "Technician", "Administrator")}
    await seed_profile_rights(fresh)
    assert fresh["Technician"].rights["ticket:self_claim"] is False
    assert fresh["Technician"].rights["ticket:view_unassigned"] is False
    again = dict(fresh["Technician"].rights)
    assert await seed_profile_rights(fresh) == []
    assert fresh["Technician"].rights == again
