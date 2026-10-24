from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.deps import current_user, get_db, require_admin, require_helpdesk
from app.main import app
from app.models import (
    Entity,
    Profile,
    Ticket,
    TicketDocument,
    TicketTypeDefinition,
    User,
)


def user(role: str, interface: str) -> SimpleNamespace:
    return SimpleNamespace(id=99, entity_id=999, profile=SimpleNamespace(name=role, interface=interface))


@pytest.fixture
async def isolated_entities_http():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        db = AsyncSession(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        suffix = uuid4().hex
        profiles = {
            profile.name: profile
            for profile in (
                await db.scalars(
                    select(Profile).where(Profile.name.in_(["Administrator", "Technician", "Requester"]))
                )
            ).all()
        }
        entities = {
            key: Entity(name=f"Isolation {key} {suffix}")
            for key in ("A", "B")
        }
        db.add_all(entities.values())
        await db.flush()
        users = {}
        for key, entity in entities.items():
            for role in ("Administrator", "Technician", "Requester"):
                account = User(
                    email=f"isolation-{key}-{role.lower()}-{suffix}@example.test",
                    password_hash="test-only",
                    name=f"Isolation {key} {role}",
                    profile=profiles[role],
                    entity=entity,
                )
                users[f"{key}_{role.lower()}"] = account
                db.add(account)
        await db.flush()
        state = {"user": users["A_requester"]}

        async def override_user() -> User:
            return state["user"]

        async def override_db():
            yield db

        app.dependency_overrides[current_user] = override_user
        app.dependency_overrides[get_db] = override_db
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                yield {"client": client, "db": db, "entities": entities, "users": users, "state": state}
        finally:
            app.dependency_overrides.pop(current_user, None)
            app.dependency_overrides.pop(get_db, None)
            await db.close()
            if transaction.is_active:
                await transaction.rollback()
            await engine.dispose()


@pytest.mark.asyncio
async def test_entity_b_cannot_read_entity_a_ticket_or_subresources(isolated_entities_http) -> None:
    client = isolated_entities_http["client"]
    db = isolated_entities_http["db"]
    state = isolated_entities_http["state"]
    users = isolated_entities_http["users"]
    incident = await db.scalar(
        select(TicketTypeDefinition).where(
            TicketTypeDefinition.itil_class == "INCIDENT",
            TicketTypeDefinition.is_active.is_(True),
        )
    )
    assert incident is not None

    state["user"] = users["A_requester"]

    async def create_ticket(label: str) -> dict:
        title = f"Entity isolation {label} {uuid4().hex}"
        response = await client.post(
            "/api/v1/tickets",
            json={
                "title": title,
                "description": "A ticket used to verify entity-level authorization.",
                "type": "INCIDENT",
                "ticket_type_id": incident.id,
                "custom_fields": {
                    "summary": title,
                    "details": "Private dynamic ticket fields.",
                },
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    ticket = await create_ticket("A")
    linked_ticket = await create_ticket("linked")
    for ticket_id in (ticket["id"], linked_ticket["id"]):
        scoped_ticket = await db.get(Ticket, ticket_id)
        assert scoped_ticket is not None
        scoped_ticket.assignee_id = users["A_technician"].id
    await db.flush()
    state["user"] = users["A_technician"]
    followup = await client.post(
        f"/api/v1/tickets/{ticket['id']}/followups",
        json={"body": "Private note for isolation test.", "is_private": True},
    )
    task = await client.post(
        f"/api/v1/tickets/{ticket['id']}/tasks",
        json={"body": "A ticket task used in isolation test."},
    )
    solution = await client.post(
        f"/api/v1/tickets/{ticket['id']}/solutions",
        json={"body": "A proposed solution used in isolation test."},
    )
    actor = await client.post(
        f"/api/v1/tickets/{ticket['id']}/actors",
        json={"actor_type": "USER", "actor_id": users["A_requester"].id, "role": "OBSERVER"},
    )
    link = await client.post(
        f"/api/v1/tickets/{ticket['id']}/links",
        json={"linked_ticket_id": linked_ticket["id"], "link_type": "RELATED"},
    )
    uploaded = await client.post(
        f"/api/v1/tickets/{ticket['id']}/documents",
        files={"document": ("isolation.txt", b"entity scoped attachment", "text/plain")},
    )
    assert followup.status_code == 201, followup.text
    assert task.status_code == 201, task.text
    assert solution.status_code == 201, solution.text
    assert actor.status_code == 201, actor.text
    assert link.status_code == 201, link.text
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["id"]
    document_path = await db.scalar(select(TicketDocument.path).where(TicketDocument.id == document_id))
    assert document_path

    try:
        state["user"] = users["B_technician"]
        read_paths = [
            f"/api/v1/tickets/{ticket['id']}",
            f"/api/v1/tickets/{ticket['id']}/timeline",
            f"/api/v1/tickets/{ticket['id']}/followups",
            f"/api/v1/tickets/{ticket['id']}/tasks",
            f"/api/v1/tickets/{ticket['id']}/solutions",
            f"/api/v1/tickets/{ticket['id']}/links",
            f"/api/v1/tickets/{ticket['id']}/actors",
            f"/api/v1/tickets/{ticket['id']}/documents/{document_id}",
        ]
        for path in read_paths:
            response = await client.get(path)
            assert response.status_code == 404, f"{path} returned {response.status_code}: {response.text}"

        state["user"] = users["B_requester"]
        audit_forbidden = await client.get("/api/v1/audit")
        assert audit_forbidden.status_code == 403

        state["user"] = users["B_administrator"]
        audit = await client.get("/api/v1/audit", params={"page_size": 100})
        assert audit.status_code == 200, audit.text
        assert not any(
            entry["entity"] == "ticket" and entry["entity_id"] == ticket["id"]
            for entry in audit.json()
        )
    finally:
        Path(document_path).unlink(missing_ok=True)


def test_role_matrix_for_helpdesk_and_administrator_guards() -> None:
    administrator = user("Administrator", "helpdesk")
    technician = user("Technician", "helpdesk")
    requester = user("Requester", "self_service")

    assert require_helpdesk(administrator) is administrator
    assert require_helpdesk(technician) is technician
    assert require_admin(administrator) is administrator
    with pytest.raises(HTTPException) as helpdesk_error:
        require_helpdesk(requester)
    assert helpdesk_error.value.status_code == 403
    with pytest.raises(HTTPException) as admin_error:
        require_admin(technician)
    assert admin_error.value.status_code == 403
