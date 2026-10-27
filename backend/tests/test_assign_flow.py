import asyncio
import json
from uuid import uuid4

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
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
    NotificationTemplate,
    Profile,
    Ticket,
    TicketActor,
    TicketFollowup,
    User,
    WorkflowApproval,
    TicketWorkflow,
)


@pytest.fixture
async def assign_http():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    suffix = uuid4().hex
    async with AsyncSession(engine, expire_on_commit=False) as db:
        profiles = {
            profile.name: profile
            for profile in (
                await db.scalars(
                    select(Profile).where(
                        Profile.name.in_(("Administrator", "Technician", "Requester"))
                    )
                )
            ).all()
        }
        # The seeded Technician no longer self-claims or sees unassigned tickets, so this fixture
        # uses a copy of the seeded rights with the two new rights granted explicitly.
        technician_profile = Profile(
            name=f"Assignment technician {suffix}",
            interface=profiles["Technician"].interface,
            rights={**(profiles["Technician"].rights or {}), "ticket:self_claim": True, "ticket:view_unassigned": True},
        )
        db.add(technician_profile)
        profiles["Technician"] = technician_profile
        entities = {
            key: Entity(name=f"Assignment {key} {suffix}")
            for key in ("A", "B")
        }
        db.add_all(entities.values())
        await db.flush()
        db.add_all(
            NotificationTemplate(
                entity_id=entity.id,
                event="assigned",
                subject="Assignment test notification",
                body_jinja="Ticket {{ ticket.id }} assigned to {{ user.name }}.",
            )
            for entity in entities.values()
        )
        users = {
            f"{entity_key}_{role.lower()}": User(
                email=f"assign-{entity_key}-{role.lower()}-{suffix}@example.test",
                password_hash="test-only",
                name=f"{entity_key} {role}",
                profile=profiles[role],
                entity=entities[entity_key],
            )
            for entity_key in ("A", "B")
            for role in ("Administrator", "Technician", "Requester")
        }
        users["A_outside_technician"] = User(
            email=f"assign-a-outside-technician-{suffix}@example.test",
            password_hash="test-only",
            name="A outside Technician",
            profile=profiles["Technician"],
            entity=entities["A"],
        )
        db.add_all(users.values())
        await db.flush()
        team_a = Group(name=f"Assignment team A {suffix}", entity_id=entities["A"].id)
        team_b = Group(name=f"Assignment team B {suffix}", entity_id=entities["B"].id)
        db.add_all((team_a, team_b))
        await db.flush()
        db.add_all(
            (
                GroupMember(group_id=team_a.id, user_id=users["A_technician"].id),
                GroupMember(group_id=team_a.id, user_id=users["A_administrator"].id),
                GroupMember(group_id=team_b.id, user_id=users["B_technician"].id),
            )
        )
        await db.commit()
        ids = {key: user.id for key, user in users.items()}
        entity_ids = [entity.id for entity in entities.values()]
        group_ids = [team_a.id, team_b.id]

    user_by_id: dict[int, User] = {}
    async with AsyncSession(engine, expire_on_commit=False) as db:
        user_by_id.update(
            {
                user.id: user
                for user in (
                    await db.scalars(
                        select(User)
                        .options(selectinload(User.profile))
                        .where(User.id.in_(list(ids.values())))
                    )
                ).all()
            }
        )

    async def user_override(request: Request) -> User:
        return user_by_id[int(request.headers["x-test-user"])]

    async def db_override():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[current_user] = user_override
    app.dependency_overrides[get_db] = db_override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            yield {
                "client": client,
                "engine": engine,
                "suffix": suffix,
                "ids": ids,
                "entity_ids": entity_ids,
                "group_ids": group_ids,
                "entities": entities,
                "teams": {"A": team_a, "B": team_b},
            }
    finally:
        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(get_db, None)
        async with AsyncSession(engine) as db:
            ticket_ids = list(
                (
                    await db.scalars(select(Ticket.id).where(Ticket.entity_id.in_(entity_ids)))
                ).all()
            )
            if ticket_ids:
                await db.execute(delete(WorkflowApproval).where(WorkflowApproval.record_id.in_(ticket_ids)))
                await db.execute(delete(TicketActor).where(TicketActor.ticket_id.in_(ticket_ids)))
                await db.execute(delete(TicketFollowup).where(TicketFollowup.ticket_id.in_(ticket_ids)))
                await db.execute(
                    delete(AuditLog).where(AuditLog.entity == "ticket", AuditLog.entity_id.in_(ticket_ids))
                )
                await db.execute(delete(Ticket).where(Ticket.id.in_(ticket_ids)))
            await db.execute(delete(TicketWorkflow).where(TicketWorkflow.entity_id.in_(entity_ids)))
            await db.execute(delete(AuditLog).where(AuditLog.actor_id.in_(list(ids.values()))))
            await db.execute(delete(NotificationTemplate).where(NotificationTemplate.entity_id.in_(entity_ids)))
            await db.execute(delete(GroupMember).where(GroupMember.group_id.in_(group_ids)))
            await db.execute(delete(User).where(User.id.in_(list(ids.values()))))
            await db.execute(delete(Group).where(Group.id.in_(group_ids)))
            await db.execute(delete(Entity).where(Entity.id.in_(entity_ids)))
            await db.execute(delete(Profile).where(Profile.name == f"Assignment technician {suffix}"))
            await db.commit()
        await engine.dispose()


