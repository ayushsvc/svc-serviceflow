import asyncio
from uuid import uuid4

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.deps import current_user, get_db
from app.main import app
from app.seed import seed_profile_rights, seed_ticket_type_field_defaults
from app.models import (
    AuditLog,
    Entity,
    FieldDefinition,
    Group,
    GroupMember,
    Profile,
    Ticket,
    TicketActor,
    TicketDocument,
    TicketFieldValue,
    TicketFollowup,
    TicketLink,
    TicketSolution,
    TicketTask,
    TicketTypeDefinition,
    TicketTypeField,
    User,
    WorkflowApproval,
)


@pytest.fixture
async def step5d_http():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    suffix = uuid4().hex
    user_by_id: dict[int, User] = {}
    entities: dict[str, Entity] = {}
    async with AsyncSession(engine, expire_on_commit=False) as db:
        requester_profile = await db.scalar(select(Profile).where(Profile.name == "Requester"))
        technician_profile = await db.scalar(select(Profile).where(Profile.name == "Technician"))
        assert requester_profile is not None and technician_profile is not None
        administrator = await db.scalar(
            select(User)
            .join(Profile, User.profile_id == Profile.id)
            .options(selectinload(User.profile))
            .where(Profile.name == "Administrator")
            .order_by(User.id)
        )
        assert administrator is not None
        custom_profile = Profile(
            name=f"Step5d custom queue {suffix}",
            interface="helpdesk",
            rights={"ticket:view_queue": True, "ticket:claim": True, "ticket:self_claim": True, "ticket:view_unassigned": True},
        )
        db.add(custom_profile)
        entities = {
            key: Entity(name=f"Step 5d {key} {suffix}")
            for key in ("A", "B")
        }
        db.add_all(entities.values())
        await db.flush()
        users = {
            "A_technician": User(
                email=f"step5d-a-technician-{suffix}@example.test",
                password_hash="test-only",
                name="Queue technician A",
                profile=custom_profile,
                entity=entities["A"],
            ),
            "A_second_technician": User(
                email=f"step5d-a-technician-2-{suffix}@example.test",
                password_hash="test-only",
                name="Queue technician A2",
                profile=custom_profile,
                entity=entities["A"],
            ),
            "A_requester": User(
                email=f"step5d-a-requester-{suffix}@example.test",
                password_hash="test-only",
                name="Queue requester A",
                profile=requester_profile,
                entity=entities["A"],
            ),
            "B_technician": User(
                email=f"step5d-b-technician-{suffix}@example.test",
                password_hash="test-only",
                name="Queue technician B",
                profile=custom_profile,
                entity=entities["B"],
            ),
            "B_requester": User(
                email=f"step5d-b-requester-{suffix}@example.test",
                password_hash="test-only",
                name="Queue requester B",
                profile=requester_profile,
                entity=entities["B"],
            ),
        }
        db.add_all(users.values())
        await db.commit()
        user_by_id = {user.id: user for user in users.values()}
        user_by_id[administrator.id] = administrator
        ids = {key: user.id for key, user in users.items()}

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
                "entities": entities,
                "users": users,
                "ids": ids,
                "user_by_role": {"Administrator": administrator.id},
            }
    finally:
        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(get_db, None)
        entity_ids = [entity.id for entity in entities.values()]
        user_ids = list(ids.values())
        async with AsyncSession(engine) as db:
            ticket_ids = list(
                (
                    await db.scalars(select(Ticket.id).where(Ticket.entity_id.in_(entity_ids)))
                ).all()
            )
            if ticket_ids:
                await db.execute(delete(WorkflowApproval).where(WorkflowApproval.record_id.in_(ticket_ids)))
                await db.execute(delete(TicketDocument).where(TicketDocument.ticket_id.in_(ticket_ids)))
                await db.execute(delete(TicketFieldValue).where(TicketFieldValue.ticket_id.in_(ticket_ids)))
                await db.execute(delete(TicketFollowup).where(TicketFollowup.ticket_id.in_(ticket_ids)))
                await db.execute(delete(TicketTask).where(TicketTask.ticket_id.in_(ticket_ids)))
                await db.execute(delete(TicketSolution).where(TicketSolution.ticket_id.in_(ticket_ids)))
                await db.execute(delete(TicketLink).where(TicketLink.ticket_id.in_(ticket_ids)))
                await db.execute(delete(TicketActor).where(TicketActor.ticket_id.in_(ticket_ids)))
                await db.execute(delete(AuditLog).where(AuditLog.entity == "ticket", AuditLog.entity_id.in_(ticket_ids)))
                await db.execute(delete(Ticket).where(Ticket.id.in_(ticket_ids)))
            await db.execute(delete(AuditLog).where(AuditLog.actor_id.in_(user_ids)))
            await db.execute(delete(GroupMember).where(GroupMember.user_id.in_(user_ids)))
            await db.execute(delete(User).where(User.id.in_(user_ids)))
            await db.execute(delete(Group).where(Group.entity_id.in_(entity_ids)))
            await db.execute(delete(Entity).where(Entity.id.in_(entity_ids)))
            await db.execute(delete(Profile).where(Profile.name == f"Step5d custom queue {suffix}"))
            await db.commit()
        await engine.dispose()


