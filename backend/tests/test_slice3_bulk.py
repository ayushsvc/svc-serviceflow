import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.deps import current_user, get_db
from app.main import app
from app.seed import seed_profile_rights
from app.models import (
    AuditLog,
    Entity,
    Group,
    GroupMember,
    Profile,
    Ticket,
    TicketActor,
    TicketFollowup,
    TicketWorkflow,
    User,
    WorkflowApproval,
)


@pytest.fixture
async def bulk_http():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    suffix = uuid4().hex
    async with AsyncSession(engine, expire_on_commit=False) as db:
        entity_a = Entity(name=f"Slice 3 A {suffix}")
        entity_b = Entity(name=f"Slice 3 B {suffix}")
        profiles = {
            "technician": Profile(
                name=f"Slice 3 tech {suffix}",
                interface="helpdesk",
                rights={"ticket:view_queue": True, "ticket:claim": True, "ticket:bulk_edit": True, "ticket:self_claim": True, "ticket:view_unassigned": True},
            ),
            "requester": Profile(
                name=f"Slice 3 requester {suffix}",
                interface="self_service",
                rights={"ticket:own_only": True},
            ),
            "administrator": Profile(
                name=f"Slice 3 admin {suffix}",
                interface="helpdesk",
                rights={
                    "ticket:view_all": True,
                    "ticket:bulk_edit": True,
                    "ticket:assign_any": True,
                    "ticket:claim": True,
                },
            ),
        }
        db.add_all([entity_a, entity_b, *profiles.values()])
        await db.flush()
        group_a = Group(name=f"Slice 3 team A {suffix}", entity_id=entity_a.id)
        group_a_other = Group(name=f"Slice 3 other team A {suffix}", entity_id=entity_a.id)
        group_b = Group(name=f"Slice 3 team B {suffix}", entity_id=entity_b.id)
        users = {
            "tech": User(
                email=f"slice3-tech-{suffix}@example.test",
                password_hash="test-only",
                name="Bulk technician",
                profile=profiles["technician"],
                entity=entity_a,
            ),
            "teammate": User(
                email=f"slice3-teammate-{suffix}@example.test",
                password_hash="test-only",
                name="Bulk teammate",
                profile=profiles["technician"],
                entity=entity_a,
            ),
            "outsider": User(
                email=f"slice3-outsider-{suffix}@example.test",
                password_hash="test-only",
                name="Other team member",
                profile=profiles["technician"],
                entity=entity_a,
            ),
            "requester": User(
                email=f"slice3-requester-{suffix}@example.test",
                password_hash="test-only",
                name="Bulk requester",
                profile=profiles["requester"],
                entity=entity_a,
            ),
            "admin": User(
                email=f"slice3-admin-{suffix}@example.test",
                password_hash="test-only",
                name="Bulk administrator",
                profile=profiles["administrator"],
                entity=entity_a,
            ),
            "foreign_tech": User(
                email=f"slice3-foreign-tech-{suffix}@example.test",
                password_hash="test-only",
                name="Foreign technician",
                profile=profiles["technician"],
                entity=entity_b,
            ),
            "foreign_requester": User(
                email=f"slice3-foreign-requester-{suffix}@example.test",
                password_hash="test-only",
                name="Foreign requester",
                profile=profiles["requester"],
                entity=entity_b,
            ),
        }
        db.add_all([group_a, group_a_other, group_b, *users.values()])
        await db.flush()
        db.add_all(
            [
                GroupMember(group_id=group_a.id, user_id=users["tech"].id),
                GroupMember(group_id=group_a.id, user_id=users["teammate"].id),
                GroupMember(group_id=group_a_other.id, user_id=users["outsider"].id),
                GroupMember(group_id=group_b.id, user_id=users["foreign_tech"].id),
            ]
        )
        await db.commit()
        ids = {key: user.id for key, user in users.items()}
        user_by_id = {user.id: user for user in users.values()}
        entity_ids = [entity_a.id, entity_b.id]
        group_ids = [group_a.id, group_a_other.id, group_b.id]
        profile_ids = [profile.id for profile in profiles.values()]
        context = {
            "engine": engine,
            "entity_a": entity_a,
            "entity_b": entity_b,
            "group_a": group_a,
            "group_a_other": group_a_other,
            "group_b": group_b,
            "users": users,
            "ids": ids,
            "profile_ids": profile_ids,
        }

    async def user_override(request: Request) -> User:
        return user_by_id[int(request.headers["x-test-user"])]

    async def db_override():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[current_user] = user_override
    app.dependency_overrides[get_db] = db_override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            context["client"] = client
            yield context
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
            await db.execute(delete(GroupMember).where(GroupMember.group_id.in_(group_ids)))
            await db.execute(delete(User).where(User.id.in_(list(ids.values()))))
            await db.execute(delete(Group).where(Group.id.in_(group_ids)))
            await db.execute(delete(Entity).where(Entity.id.in_(entity_ids)))
            await db.execute(delete(Profile).where(Profile.id.in_(profile_ids)))
            await db.commit()
        await engine.dispose()