async def create_ticket(assign_http, *, entity_key: str = "A", routed_team_id: int | None = None) -> int:
    async with AsyncSession(assign_http["engine"], expire_on_commit=False) as db:
        ticket = Ticket(
            title=f"Assignment test {uuid4().hex}",
            description="A real database assignment flow test ticket.",
            type="INCIDENT",
            status="NEW",
            urgency=3,
            impact=3,
            priority=3,
            entity_id=assign_http["entities"][entity_key].id,
            requester_id=assign_http["ids"][f"{entity_key}_requester"],
        )
        db.add(ticket)
        await db.flush()
        if routed_team_id is not None:
            db.add(TicketActor(
                ticket_id=ticket.id,
                actor_type="GROUP",
                actor_id=routed_team_id,
                role="ASSIGNEE",
            ))
        await db.commit()
        return ticket.id


async def mailhog_count_for(email: str) -> int:
    async with AsyncClient(base_url="http://mailhog:8025", timeout=2) as mailhog:
        response = await mailhog.get("/api/v2/messages", params={"limit": 50})
        response.raise_for_status()
        messages = response.json().get("items", [])
        return sum(email.lower() in json.dumps(message).lower() for message in messages)


def headers(assign_http, user_key: str) -> dict[str, str]:
    return {"x-test-user": str(assign_http["ids"][user_key])}