async def add_ticket(
    engine,
    entity_id: int,
    requester_id: int,
    title: str,
    *,
    group_id: int | None = None,
    assignee_id: int | None = None,
) -> int:
    async with AsyncSession(engine, expire_on_commit=False) as db:
        ticket = Ticket(
            title=title,
            description="Step 5d queue test ticket.",
            type="INCIDENT",
            status="ASSIGNED" if assignee_id else "NEW",
            urgency=3,
            impact=3,
            priority=3,
            entity_id=entity_id,
            requester_id=requester_id,
            assignee_id=assignee_id,
            source="web",
        )
        db.add(ticket)
        await db.flush()
        if group_id is not None:
            db.add(TicketActor(ticket_id=ticket.id, actor_type="GROUP", actor_id=group_id, role="ASSIGNEE"))
        ticket_id = ticket.id
        await db.commit()
    return ticket_id


@pytest.mark.asyncio
async def test_queue_scopes_are_rights_based_and_entity_scoped(step5d_http) -> None:
    client = step5d_http["client"]
    engine = step5d_http["engine"]
    entities = step5d_http["entities"]
    ids = step5d_http["ids"]
    async with AsyncSession(engine, expire_on_commit=False) as db:
        team_a = Group(name=f"Step5d A team {uuid4().hex}", entity_id=entities["A"].id)
        team_other = Group(name=f"Step5d other team {uuid4().hex}", entity_id=entities["A"].id)
        db.add_all([team_a, team_other])
        await db.flush()
        db.add(GroupMember(group_id=team_a.id, user_id=ids["A_technician"]))
        await db.commit()
        team_a_id, other_team_id = team_a.id, team_other.id
    unassigned_a = await add_ticket(engine, entities["A"].id, ids["A_requester"], "A unassigned")
    routed_a = await add_ticket(engine, entities["A"].id, ids["A_requester"], "A team ticket", group_id=team_a_id)
    hidden_routed = await add_ticket(engine, entities["A"].id, ids["A_requester"], "A other-team ticket", group_id=other_team_id)
    unassigned_b = await add_ticket(engine, entities["B"].id, ids["B_requester"], "B unassigned")

    headers = {"x-test-user": str(ids["A_technician"])}
    response = await client.get("/api/v1/tickets?queue=all", headers=headers)
    assert response.status_code == 422
    visible = await client.get("/api/v1/tickets?queue=unassigned", headers=headers)
    assert visible.status_code == 200, visible.text
    assert {row["id"] for row in visible.json()["items"]} == {unassigned_a}
    teams = await client.get("/api/v1/tickets?queue=teams", headers=headers)
    assert teams.status_code == 200, teams.text
    assert {row["id"] for row in teams.json()["items"]} == {routed_a}
    counts = await client.get("/api/v1/tickets/queue-counts", headers=headers)
    assert counts.status_code == 200, counts.text
    assert counts.json() == {"all": 2, "mine": 0, "teams": 1, "unassigned": 1}
    for queue in ("mine", "teams", "unassigned"):
        listed_queue = await client.get(f"/api/v1/tickets?queue={queue}", headers=headers)
        assert listed_queue.status_code == 200, listed_queue.text
        assert listed_queue.json()["total"] == counts.json()[queue]
    team_counts = await client.get("/api/v1/tickets/team-counts", headers=headers)
    assert team_counts.status_code == 200, team_counts.text
    assert next(row for row in team_counts.json() if row["team_id"] is None)["count"] == 1

    foreign = await client.get("/api/v1/tickets?queue=unassigned", headers={"x-test-user": str(ids["B_technician"])})
    assert foreign.status_code == 200, foreign.text
    assert {row["id"] for row in foreign.json()["items"]} == {unassigned_b}
    assert hidden_routed not in {row["id"] for row in teams.json()["items"]}