async def create_ticket(
    engine,
    *,
    entity_id: int,
    requester_id: int,
    title: str,
    assignee_id: int | None = None,
    group_id: int | None = None,
    status: str = "NEW",
    deleted: bool = False,
) -> int:
    async with AsyncSession(engine, expire_on_commit=False) as db:
        ticket = Ticket(
            title=title,
            description="A test ticket for bulk ticket behavior.",
            type="INCIDENT",
            status=status,
            urgency=3,
            impact=3,
            priority=3,
            entity_id=entity_id,
            requester_id=requester_id,
            assignee_id=assignee_id,
            deleted_at=datetime.now(UTC) if deleted else None,
        )
        db.add(ticket)
        await db.flush()
        if group_id is not None:
            db.add(TicketActor(ticket_id=ticket.id, actor_type="GROUP", actor_id=group_id, role="ASSIGNEE"))
        await db.commit()
        return ticket.id


def headers(context, user: str) -> dict[str, str]:
    return {"x-test-user": str(context["ids"][user])}


async def post_bulk(context, user: str, ticket_ids: list, action: str, value):
    return await context["client"].post(
        "/api/v1/tickets/bulk",
        headers=headers(context, user),
        json={"ticket_ids": ticket_ids, "action": action, "value": value},
    )


@pytest.mark.asyncio
async def test_bulk_rights_seed_is_additive_and_preserves_existing_values():
    profiles = {
        "Requester": Profile(
            name="Requester",
            rights={"ticket:own_only": True, "custom:preserve": True},
        ),
        "Technician": Profile(
            name="Technician",
            rights={"ticket:bulk_edit": False, "custom:preserve": True},
        ),
        "Administrator": Profile(
            name="Administrator",
            rights={"ticket:assign_any": False, "custom:preserve": True},
        ),
    }
    await seed_profile_rights(profiles)
    assert profiles["Technician"].rights["ticket:bulk_edit"] is False
    assert profiles["Technician"].rights["ticket:view_queue"] is True
    assert profiles["Technician"].rights["custom:preserve"] is True
    assert profiles["Administrator"].rights["ticket:assign_any"] is False
    assert profiles["Administrator"].rights["ticket:bulk_edit"] is True
    assert "ticket:bulk_edit" not in profiles["Requester"].rights


@pytest.mark.asyncio
async def test_bulk_visibility_matches_list_and_hides_foreign_deleted_and_team_invisible(bulk_http):
    visible_id = await create_ticket(
        bulk_http["engine"],
        entity_id=bulk_http["entity_a"].id,
        requester_id=bulk_http["ids"]["requester"],
        title="Visible unassigned",
    )
    foreign_id = await create_ticket(
        bulk_http["engine"],
        entity_id=bulk_http["entity_b"].id,
        requester_id=bulk_http["ids"]["foreign_tech"],
        title="Foreign entity ticket",
    )
    deleted_id = await create_ticket(
        bulk_http["engine"],
        entity_id=bulk_http["entity_a"].id,
        requester_id=bulk_http["ids"]["requester"],
        title="Deleted ticket",
        deleted=True,
    )
    invisible_id = await create_ticket(
        bulk_http["engine"],
        entity_id=bulk_http["entity_a"].id,
        requester_id=bulk_http["ids"]["requester"],
        title="Ticket routed to another team",
        group_id=bulk_http["group_a_other"].id,
    )
    list_response = await bulk_http["client"].get("/api/v1/tickets", headers=headers(bulk_http, "tech"))
    assert list_response.status_code == 200
    visible_list_ids = {ticket["id"] for ticket in list_response.json()["items"]}

    response = await post_bulk(
        bulk_http,
        "tech",
        [visible_id, foreign_id, deleted_id, invisible_id, 987654321],
        "flag",
        True,
    )
    assert response.status_code == 200
    results = {item["ticket_id"]: item for item in response.json()["results"]}
    assert visible_id in visible_list_ids
    for ticket_id in (foreign_id, deleted_id, invisible_id, 987654321):
        assert ticket_id not in visible_list_ids
        assert results[ticket_id] == {"ticket_id": ticket_id, "ok": False, "reason": "not_found"}
    assert results[visible_id]["ok"] is True