@pytest.mark.asyncio
async def test_administrator_assigns_ticket_and_writes_timeline_and_audit(assign_http) -> None:
    ticket_id = await create_ticket(assign_http)
    response = await assign_http["client"].post(
        f"/api/v1/tickets/{ticket_id}/assign",
        headers=headers(assign_http, "A_administrator"),
        json={"assignee_id": assign_http["ids"]["A_technician"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["ticket"]["status"] == "ASSIGNED"
    assert response.json()["ticket"]["assignee_id"] == assign_http["ids"]["A_technician"]

    async with AsyncSession(assign_http["engine"]) as db:
        assert await db.scalar(select(TicketFollowup.id).where(TicketFollowup.ticket_id == ticket_id))
        assert await db.scalar(
            select(AuditLog.id).where(
                AuditLog.entity == "ticket",
                AuditLog.entity_id == ticket_id,
                AuditLog.action == "assigned",
                AuditLog.actor_id == assign_http["ids"]["A_administrator"],
            )
        )
    technician_email = f"assign-a-technician-{assign_http['suffix']}@example.test"
    for _ in range(30):
        if await mailhog_count_for(technician_email):
            break
        await asyncio.sleep(0.2)
    assert await mailhog_count_for(technician_email) >= 1


@pytest.mark.asyncio
async def test_reassign_unassign_and_same_assignee_is_idempotent(assign_http) -> None:
    ticket_id = await create_ticket(assign_http)
    client = assign_http["client"]
    admin_headers = headers(assign_http, "A_administrator")
    technician_id = assign_http["ids"]["A_technician"]
    first = await client.post(
        f"/api/v1/tickets/{ticket_id}/assign",
        headers=admin_headers,
        json={"assignee_id": technician_id},
    )
    assert first.status_code == 200, first.text
    technician_email = f"assign-a-technician-{assign_http['suffix']}@example.test"
    for _ in range(30):
        if await mailhog_count_for(technician_email):
            break
        await asyncio.sleep(0.2)
    initial_notification_count = await mailhog_count_for(technician_email)
    assert initial_notification_count >= 1
    reassigned = await client.post(
        f"/api/v1/tickets/{ticket_id}/assign",
        headers=admin_headers,
        json={"assignee_id": assign_http["ids"]["A_administrator"]},
    )
    assert reassigned.status_code == 200, reassigned.text
    for _ in range(30):
        if await mailhog_count_for(technician_email) > initial_notification_count:
            break
        await asyncio.sleep(0.2)
    assert await mailhog_count_for(technician_email) == initial_notification_count + 1
    async with AsyncSession(assign_http["engine"]) as db:
        audit_count_before = await db.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.entity == "ticket",
                AuditLog.entity_id == ticket_id,
            )
        )
    same = await client.post(
        f"/api/v1/tickets/{ticket_id}/assign",
        headers=admin_headers,
        json={"assignee_id": assign_http["ids"]["A_administrator"]},
    )
    assert same.status_code == 200, same.text
    assert same.json()["reason"] == "unchanged"
    notification_count = await mailhog_count_for(technician_email)
    await asyncio.sleep(0.8)
    assert await mailhog_count_for(technician_email) == notification_count
    async with AsyncSession(assign_http["engine"]) as db:
        assert await db.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.entity == "ticket",
                AuditLog.entity_id == ticket_id,
            )
        ) == audit_count_before

    async with AsyncSession(assign_http["engine"]) as db:
        db.add(
            TicketWorkflow(
                entity_id=assign_http["entities"]["A"].id,
                record_type="ticket",
                states=["NEW", "ASSIGNED"],
                transitions={"NEW": ["ASSIGNED"], "ASSIGNED": ["NEW"]},
                approval_requirements={},
                is_active=True,
            )
        )
        await db.commit()
    unassigned = await client.post(
        f"/api/v1/tickets/{ticket_id}/assign",
        headers=admin_headers,
        json={"assignee_id": None},
    )
    assert unassigned.status_code == 200, unassigned.text
    assert unassigned.json()["ticket"]["status"] == "NEW"
    assert unassigned.json()["ticket"]["assignee_id"] is None


@pytest.mark.asyncio
async def test_assignable_users_and_assignment_are_entity_and_visibility_scoped(assign_http) -> None:
    ticket_id = await create_ticket(assign_http, routed_team_id=assign_http["teams"]["A"].id)
    admin_headers = headers(assign_http, "A_administrator")
    endpoint = await assign_http["client"].get(
        "/api/v1/users/assignable",
        headers=admin_headers,
        params={"ticket_id": ticket_id, "limit": 50},
    )
    assert endpoint.status_code == 200, endpoint.text
    assert any(user["id"] == assign_http["ids"]["A_technician"] for user in endpoint.json()["items"])
    assert all(user["id"] != assign_http["ids"]["B_technician"] for user in endpoint.json()["items"])

    foreign_assignee = await assign_http["client"].post(
        f"/api/v1/tickets/{ticket_id}/assign",
        headers=admin_headers,
        json={"assignee_id": assign_http["ids"]["B_technician"]},
    )
    assert foreign_assignee.status_code == 422

    no_visibility = await assign_http["client"].post(
        f"/api/v1/tickets/{ticket_id}/assign",
        headers=admin_headers,
        json={"assignee_id": assign_http["ids"]["A_requester"]},
    )
    assert no_visibility.status_code == 422
    assert no_visibility.json()["detail"] == "assignee_cannot_view_ticket"

    foreign_ticket = await create_ticket(assign_http, entity_key="B")
    hidden = await assign_http["client"].post(
        f"/api/v1/tickets/{foreign_ticket}/assign",
        headers=admin_headers,
        json={"assignee_id": assign_http["ids"]["A_technician"]},
    )
    assert hidden.status_code == 404

    agents = await assign_http["client"].get("/api/v1/agents", headers=admin_headers)
    assert agents.status_code == 200, agents.text
    assert all(item["id"] != assign_http["ids"]["B_technician"] for item in agents.json())

    tech_headers = headers(assign_http, "A_technician")
    technician_candidates = await assign_http["client"].get(
        "/api/v1/users/assignable",
        headers=tech_headers,
        params={"ticket_id": ticket_id, "limit": 50},
    )
    assert technician_candidates.status_code == 200, technician_candidates.text
    candidate_ids = {item["id"] for item in technician_candidates.json()["items"]}
    assert candidate_ids == {
        assign_http["ids"]["A_technician"],
        assign_http["ids"]["A_administrator"],
    }
    blocked_assignment = await assign_http["client"].post(
        f"/api/v1/tickets/{ticket_id}/assign",
        headers=tech_headers,
        json={"assignee_id": assign_http["ids"]["A_outside_technician"]},
    )
    assert blocked_assignment.status_code == 403