@pytest.mark.asyncio
async def test_claim_commits_assignment_timeline_and_audit(step5d_http) -> None:
    client = step5d_http["client"]
    engine = step5d_http["engine"]
    ids = step5d_http["ids"]
    entity = step5d_http["entities"]["A"]
    ticket_id = await add_ticket(engine, entity.id, ids["A_requester"], "Claim happy path")
    response = await client.post(
        f"/api/v1/tickets/{ticket_id}/claim",
        headers={"x-test-user": str(ids["A_technician"])},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ASSIGNED"
    assert response.json()["assignee_id"] == ids["A_technician"]
    async with AsyncSession(engine, expire_on_commit=False) as db:
        assert await db.scalar(
            select(TicketFollowup.id).where(
                TicketFollowup.ticket_id == ticket_id,
                TicketFollowup.is_private.is_(True),
            )
        )
        assert await db.scalar(
            select(AuditLog.id).where(
                AuditLog.entity == "ticket",
                AuditLog.entity_id == ticket_id,
                AuditLog.action == "claimed",
                AuditLog.actor_id == ids["A_technician"],
            )
        )


@pytest.mark.asyncio
async def test_claim_permissions_and_double_claim_race(step5d_http) -> None:
    client = step5d_http["client"]
    engine = step5d_http["engine"]
    ids = step5d_http["ids"]
    entity = step5d_http["entities"]["A"]
    denied_ticket = await add_ticket(engine, entity.id, ids["A_requester"], "Requester cannot claim")
    denied = await client.post(
        f"/api/v1/tickets/{denied_ticket}/claim",
        headers={"x-test-user": str(ids["A_requester"])},
    )
    assert denied.status_code == 403
    foreign = await client.post(
        f"/api/v1/tickets/{denied_ticket}/claim",
        headers={"x-test-user": str(ids["B_technician"])},
    )
    assert foreign.status_code == 404

    already_assigned = await add_ticket(
        engine,
        entity.id,
        ids["A_requester"],
        "Already assigned",
        assignee_id=ids["A_technician"],
    )
    conflict = await client.post(
        f"/api/v1/tickets/{already_assigned}/claim",
        headers={"x-test-user": str(ids["A_technician"])},
    )
    assert conflict.status_code == 409

    race_ticket = await add_ticket(engine, entity.id, ids["A_requester"], "Concurrent claim")
    first, second = await asyncio.gather(
        client.post(
            f"/api/v1/tickets/{race_ticket}/claim",
            headers={"x-test-user": str(ids["A_technician"])},
        ),
        client.post(
            f"/api/v1/tickets/{race_ticket}/claim",
            headers={"x-test-user": str(ids["A_second_technician"])},
        ),
    )
    assert sorted([first.status_code, second.status_code]) == [200, 409]


@pytest.mark.asyncio
async def test_requester_cannot_submit_problem_or_change_ticket_types(step5d_http) -> None:
    client = step5d_http["client"]
    requester = next(
        user for user in step5d_http["users"].values()
        if user.profile.name == "Requester"
    )
    headers = {"x-test-user": str(requester.id)}
    async with AsyncSession(step5d_http["engine"], expire_on_commit=False) as db:
        types = {
            ticket_type.itil_class: ticket_type.id
            for ticket_type in (
                await db.scalars(
                    select(TicketTypeDefinition).where(
                        TicketTypeDefinition.itil_class.in_(["PROBLEM", "CHANGE"]),
                        TicketTypeDefinition.is_active.is_(True),
                    )
                )
            ).all()
        }
    assert {"PROBLEM", "CHANGE"} <= types.keys()
    for ticket_class in ("PROBLEM", "CHANGE"):
        response = await client.post(
            "/api/v1/tickets",
            headers=headers,
            json={
                "title": f"Rejected {ticket_class} request",
                "description": "Requester portal must reject this ticket type.",
                "type": "INCIDENT",
                "ticket_type_id": types[ticket_class],
            },
        )
        assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_live_ticket_forms_are_role_filtered_and_requester_hidden_fields_are_rejected(step5d_http) -> None:
    client = step5d_http["client"]
    async with AsyncSession(step5d_http["engine"], expire_on_commit=False) as db:
        incident = await db.scalar(
            select(TicketTypeDefinition)
            .where(
                TicketTypeDefinition.entity_id.is_(None),
                TicketTypeDefinition.itil_class == "INCIDENT",
                TicketTypeDefinition.is_active.is_(True),
            )
            .order_by(TicketTypeDefinition.id)
        )
    assert incident is not None

    forms = {}
    for role, user_id in (
        ("Administrator", step5d_http["user_by_role"]["Administrator"]),
        ("Technician", step5d_http["ids"]["B_technician"]),
        ("Requester", step5d_http["ids"]["A_requester"]),
    ):
        response = await client.get(
            f"/api/v1/ticket-types/{incident.id}/form",
            headers={"x-test-user": str(user_id)},
        )
        assert response.status_code == 200, response.text
        fields = [field for page in response.json()["pages"].values() for field in page]
        forms[role] = {field["key"] for field in fields}

    assert len({len(keys) for keys in forms.values()}) > 1
    assert forms["Requester"] == {"summary", "details", "attachments", "priority"}
    assert {"team", "agent", "sla", "flag", "source", "asset"} <= forms["Technician"]
    assert forms["Technician"] <= forms["Administrator"]

    rejected = await client.post(
        "/api/v1/tickets",
        headers={"x-test-user": str(step5d_http["ids"]["A_requester"])},
        json={
            "title": "Hidden field rejection",
            "description": "A requester must not submit a hidden technician field.",
            "type": "INCIDENT",
            "ticket_type_id": incident.id,
            "custom_fields": {
                "summary": "Hidden field rejection",
                "details": "A requester must not submit a hidden technician field.",
                "source": "EMAIL",
            },
        },
    )
    assert rejected.status_code == 422


@pytest.mark.asyncio
async def test_profile_right_seeding_is_additive_and_preserves_existing_values() -> None:
    profiles = {
        "Requester": Profile(name="Requester", rights={"ticket:own_only": False, "custom": True}),
        "Technician": Profile(name="Technician", rights={"ticket:view_queue": False, "custom": True}),
        "Administrator": Profile(name="Administrator", rights={"*": True, "custom": True}),
    }
    changed = await seed_profile_rights(profiles)
    assert profiles["Requester"].rights == {"ticket:own_only": False, "custom": True}
    assert profiles["Technician"].rights == {
        "ticket:view_queue": False,
        "ticket:claim": True,
        "ticket:bulk_edit": True,
        "ticket:self_claim": False,
        "ticket:view_unassigned": False,
        "custom": True,
    }
    assert profiles["Administrator"].rights == {
        "*": True,
        "custom": True,
        "ticket:view_all": True,
        "ticket:claim": True,
        "ticket:bulk_edit": True,
        "ticket:assign_any": True,
        "ticket:self_claim": True,
        "ticket:view_unassigned": True,
    }
    assert changed


@pytest.mark.asyncio
async def test_field_seed_updates_only_original_defaults_and_is_idempotent(step5d_http) -> None:
    engine = step5d_http["engine"]
    entity = step5d_http["entities"]["A"]
    async with engine.connect() as connection:
        transaction = await connection.begin()
        db = AsyncSession(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        try:
            source = await db.scalar(select(FieldDefinition).where(FieldDefinition.key == "source"))
            priority = await db.scalar(select(FieldDefinition).where(FieldDefinition.key == "priority"))
            urgency = await db.scalar(select(FieldDefinition).where(FieldDefinition.key == "urgency"))
            assert source is not None and priority is not None and urgency is not None
            ticket_type = TicketTypeDefinition(
                name=f"Step5d seed defaults {uuid4().hex}",
                itil_class="INCIDENT",
                entity_id=entity.id,
            )
            db.add(ticket_type)
            await db.flush()
            source_mapping = TicketTypeField(
                ticket_type_id=ticket_type.id,
                field_definition_id=source.id,
                visible_to_roles=["Administrator", "Technician", "Requester"],
                editable_by_roles=["Administrator", "Technician"],
                show_on_portal=True,
            )
            customized_mapping = TicketTypeField(
                ticket_type_id=ticket_type.id,
                field_definition_id=priority.id,
                visible_to_roles=["Administrator"],
                editable_by_roles=["Administrator"],
                show_on_portal=False,
            )
            urgency_mapping = TicketTypeField(
                ticket_type_id=ticket_type.id,
                field_definition_id=urgency.id,
                visible_to_roles=["Administrator", "Technician", "Requester"],
                editable_by_roles=["Administrator", "Technician"],
                show_on_portal=True,
            )
            db.add_all([source_mapping, customized_mapping, urgency_mapping])
            await db.flush()

            changed = await seed_ticket_type_field_defaults(db)
            assert set(changed) == {
                f"{ticket_type.name}.source",
                f"{ticket_type.name}.urgency",
                f"{ticket_type.name}.attachments (added)",
            }
            assert source_mapping.visible_to_roles == ["Administrator", "Technician"]
            assert source_mapping.editable_by_roles == ["Administrator", "Technician"]
            assert source_mapping.show_on_portal is False
            assert urgency_mapping.editable_by_roles == ["Administrator", "Technician"]
            assert urgency_mapping.show_on_portal is False
            assert customized_mapping.visible_to_roles == ["Administrator"]
            assert customized_mapping.editable_by_roles == ["Administrator"]
            assert customized_mapping.show_on_portal is False
            assert await seed_ticket_type_field_defaults(db) == []
        finally:
            await db.close()
            if transaction.is_active:
                await transaction.rollback()


def test_startup_requires_jwt_secret_without_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import ValidationError

    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(ValidationError, match="JWT_SECRET is required from the environment in every mode"):
        Settings(_env_file=None, debug=False)