@pytest.mark.asyncio
async def test_bulk_requester_denied_and_assignments_respect_team_and_entity_rights(bulk_http):
    ticket_id = await create_ticket(
        bulk_http["engine"],
        entity_id=bulk_http["entity_a"].id,
        requester_id=bulk_http["ids"]["requester"],
        title="Assignment rules",
    )
    requester_response = await post_bulk(bulk_http, "requester", [ticket_id], "flag", True)
    assert requester_response.status_code == 403

    denied = await post_bulk(bulk_http, "tech", [ticket_id], "assignee", bulk_http["ids"]["outsider"])
    assert denied.status_code == 200
    assert denied.json()["results"][0]["reason"] == "forbidden_action"

    assigned = await post_bulk(bulk_http, "tech", [ticket_id], "assignee", bulk_http["ids"]["teammate"])
    assert assigned.status_code == 200
    assert assigned.json()["results"][0]["ok"] is True

    other_entity_id = await create_ticket(
        bulk_http["engine"],
        entity_id=bulk_http["entity_a"].id,
        requester_id=bulk_http["ids"]["requester"],
        title="Cross-entity assignee",
    )
    cross_entity = await post_bulk(
        bulk_http, "admin", [other_entity_id], "assignee", bulk_http["ids"]["foreign_tech"]
    )
    assert cross_entity.status_code == 200
    assert cross_entity.json()["results"][0]["reason"] == "invalid_value"
    cross_entity_team = await post_bulk(
        bulk_http, "admin", [other_entity_id], "team", bulk_http["group_b"].id
    )
    assert cross_entity_team.status_code == 200
    assert cross_entity_team.json()["results"][0]["reason"] == "invalid_value"
    admin_assignment = await post_bulk(
        bulk_http, "admin", [other_entity_id], "assignee", bulk_http["ids"]["outsider"]
    )
    assert admin_assignment.status_code == 200
    assert admin_assignment.json()["results"][0]["ok"] is True


@pytest.mark.asyncio
async def test_bulk_value_validation_deduplicates_ids_and_preserves_per_item_results(bulk_http):
    ticket_id = await create_ticket(
        bulk_http["engine"],
        entity_id=bulk_http["entity_a"].id,
        requester_id=bulk_http["ids"]["requester"],
        title="Priority idempotency",
    )
    second_ticket_id = await create_ticket(
        bulk_http["engine"],
        entity_id=bulk_http["entity_a"].id,
        requester_id=bulk_http["ids"]["requester"],
        title="Shared request audit id",
    )
    changed = await post_bulk(
        bulk_http, "tech", [ticket_id, ticket_id, second_ticket_id], "priority", 1
    )
    assert changed.status_code == 200
    assert len(changed.json()["results"]) == 2
    assert [item["ticket_id"] for item in changed.json()["results"]] == sorted(
        {ticket_id, second_ticket_id}
    )
    assert changed.json()["summary"] == {"ok": 2, "failed": 0}
    unchanged = await post_bulk(bulk_http, "tech", [ticket_id], "priority", 1)
    assert unchanged.json()["results"][0] == {
        "ticket_id": ticket_id,
        "ok": True,
        "reason": "unchanged",
    }
    invalid_value = await post_bulk(bulk_http, "tech", [ticket_id], "priority", 9)
    assert invalid_value.json()["results"][0]["reason"] == "invalid_value"

    too_many = await post_bulk(bulk_http, "tech", list(range(1, 102)), "flag", True)
    assert too_many.status_code == 422
    non_integer = await post_bulk(bulk_http, "tech", [str(ticket_id)], "flag", True)
    assert non_integer.status_code == 422

    async with AsyncSession(bulk_http["engine"]) as db:
        entries = list(
            (
                await db.scalars(
                    select(AuditLog).where(AuditLog.entity == "ticket", AuditLog.entity_id == ticket_id)
                )
            ).all()
        )
        second_entries = list(
            (
                await db.scalars(
                    select(AuditLog).where(
                        AuditLog.entity == "ticket",
                        AuditLog.entity_id == second_ticket_id,
                    )
                )
            ).all()
        )
    assert len(entries) == 1
    assert len(second_entries) == 1
    assert entries[0].action == "bulk_priority"
    assert entries[0].diff["bulk_request_id"] == second_entries[0].diff["bulk_request_id"]
    assert entries[0].diff["priority"] == {"old": 3, "new": 1}