@pytest.mark.asyncio
async def test_requester_is_denied_and_workflow_can_reject_unassignment(assign_http) -> None:
    client = assign_http["client"]
    ticket_id = await create_ticket(assign_http)
    forbidden = await client.post(
        f"/api/v1/tickets/{ticket_id}/assign",
        headers=headers(assign_http, "A_requester"),
        json={"assignee_id": assign_http["ids"]["A_technician"]},
    )
    assert forbidden.status_code == 403

    assigned = await client.post(
        f"/api/v1/tickets/{ticket_id}/assign",
        headers=headers(assign_http, "A_administrator"),
        json={"assignee_id": assign_http["ids"]["A_technician"]},
    )
    assert assigned.status_code == 200, assigned.text
    async with AsyncSession(assign_http["engine"], expire_on_commit=False) as db:
        db.add(
            TicketWorkflow(
                entity_id=assign_http["entities"]["A"].id,
                record_type="ticket",
                states=["NEW", "ASSIGNED"],
                transitions={"NEW": ["ASSIGNED"], "ASSIGNED": []},
                approval_requirements={},
                is_active=True,
            )
        )
        await db.commit()
    rejected = await client.post(
        f"/api/v1/tickets/{ticket_id}/assign",
        headers=headers(assign_http, "A_administrator"),
        json={"assignee_id": None},
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "Invalid ticket transition"
    async with AsyncSession(assign_http["engine"]) as db:
        ticket = await db.scalar(select(Ticket).where(Ticket.id == ticket_id))
    assert ticket is not None
    assert ticket.status == "ASSIGNED"
    assert ticket.assignee_id == assign_http["ids"]["A_technician"]


@pytest.mark.asyncio
async def test_assign_and_claim_or_patch_lock_the_ticket_without_duplicate_assignees(assign_http) -> None:
    client = assign_http["client"]
    ticket_id = await create_ticket(assign_http)
    assign_request = client.post(
        f"/api/v1/tickets/{ticket_id}/assign",
        headers=headers(assign_http, "A_administrator"),
        json={"assignee_id": assign_http["ids"]["A_technician"]},
    )
    claim_request = client.post(
        f"/api/v1/tickets/{ticket_id}/claim",
        headers=headers(assign_http, "A_technician"),
    )
    responses = await asyncio.wait_for(asyncio.gather(assign_request, claim_request), timeout=10)
    assert all(response.status_code in {200, 409, 404} for response in responses)
    async with AsyncSession(assign_http["engine"]) as db:
        actors = list(
            (
                await db.scalars(
                    select(TicketActor).where(
                        TicketActor.ticket_id == ticket_id,
                        TicketActor.actor_type == "USER",
                        TicketActor.role == "ASSIGNEE",
                    )
                )
            ).all()
        )
        ticket = await db.scalar(select(Ticket).where(Ticket.id == ticket_id))
    assert ticket is not None and ticket.assignee_id is not None
    assert len(actors) == 1

    second_ticket = await create_ticket(assign_http)
    concurrent_assign = client.post(
        f"/api/v1/tickets/{second_ticket}/assign",
        headers=headers(assign_http, "A_administrator"),
        json={"assignee_id": assign_http["ids"]["A_technician"]},
    )
    concurrent_patch = client.patch(
        f"/api/v1/tickets/{second_ticket}",
        headers=headers(assign_http, "A_administrator"),
        json={"assignee_id": assign_http["ids"]["A_administrator"]},
    )
    responses = await asyncio.wait_for(
        asyncio.gather(concurrent_assign, concurrent_patch),
        timeout=10,
    )
    assert all(response.status_code == 200 for response in responses)
    async with AsyncSession(assign_http["engine"]) as db:
        actors = list(
            (
                await db.scalars(
                    select(TicketActor).where(
                        TicketActor.ticket_id == second_ticket,
                        TicketActor.actor_type == "USER",
                        TicketActor.role == "ASSIGNEE",
                    )
                )
            ).all()
        )
    assert len(actors) == 1
