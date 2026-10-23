from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.deps import current_user, get_db
from app.main import app
from app.models import Entity, Profile, TicketTypeDefinition, User


@pytest.fixture
async def real_postgres_http():
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
            name: Entity(name=f"Step 5c {name} {suffix}")
            for name in ("A", "B")
        }
        db.add_all(entities.values())
        await db.flush()
        users = {}
        for entity_name, entity in entities.items():
            for role in ("Administrator", "Technician", "Requester"):
                user = User(
                    email=f"step5c-{entity_name}-{role.lower()}-{suffix}@example.test",
                    password_hash="test-only",
                    name=f"{entity_name} {role}",
                    profile=profiles[role],
                    entity=entity,
                )
                users[f"{entity_name}_{role.lower()}"] = user
                db.add(user)
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
                yield {
                    "client": client,
                    "db": db,
                    "entities": entities,
                    "users": users,
                    "state": state,
                }
        finally:
            app.dependency_overrides.pop(current_user, None)
            app.dependency_overrides.pop(get_db, None)
            await db.close()
            if transaction.is_active:
                await transaction.rollback()
            await engine.dispose()


@pytest.mark.asyncio
async def test_requester_can_create_incident_and_service_request(real_postgres_http) -> None:
    client = real_postgres_http["client"]
    user = real_postgres_http["users"]["A_requester"]
    state = real_postgres_http["state"]
    state["user"] = user
    types = {
        ticket_type.itil_class: ticket_type
        for ticket_type in (
            await real_postgres_http["db"].scalars(
                select(TicketTypeDefinition).where(
                    TicketTypeDefinition.itil_class.in_(["INCIDENT", "REQUEST"]),
                    TicketTypeDefinition.is_active.is_(True),
                )
            )
        ).all()
    }
    assert {"INCIDENT", "REQUEST"} <= types.keys()

    for ticket_class in ("INCIDENT", "REQUEST"):
        title = f"Requester {ticket_class} {uuid4().hex}"
        rich_text = '<p>Safe details</p><script>alert(1)</script><img src=x onerror="alert(2)">'
        response = await client.post(
            "/api/v1/tickets",
            json={
                "title": title,
                "description": rich_text,
                "type": ticket_class,
                "ticket_type_id": types[ticket_class].id,
                "custom_fields": {
                    "summary": title,
                    "details": rich_text,
                    "user": real_postgres_http["users"]["B_requester"].id,
                },
            },
        )
        assert response.status_code == 201, response.text
        result = response.json()
        assert result["requester_id"] == user.id
        assert result["custom_fields"]["user"] == user.id
        assert result["custom_fields"]["organisation"] == user.entity_id
        assert result["description"] == "<p>Safe details</p>"
        assert result["custom_fields"]["details"] == "<p>Safe details</p>"


@pytest.mark.asyncio
async def test_rich_text_is_sanitized_for_ticket_subresources_and_templates(real_postgres_http) -> None:
    client = real_postgres_http["client"]
    state = real_postgres_http["state"]
    db = real_postgres_http["db"]
    requester = real_postgres_http["users"]["A_requester"]
    admin = real_postgres_http["users"]["A_administrator"]
    technician = real_postgres_http["users"]["A_technician"]
    incident = await db.scalar(select(TicketTypeDefinition).where(TicketTypeDefinition.itil_class == "INCIDENT"))
    state["user"] = requester
    title = f"Sanitizer coverage {uuid4().hex}"
    response = await client.post(
        "/api/v1/tickets",
        json={
            "title": title,
            "description": "A ticket with enough detail to pass validation.",
            "type": "INCIDENT",
            "ticket_type_id": incident.id,
            "custom_fields": {
                "summary": title,
                "details": "Ordinary field content long enough to use.",
            },
        },
    )
    assert response.status_code == 201, response.text
    ticket_id = response.json()["id"]
    attack = '<p>Safe</p><script>alert(1)</script><svg onload="alert(2)"><text>hidden</text></svg>'
    state["user"] = technician

    for path, body_key, body in (
        (f"/api/v1/tickets/{ticket_id}/followups", "body", attack),
        (f"/api/v1/tickets/{ticket_id}/tasks", "body", attack),
        (f"/api/v1/tickets/{ticket_id}/solutions", "body", attack),
    ):
        resource = await client.post(path, json={body_key: body})
        assert resource.status_code == 201, resource.text
        assert resource.json()[body_key] == "<p>Safe</p>"
        assert "<script" not in resource.text and "onload" not in resource.text

    state["user"] = admin
    template = await client.post(
        "/api/v1/templates",
        json={
            "name": f"Sanitized template {uuid4().hex}",
            "ticket_type_id": incident.id,
            "prefilled": {"details": attack},
        },
    )
    assert template.status_code == 201, template.text
    assert template.json()["prefilled"]["details"] == "<p>Safe</p>"

    notification = await client.post(
        "/api/v1/notification-templates",
        json={
            "event": f"step5c-{uuid4().hex}",
            "subject": "Sanitizer coverage",
            "body_jinja": attack,
        },
    )
    assert notification.status_code == 201, notification.text
    assert notification.json()["body_jinja"] == "<p>Safe</p>"


@pytest.mark.asyncio
async def test_requester_user_field_is_derived_and_helpdesk_on_behalf_still_works(real_postgres_http) -> None:
    client = real_postgres_http["client"]
    state = real_postgres_http["state"]
    incident = await real_postgres_http["db"].scalar(
        select(TicketTypeDefinition).where(TicketTypeDefinition.itil_class == "INCIDENT")
    )
    requester = real_postgres_http["users"]["A_requester"]
    other_same_entity = real_postgres_http["users"]["A_technician"]
    other_entity = real_postgres_http["users"]["B_requester"]

    for forged_user in (other_same_entity.id, other_entity.id):
        state["user"] = requester
        response = await client.post(
            "/api/v1/tickets",
            json={
                "title": f"Derived requester {uuid4().hex}",
                "description": "A requester-created ticket with enough detail.",
                "type": "INCIDENT",
                "ticket_type_id": incident.id,
                "custom_fields": {
                    "summary": "Requester ticket",
                    "details": "Requester-provided details.",
                    "user": forged_user,
                },
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["requester_id"] == requester.id
        assert response.json()["custom_fields"]["user"] == requester.id

    for role in ("technician", "administrator"):
        state["user"] = real_postgres_http["users"][f"A_{role}"]
        response = await client.post(
            "/api/v1/tickets",
            json={
                "title": f"On behalf {role} {uuid4().hex}",
                "description": "A helpdesk-created ticket with enough detail.",
                "type": "INCIDENT",
                "ticket_type_id": incident.id,
                "requester_id": requester.id,
                "custom_fields": {
                    "summary": "Helpdesk ticket",
                    "details": "Helpdesk-provided details.",
                    "user": requester.id,
                },
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["requester_id"] == requester.id

    state["user"] = real_postgres_http["users"]["A_technician"]
    rejected = await client.post(
        "/api/v1/tickets",
        json={
            "title": f"Cross-entity requester {uuid4().hex}",
            "description": "A helpdesk-created ticket with enough detail.",
            "type": "INCIDENT",
            "ticket_type_id": incident.id,
            "requester_id": other_entity.id,
            "custom_fields": {
                "summary": "Invalid requester",
                "details": "Helpdesk-provided details.",
                "user": other_entity.id,
            },
        },
    )
    assert rejected.status_code == 422