@pytest.mark.asyncio
async def test_bulk_workflow_gates_approvals_and_isolates_invalid_transitions(bulk_http):
    pending_ticket = await create_ticket(
        bulk_http["engine"],
        entity_id=bulk_http["entity_a"].id,
        requester_id=bulk_http["ids"]["requester"],
        title="Transition that can succeed",
    )
    closed_ticket = await create_ticket(
        bulk_http["engine"],
        entity_id=bulk_http["entity_a"].id,
        requester_id=bulk_http["ids"]["requester"],
        title="Transition that cannot succeed",
        status="CLOSED",
    )
    approval_ticket = await create_ticket(
        bulk_http["engine"],
        entity_id=bulk_http["entity_a"].id,
        requester_id=bulk_http["ids"]["requester"],
        title="Approval required",
    )
    async with AsyncSession(bulk_http["engine"]) as db:
        db.add(
            TicketWorkflow(
                entity_id=bulk_http["entity_a"].id,
                record_type="ticket",
                states=["NEW", "ASSIGNED", "PLANNED", "PENDING", "SOLVED", "CLOSED"],
                transitions={"NEW": ["PENDING", "SOLVED"], "ASSIGNED": ["PENDING"]},
                approval_requirements={"SOLVED": 1},
                is_active=True,
            )
        )
        await db.commit()
    response = await post_bulk(
        bulk_http, "tech", [pending_ticket, closed_ticket], "status", "PENDING"
    )
    assert response.status_code == 200
    reasons = {item["ticket_id"]: item["reason"] for item in response.json()["results"]}
    assert reasons[pending_ticket] is None
    assert reasons[closed_ticket] == "invalid_transition"
    approval_response = await post_bulk(bulk_http, "tech", [approval_ticket], "status", "SOLVED")
    assert approval_response.json()["results"][0]["reason"] == "approval_required"

    async with AsyncSession(bulk_http["engine"]) as db:
        approval_audit = await db.scalar(
            select(AuditLog.id).where(AuditLog.entity == "ticket", AuditLog.entity_id == approval_ticket)
        )
        approval_timeline = await db.scalar(
            select(TicketFollowup.id).where(TicketFollowup.ticket_id == approval_ticket)
        )
        updated = await db.scalar(select(Ticket).where(Ticket.id == pending_ticket))
    assert approval_audit is None
    assert approval_timeline is None
    assert updated.status == "PENDING"
    await asyncio.sleep(1)
    async with AsyncClient() as mailhog:
        messages = await mailhog.get(
            f"http://mailhog:8025/api/v2/search?kind=containing&query={approval_ticket}"
        )
    assert messages.status_code == 200
    assert messages.json().get("count", 0) == 0


@pytest.mark.asyncio
async def test_bulk_visibility_and_team_membership_are_rights_based_and_match_queue_list(bulk_http):
    visible_ticket = await create_ticket(
        bulk_http["engine"],
        entity_id=bulk_http["entity_a"].id,
        requester_id=bulk_http["ids"]["requester"],
        title="Rights-based queue ticket",
    )
    hidden_ticket = await create_ticket(
        bulk_http["engine"],
        entity_id=bulk_http["entity_a"].id,
        requester_id=bulk_http["ids"]["requester"],
        title="Different team ticket",
        group_id=bulk_http["group_a_other"].id,
    )
    queued = await bulk_http["client"].get(
        "/api/v1/tickets?queue=unassigned", headers=headers(bulk_http, "tech")
    )
    assert queued.status_code == 200
    assert {item["id"] for item in queued.json()["items"]} == {visible_ticket}
    response = await post_bulk(bulk_http, "tech", [visible_ticket, hidden_ticket], "flag", True)
    results = {item["ticket_id"]: item for item in response.json()["results"]}
    assert results[visible_ticket]["ok"] is True
    assert results[hidden_ticket]["reason"] == "not_found"


@pytest.mark.asyncio
async def test_concurrent_bulk_assignment_and_claim_lock_without_double_assignment(bulk_http):
    ticket_id = await create_ticket(
        bulk_http["engine"],
        entity_id=bulk_http["entity_a"].id,
        requester_id=bulk_http["ids"]["requester"],
        title="Concurrent claim and bulk assignment",
    )

    async def assign_bulk():
        return await post_bulk(bulk_http, "tech", [ticket_id], "assignee", bulk_http["ids"]["teammate"])

    async def claim():
        return await bulk_http["client"].post(
            f"/api/v1/tickets/{ticket_id}/claim",
            headers=headers(bulk_http, "teammate"),
        )

    bulk_response, claim_response = await asyncio.wait_for(
        asyncio.gather(assign_bulk(), claim()),
        timeout=10,
    )
    assert bulk_response.status_code == 200
    assert claim_response.status_code in {200, 404, 409}
    async with AsyncSession(bulk_http["engine"]) as db:
        ticket = await db.scalar(select(Ticket).where(Ticket.id == ticket_id))
        assignees = list(
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
    assert ticket.assignee_id is not None
    assert len({actor.actor_id for actor in assignees}) == 1
