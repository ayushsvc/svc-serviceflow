from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.deps import current_user, get_db
from app.main import app
from app.models import AuditLog, Entity, PriorityMatrix, Profile, SLA, Ticket, TicketTypeDefinition, User
from app.seed import seed_entity_defaults, seed_ticket_type_field_defaults
from app.workers.tasks import _notification_ticket


@pytest.fixture
async def real_postgres_http(monkeypatch):
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        db = AsyncSession(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        suffix = uuid4().hex
        requester_profile = Profile(
            name=f"Release 1 requester {suffix}",
            interface="self_service",
            rights={"ticket:own_only": True},
        )
        staff_profile = Profile(
            name=f"Release 1 staff {suffix}",
            interface="helpdesk",
            rights={"ticket:view_all": True, "ticket:update": True, "ticket:edit": True, "ticket:bulk_edit": True},
        )
        entities = {
            name: Entity(name=f"Release 1 {name} {suffix}")
            for name in ("A", "B")
        }
        db.add_all([requester_profile, staff_profile, *entities.values()])
        await db.flush()

        users = {}
        for entity_name, entity in entities.items():
            for role, profile in (("requester", requester_profile), ("staff", staff_profile)):
                user = User(
                    email=f"release1-{entity_name}-{role}-{suffix}@example.test",
                    password_hash="test-only",
                    name=f"{entity_name} {role}",
                    profile=profile,
                    entity=entity,
                )
                users[f"{entity_name}_{role}"] = user
                db.add(user)
        await db.flush()

        await seed_ticket_type_field_defaults(db)
        for entity in entities.values():
            await seed_entity_defaults(db, entity)
        await db.flush()

        state = {"user": users["A_requester"]}

        async def override_user() -> User:
            return state["user"]

        async def override_db():
            yield db

        monkeypatch.setattr("app.api.tickets.send_ticket_notification.delay", lambda *args, **kwargs: None)
        monkeypatch.setattr("app.api.tickets.dispatch_ticket_update_effects", lambda *args, **kwargs: None)
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


async def _ticket_type(db: AsyncSession, itil_class: str) -> TicketTypeDefinition:
    ticket_type = await db.scalar(
        select(TicketTypeDefinition)
        .where(
            TicketTypeDefinition.itil_class == itil_class,
            TicketTypeDefinition.is_active.is_(True),
            TicketTypeDefinition.entity_id.is_(None),
        )
        .order_by(TicketTypeDefinition.id)
    )
    assert ticket_type is not None, f"Missing seeded {itil_class} ticket type"
    return ticket_type


async def _create_requester_ticket(context, *, priority: str = "medium", description: str = "<p>Normal ticket details</p>"):
    client = context["client"]
    db = context["db"]
    incident = await _ticket_type(db, "INCIDENT")
    return await client.post(
        "/api/v1/tickets",
        json={
            "title": f"Release 1 {priority} {uuid4().hex}",
            "description": description,
            "type": "INCIDENT",
            "ticket_type_id": incident.id,
            "priority": priority,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requester_value", "stored_priority", "response_minutes", "resolution_minutes", "label"),
    [
        ("low", 5, 480, 5760, "Low"),
        ("medium", 3, 60, 1440, "Medium"),
        ("high", 2, 30, 480, "High"),
        ("critical", 1, 15, 240, "Critical"),
    ],
)
async def test_requester_priority_selects_stored_priority_sla_and_label(
    real_postgres_http,
    requester_value,
    stored_priority,
    response_minutes,
    resolution_minutes,
    label,
) -> None:
    result = await _create_requester_ticket(real_postgres_http, priority=requester_value)
    assert result.status_code == 201, result.text
    ticket_data = result.json()
    assert ticket_data["priority"] == stored_priority
    assert ticket_data["priority_label"] == label
    assert (ticket_data["urgency"], ticket_data["impact"]) == (stored_priority, stored_priority)

    sla = await real_postgres_http["db"].get(SLA, ticket_data["sla_id"])
    assert sla is not None
    assert (sla.tto_minutes, sla.ttr_minutes) == (response_minutes, resolution_minutes)


@pytest.mark.asyncio
async def test_requester_priority_rejects_unknown_numeric_and_urgency_impact(real_postgres_http) -> None:
    for priority in ("urgent", 5):
        response = await _create_requester_ticket(real_postgres_http, priority=priority)
        assert response.status_code == 422

    incident = await _ticket_type(real_postgres_http["db"], "INCIDENT")
    for fields in ({"urgency": 1}, {"impact": 1}, {"custom_fields": {"urgency": 1}}, {"custom_fields": {"impact": 1}}):
        payload = {
            "title": f"Requester forbidden field {uuid4().hex}",
            "description": "A valid description for testing",
            "type": "INCIDENT",
            "ticket_type_id": incident.id,
            "priority": "low",
            **fields,
        }
        response = await real_postgres_http["client"].post("/api/v1/tickets", json=payload)
        assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_requester_form_schema_contains_only_four_editable_fields(real_postgres_http) -> None:
    requester = real_postgres_http["users"]["A_requester"]
    real_postgres_http["state"]["user"] = requester
    incident = await _ticket_type(real_postgres_http["db"], "INCIDENT")

    response = await real_postgres_http["client"].get(f"/api/v1/ticket-types/{incident.id}/form")
    assert response.status_code == 200, response.text
    schema = response.json()
    fields = [field for page in schema["pages"].values() for field in page if field["editable"]]
    assert {field["key"] for field in fields} == {"summary", "details", "attachments", "priority"}
    priority_field = next(field for field in fields if field["key"] == "priority")
    assert priority_field["options"]["choices"] == ["low", "medium", "high", "critical"]
    staff_only_types = (
        await real_postgres_http["db"].scalars(
            select(TicketTypeDefinition).where(
                TicketTypeDefinition.itil_class.in_(("PROBLEM", "CHANGE")),
                TicketTypeDefinition.entity_id.is_(None),
                TicketTypeDefinition.is_active.is_(True),
            )
        )
    ).all()
    for staff_only_type in staff_only_types:
        hidden = await real_postgres_http["client"].get(f"/api/v1/ticket-types/{staff_only_type.id}/form")
        assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_editing_priority_matrix_does_not_change_requester_choice(real_postgres_http) -> None:
    db = real_postgres_http["db"]
    entity = real_postgres_http["entities"]["A"]
    matrix = await db.scalar(select(PriorityMatrix).where(PriorityMatrix.entity_id == entity.id))
    if matrix is None:
        matrix = PriorityMatrix(entity_id=entity.id, matrix=[[1] * 5 for _ in range(5)])
        db.add(matrix)
    else:
        matrix.matrix = [[1] * 5 for _ in range(5)]
    await db.flush()

    response = await _create_requester_ticket(real_postgres_http, priority="low")
    assert response.status_code == 201, response.text
    assert response.json()["priority"] == 5
    assert (response.json()["urgency"], response.json()["impact"]) == (5, 5)


@pytest.mark.asyncio
async def test_staff_direct_priority_and_ticket_type_update_are_audited(real_postgres_http) -> None:
    created = await _create_requester_ticket(real_postgres_http, priority="low")
    assert created.status_code == 201, created.text
    ticket_id = created.json()["id"]
    service_request = await _ticket_type(real_postgres_http["db"], "REQUEST")
    real_postgres_http["state"]["user"] = real_postgres_http["users"]["A_staff"]

    response = await real_postgres_http["client"].patch(
        f"/api/v1/tickets/{ticket_id}",
        json={"priority": 2, "ticket_type_id": service_request.id},
    )
    assert response.status_code == 200, response.text
    ticket_data = response.json()
    assert (ticket_data["priority"], ticket_data["urgency"], ticket_data["impact"]) == (2, 2, 2)
    assert ticket_data["ticket_type_id"] == service_request.id
    sla = await real_postgres_http["db"].get(SLA, ticket_data["sla_id"])
    assert sla is not None and (sla.tto_minutes, sla.ttr_minutes) == (30, 480)
    audit = await real_postgres_http["db"].scalar(
        select(AuditLog).where(AuditLog.entity == "ticket", AuditLog.entity_id == ticket_id, AuditLog.action == "updated")
    )
    assert audit is not None
    assert audit.diff["priority"] == {"old": 5, "new": 2}
    assert audit.diff["ticket_type_id"] == {"old": created.json()["ticket_type_id"], "new": service_request.id}
    assert ticket_data["priority_label"] == "High"


@pytest.mark.asyncio
async def test_staff_urgency_impact_use_entity_matrix_and_bulk_priority_is_direct(real_postgres_http) -> None:
    db = real_postgres_http["db"]
    entity = real_postgres_http["entities"]["A"]
    matrix = await db.scalar(select(PriorityMatrix).where(PriorityMatrix.entity_id == entity.id))
    values = [[5] * 5 for _ in range(5)]
    if matrix is None:
        matrix = PriorityMatrix(entity_id=entity.id, matrix=values)
        db.add(matrix)
    else:
        matrix.matrix = values
    await db.flush()

    created = await _create_requester_ticket(real_postgres_http, priority="medium")
    assert created.status_code == 201, created.text
    ticket_id = created.json()["id"]
    real_postgres_http["state"]["user"] = real_postgres_http["users"]["A_staff"]
    matrix_update = await real_postgres_http["client"].patch(
        f"/api/v1/tickets/{ticket_id}",
        json={"urgency": 2, "impact": 4},
    )
    assert matrix_update.status_code == 200, matrix_update.text
    assert (matrix_update.json()["priority"], matrix_update.json()["urgency"], matrix_update.json()["impact"]) == (5, 2, 4)

    bulk = await real_postgres_http["client"].post(
        "/api/v1/tickets/bulk",
        json={"ticket_ids": [ticket_id], "action": "priority", "value": 1},
    )
    assert bulk.status_code == 200, bulk.text
    updated = await real_postgres_http["client"].get(f"/api/v1/tickets/{ticket_id}")
    assert updated.status_code == 200, updated.text
    data = updated.json()
    assert (data["priority"], data["urgency"], data["impact"]) == (1, 1, 1)
    sla = await db.get(SLA, data["sla_id"])
    assert sla is not None and (sla.tto_minutes, sla.ttr_minutes) == (15, 240)


@pytest.mark.asyncio
async def test_priority_labels_are_role_aware_for_requester_and_staff(real_postgres_http) -> None:
    db = real_postgres_http["db"]
    incident = await _ticket_type(db, "INCIDENT")
    requester = real_postgres_http["users"]["A_requester"]
    real_postgres_http["state"]["user"] = requester

    ticket_response = await real_postgres_http["client"].post(
        "/api/v1/tickets",
        json={
            "title": "Requester low priority label",
            "description": "A valid description with enough detail.",
            "type": "INCIDENT",
            "ticket_type_id": incident.id,
            "priority": "low",
        },
    )
    assert ticket_response.status_code == 201, ticket_response.text
    assert ticket_response.json()["priority"] == 5
    assert ticket_response.json()["priority_label"] == "Low"
    requester_list = await real_postgres_http["client"].get("/api/v1/tickets")
    assert [item["label"] for item in requester_list.json()["priority_options"]] == [
        "Critical", "High", "Medium", "Low", "Low",
    ]
    stored = await db.get(Ticket, ticket_response.json()["id"])
    assert stored is not None
    real_postgres_http["state"]["user"] = real_postgres_http["users"]["A_staff"]
    staff_response = await real_postgres_http["client"].get(f"/api/v1/tickets/{stored.id}")
    assert staff_response.status_code == 200
    assert staff_response.json()["priority_label"] == "Low"
    staff_list = await real_postgres_http["client"].get("/api/v1/tickets")
    assert [item["label"] for item in staff_list.json()["priority_options"]] == [
        "Critical", "High", "Medium", "Low-Medium", "Low",
    ]
    assert _notification_ticket(stored, requester=True)["priority"] == "Low"
    assert _notification_ticket(stored, requester=False)["priority"] == 5


@pytest.mark.asyncio
async def test_requester_cannot_update_and_foreign_entity_cannot_read_ticket(real_postgres_http) -> None:
    created = await _create_requester_ticket(real_postgres_http, priority="low")
    assert created.status_code == 201, created.text
    ticket_id = created.json()["id"]

    denied = await real_postgres_http["client"].patch(f"/api/v1/tickets/{ticket_id}", json={"priority": 1})
    assert denied.status_code == 403

    real_postgres_http["state"]["user"] = real_postgres_http["users"]["B_requester"]
    foreign = await real_postgres_http["client"].get(f"/api/v1/tickets/{ticket_id}")
    assert foreign.status_code == 404
    listed = await real_postgres_http["client"].get("/api/v1/tickets")
    assert listed.status_code == 200
    assert all(ticket["id"] != ticket_id for ticket in listed.json()["items"])


@pytest.mark.asyncio
async def test_priority_four_uses_seeded_low_medium_sla(real_postgres_http) -> None:
    db = real_postgres_http["db"]
    staff = real_postgres_http["users"]["A_staff"]
    real_postgres_http["state"]["user"] = staff
    incident = await _ticket_type(db, "INCIDENT")
    response = await real_postgres_http["client"].post(
        "/api/v1/tickets",
        json={
            "title": f"Priority four {uuid4().hex}",
            "description": "A staff-created priority four test ticket",
            "type": "INCIDENT",
            "ticket_type_id": incident.id,
            "priority": 4,
            "requester_id": real_postgres_http["users"]["A_requester"].id,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["priority"] == 4
    assert (response.json()["urgency"], response.json()["impact"]) == (4, 4)
    assert response.json()["priority_label"] == "Low-Medium"
    sla = await db.get(SLA, response.json()["sla_id"])
    assert sla is not None and (sla.tto_minutes, sla.ttr_minutes) == (240, 2880)
    real_postgres_http["state"]["user"] = real_postgres_http["users"]["A_requester"]
    requester_view = await real_postgres_http["client"].get(f"/api/v1/tickets/{response.json()['id']}")
    assert requester_view.status_code == 200, requester_view.text
    assert requester_view.json()["priority_label"] == "Low"


@pytest.mark.asyncio
async def test_requester_description_is_sanitized(real_postgres_http) -> None:
    attack = '<p>Safe details</p><script>alert(1)</script><img src=x onerror="alert(2)">'
    response = await _create_requester_ticket(real_postgres_http, priority="medium", description=attack)
    assert response.status_code == 201, response.text
    assert response.json()["description"] == "<p>Safe details</p>"
    assert response.json()["custom_fields"]["details"] == "<p>Safe details</p>"
    assert "<script" not in response.text and "onerror" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("description", ["<p><br></p>", "123456789"])
async def test_ticket_description_minimum_uses_sanitized_text_on_create_and_update(
    real_postgres_http,
    description,
) -> None:
    client = real_postgres_http["client"]
    db = real_postgres_http["db"]
    requester = real_postgres_http["users"]["A_requester"]
    staff = real_postgres_http["users"]["A_staff"]
    incident = await _ticket_type(db, "INCIDENT")

    for user, role in ((requester, "Requester"), (staff, "Staff")):
        real_postgres_http["state"]["user"] = user
        response = await client.post(
            "/api/v1/tickets",
            json={
                "title": f"Short description {role} {uuid4().hex}",
                "description": description,
                "type": "INCIDENT",
                "ticket_type_id": incident.id,
                "priority": "medium" if role == "Requester" else 3,
                **({"requester_id": requester.id} if role == "Staff" else {}),
            },
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "Description must contain at least 10 characters of text."

    real_postgres_http["state"]["user"] = requester
    created = await _create_requester_ticket(
        real_postgres_http,
        priority="medium",
        description="A sufficiently detailed initial description.",
    )
    assert created.status_code == 201, created.text
    real_postgres_http["state"]["user"] = staff
    updated = await client.patch(
        f"/api/v1/tickets/{created.json()['id']}",
        json={"description": description},
    )
    assert updated.status_code == 422, updated.text
    assert updated.json()["detail"] == "Description must contain at least 10 characters of text."


@pytest.mark.asyncio
async def test_printer_description_is_accepted_on_create_and_update(real_postgres_http) -> None:
    created = await _create_requester_ticket(
        real_postgres_http,
        priority="medium",
        description="Printer not working",
    )
    assert created.status_code == 201, created.text
    assert created.json()["description"] == "Printer not working"

    real_postgres_http["state"]["user"] = real_postgres_http["users"]["A_staff"]
    updated = await real_postgres_http["client"].patch(
        f"/api/v1/tickets/{created.json()['id']}",
        json={"description": "Printer not working"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["description"] == "Printer not working"


@pytest.mark.asyncio
async def test_script_payload_is_sanitized_on_update(real_postgres_http) -> None:
    created = await _create_requester_ticket(real_postgres_http, priority="medium")
    assert created.status_code == 201, created.text
    real_postgres_http["state"]["user"] = real_postgres_http["users"]["A_staff"]
    attack = '<p>Safe details</p><script>alert(1)</script><img src=x onerror="alert(2)">'
    updated = await real_postgres_http["client"].patch(
        f"/api/v1/tickets/{created.json()['id']}",
        json={"description": attack},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["description"] == "<p>Safe details</p>"
    assert "<script" not in updated.text and "onerror" not in updated.text
