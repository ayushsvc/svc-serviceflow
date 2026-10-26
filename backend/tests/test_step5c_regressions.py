from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.deps import current_user, get_db
from app.main import app
from app.models import (
    BusinessRule,
    Entity,
    FieldDefinition,
    Group,
    GroupMember,
    NotificationTemplate,
    Profile,
    SLA,
    TicketActor,
    Ticket,
    TicketTypeDefinition,
    TicketTypeField,
    User,
    WorkflowApproval,
)
from app.seed import seed_entity_defaults


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
async def test_requester_ticket_type_catalog_exposes_only_portal_types(real_postgres_http) -> None:
    client = real_postgres_http["client"]
    state = real_postgres_http["state"]
    db = real_postgres_http["db"]
    requester = real_postgres_http["users"]["A_requester"]
    state["user"] = requester

    response = await client.get("/api/v1/ticket-types")
    assert response.status_code == 200, response.text
    ticket_types = response.json()
    assert {ticket_type["itil_class"] for ticket_type in ticket_types} == {"INCIDENT", "REQUEST"}
    assert all(
        "Requester" in field["visible_to_roles"] and field["show_on_portal"]
        for ticket_type in ticket_types
        for field in ticket_type["fields"]
    )

    for ticket_type in ticket_types:
        form = await client.get(f"/api/v1/ticket-types/{ticket_type['id']}/form")
        assert form.status_code == 200, form.text

    non_portal_type = await db.scalar(
        select(TicketTypeDefinition).where(
            TicketTypeDefinition.itil_class.in_(["PROBLEM", "CHANGE"]),
            TicketTypeDefinition.is_active.is_(True),
        )
    )
    if non_portal_type is not None:
        form = await client.get(f"/api/v1/ticket-types/{non_portal_type.id}/form")
        assert form.status_code == 404
        create = await client.post(
            "/api/v1/tickets",
            json={
                "title": "Self-service non-portal type",
                "description": "This ticket type is reserved for service desk use.",
                "type": non_portal_type.itil_class,
                "ticket_type_id": non_portal_type.id,
            },
        )
        assert create.status_code == 422


@pytest.mark.asyncio
async def test_technician_ticket_queue_is_limited_to_assignee_and_member_teams(real_postgres_http) -> None:
    client = real_postgres_http["client"]
    db = real_postgres_http["db"]
    state = real_postgres_http["state"]
    entity = real_postgres_http["entities"]["A"]
    technician = real_postgres_http["users"]["A_technician"]
    requester = real_postgres_http["users"]["A_requester"]
    team_a = Group(name=f"Queue A {uuid4().hex}", entity_id=entity.id)
    team_b = Group(name=f"Queue B {uuid4().hex}", entity_id=entity.id)
    db.add_all([team_a, team_b])
    await db.flush()
    type_a = TicketTypeDefinition(
        name=f"Queue type A {uuid4().hex}",
        itil_class="INCIDENT",
        entity_id=entity.id,
        default_team_id=team_a.id,
    )
    type_b = TicketTypeDefinition(
        name=f"Queue type B {uuid4().hex}",
        itil_class="INCIDENT",
        entity_id=entity.id,
        default_team_id=team_b.id,
    )
    db.add_all([type_a, type_b])
    await db.flush()
    other_technician = User(
        email=f"step5c-other-technician-{uuid4().hex}@example.test",
        password_hash="test-only",
        name="Another Technician",
        profile=technician.profile,
        entity=entity,
    )
    db.add(other_technician)
    await db.flush()
    db.add(GroupMember(group_id=team_a.id, user_id=technician.id))

    assigned_to_technician = Ticket(
        title="Assigned directly to technician",
        description="Visible because this ticket is assigned directly.",
        entity_id=entity.id,
        requester_id=requester.id,
        assignee_id=technician.id,
    )
    assigned_to_team = Ticket(
        title="Assigned to technician team",
        description="Visible because the technician belongs to the assigned team.",
        entity_id=entity.id,
        requester_id=requester.id,
        ticket_type_id=type_b.id,
    )
    assigned_by_type_to_team = Ticket(
        title="Ticket type routes to technician team",
        description="Visible because the ticket type has a member team.",
        entity_id=entity.id,
        requester_id=requester.id,
        ticket_type_id=type_a.id,
    )
    not_assigned_to_technician = Ticket(
        title="Assigned to another technician",
        description="Not visible because this ticket belongs to another technician team.",
        entity_id=entity.id,
        requester_id=requester.id,
        assignee_id=other_technician.id,
        ticket_type_id=type_b.id,
    )
    db.add_all([
        assigned_to_technician,
        assigned_to_team,
        assigned_by_type_to_team,
        not_assigned_to_technician,
    ])
    await db.flush()
    db.add(TicketActor(
        ticket_id=assigned_to_team.id,
        actor_type="GROUP",
        actor_id=team_a.id,
        role="ASSIGNEE",
    ))
    await db.flush()

    state["user"] = technician
    listed = await client.get("/api/v1/tickets?page_size=100")
    assert listed.status_code == 200, listed.text
    visible_ids = {ticket["id"] for ticket in listed.json()["items"]}
    assert visible_ids == {
        assigned_to_technician.id,
        assigned_to_team.id,
        assigned_by_type_to_team.id,
    }

    hidden_detail = await client.get(f"/api/v1/tickets/{not_assigned_to_technician.id}")
    hidden_timeline = await client.get(f"/api/v1/tickets/{not_assigned_to_technician.id}/timeline")
    assert hidden_detail.status_code == 404
    assert hidden_timeline.status_code == 404

    team_counts = await client.get("/api/v1/tickets/team-counts")
    assert team_counts.status_code == 200, team_counts.text
    assert sum(item["count"] for item in team_counts.json()) == 2
    queue_counts = await client.get("/api/v1/tickets/queue-counts")
    assert queue_counts.status_code == 200, queue_counts.text
    assert queue_counts.json() == {"all": 3, "mine": 1, "teams": 2, "unassigned": 0}

    state["user"] = real_postgres_http["users"]["A_administrator"]
    administrator_list = await client.get("/api/v1/tickets?page_size=100")
    assert administrator_list.status_code == 200, administrator_list.text
    assert administrator_list.json()["total"] == 4


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
    db = real_postgres_http["db"]
    created_ticket = await db.get(Ticket, ticket_id)
    assert created_ticket is not None
    created_ticket.assignee_id = technician.id
    await db.flush()
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
            "prefilled": {"details": attack, "source": "EMAIL", "agent": technician.id},
            "show_on_portal": True,
        },
    )
    assert template.status_code == 201, template.text
    assert template.json()["prefilled"]["details"] == "<p>Safe</p>"
    state["user"] = requester
    requester_templates = await client.get("/api/v1/templates")
    assert requester_templates.status_code == 200, requester_templates.text
    requester_template = next(
        item for item in requester_templates.json() if item["id"] == template.json()["id"]
    )
    assert requester_template["prefilled"] == {"details": "<p>Safe</p>"}
    assert requester_template["team_id"] is None
    state["user"] = admin

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

    recurring = await client.post(
        "/api/v1/recurring",
        json={
            "title": f"Sanitized recurring {uuid4().hex}",
            "description": attack,
            "cron_expr": "0 9 * * 1",
            "next_run_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "requester_id": requester.id,
        },
    )
    assert recurring.status_code == 201, recurring.text
    assert recurring.json()["description"] == "<p>Safe</p>"


@pytest.mark.asyncio
async def test_form_schema_and_submitted_fields_are_role_filtered(real_postgres_http) -> None:
    client = real_postgres_http["client"]
    state = real_postgres_http["state"]
    db = real_postgres_http["db"]
    incident = await db.scalar(select(TicketTypeDefinition).where(TicketTypeDefinition.itil_class == "INCIDENT"))
    mappings = list(
        (
            await db.scalars(
                select(TicketTypeField)
                .where(TicketTypeField.ticket_type_id == incident.id)
                .order_by(TicketTypeField.order)
            )
        ).all()
    )
    definitions = {
        mapping.field_definition_id: await db.get(FieldDefinition, mapping.field_definition_id)
        for mapping in mappings
    }
    core_keys = {"summary", "details", "attachments", "priority"}
    extras = [mapping for mapping in mappings if definitions[mapping.field_definition_id].key not in core_keys]
    assert len(extras) >= 2
    hidden_key = "urgency"
    for mapping in mappings:
        if definitions[mapping.field_definition_id].key in core_keys:
            mapping.visible_to_roles = ["Administrator", "Technician", "Requester"]
            mapping.editable_by_roles = ["Administrator", "Technician", "Requester"]
            mapping.show_on_portal = True
            continue
        mapping.visible_to_roles = ["Administrator", "Technician"] if mapping is extras[0] else ["Administrator"]
        mapping.editable_by_roles = ["Administrator", "Technician"]
        mapping.show_on_portal = False
        if mapping.field_definition_id == extras[1].field_definition_id:
            mapping.required = False

    forms = {}
    for role in ("administrator", "technician", "requester"):
        state["user"] = real_postgres_http["users"][f"A_{role}"]
        response = await client.get(f"/api/v1/ticket-types/{incident.id}/form")
        assert response.status_code == 200, response.text
        form_fields = [
            field
            for page in response.json()["pages"].values()
            for field in page
        ]
        forms[role] = {field["key"]: field for field in form_fields}
        assert all(field["editable"] == (role != "requester" or field["key"] in core_keys) for field in form_fields)

    assert len(forms["administrator"]) > len(forms["technician"]) > len(forms["requester"])
    assert set(forms["requester"]) == core_keys
    assert hidden_key not in forms["requester"] and hidden_key not in forms["technician"]

    state["user"] = real_postgres_http["users"]["A_requester"]
    rejected = await client.post(
        "/api/v1/tickets",
        json={
            "title": f"Hidden field rejection {uuid4().hex}",
            "description": "A requester ticket with enough details.",
            "type": "INCIDENT",
            "ticket_type_id": incident.id,
            "custom_fields": {
                "summary": "Requester ticket",
                "details": "Requester details.",
                hidden_key: "must not be accepted",
            },
        },
    )
    assert rejected.status_code == 422
    assert hidden_key in rejected.json()["detail"]


@pytest.mark.asyncio
async def test_select_and_multiselect_choices_are_enforced_on_create_and_update(real_postgres_http) -> None:
    client = real_postgres_http["client"]
    state = real_postgres_http["state"]
    db = real_postgres_http["db"]
    state["user"] = real_postgres_http["users"]["A_administrator"]
    incident = await db.scalar(select(TicketTypeDefinition).where(TicketTypeDefinition.itil_class == "INCIDENT"))
    source = await db.scalar(select(FieldDefinition).where(FieldDefinition.key == "source"))
    source.options = {"choices": ["PORTAL", "EMAIL"]}
    source_mapping = await db.get(TicketTypeField, (incident.id, source.id))
    source_mapping.visible_to_roles = ["Administrator", "Technician", "Requester"]
    source_mapping.editable_by_roles = ["Administrator", "Technician", "Requester"]
    source_mapping.show_on_portal = True

    multi_key = f"step5c_multi_{uuid4().hex}"
    multi = FieldDefinition(
        key=multi_key,
        label="Regression multiselect",
        type="multiselect",
        options={"choices": ["PORTAL", "EMAIL"]},
        is_system=False,
    )
    db.add(multi)
    await db.flush()
    db.add(
        TicketTypeField(
            ticket_type_id=incident.id,
            field_definition_id=multi.id,
            visible_to_roles=["Administrator"],
            editable_by_roles=["Administrator"],
            show_on_portal=False,
        )
    )
    await db.flush()

    async def create_ticket(source_value: str, multi_value: list[str]):
        title = f"Choice validation {uuid4().hex}"
        return await client.post(
            "/api/v1/tickets",
            json={
                "title": title,
                "description": "A valid ticket description for choice validation.",
                "type": "INCIDENT",
                "ticket_type_id": incident.id,
                "custom_fields": {
                    "summary": title,
                    "details": "Valid details for choice validation.",
                    "source": source_value,
                    "user": real_postgres_http["users"]["A_requester"].id,
                    multi_key: multi_value,
                },
            },
        )

    invalid_select = await create_ticket("__invalid__", ["PORTAL"])
    assert invalid_select.status_code == 422
    valid_response = await create_ticket("PORTAL", ["PORTAL", "EMAIL"])
    assert valid_response.status_code == 201, valid_response.text
    ticket_id = valid_response.json()["id"]
    assert valid_response.json()["custom_fields"][multi_key] == ["PORTAL", "EMAIL"]

    invalid_update = await client.patch(
        f"/api/v1/tickets/{ticket_id}",
        json={"custom_fields": {"source": "__invalid__"}},
    )
    assert invalid_update.status_code == 422
    invalid_multi_update = await client.patch(
        f"/api/v1/tickets/{ticket_id}",
        json={"custom_fields": {multi_key: ["PORTAL", "INVALID"]}},
    )
    assert invalid_multi_update.status_code == 422
    valid_update = await client.patch(
        f"/api/v1/tickets/{ticket_id}",
        json={"custom_fields": {"source": "EMAIL", multi_key: ["EMAIL"]}},
    )
    assert valid_update.status_code == 200, valid_update.text
    assert valid_update.json()["custom_fields"]["source"] == "EMAIL"


@pytest.mark.asyncio
async def test_default_seed_is_idempotent_and_preserves_existing_rows(real_postgres_http) -> None:
    db = real_postgres_http["db"]
    entity = real_postgres_http["entities"]["A"]
    await seed_entity_defaults(db, entity)
    slas = list(
        (
            await db.scalars(
                select(SLA).where(SLA.entity_id == entity.id, SLA.name.like(f"{entity.name} Priority % SLA"))
            )
        ).all()
    )
    templates = list(
        (
            await db.scalars(select(NotificationTemplate).where(NotificationTemplate.entity_id == entity.id))
        ).all()
    )
    rules = list(
        (
            await db.scalars(select(BusinessRule).where(BusinessRule.entity_id == entity.id))
        ).all()
    )
    assert len(slas) == 5
    assert len(templates) == 5
    assert len(rules) == 1
    original_target = slas[0].tto_minutes
    original_body = templates[0].body_jinja
    slas[0].tto_minutes = 999
    templates[0].body_jinja = "Preserve existing customized content."
    await seed_entity_defaults(db, entity)
    assert len(
        list(
            (
                await db.scalars(
                    select(SLA).where(SLA.entity_id == entity.id, SLA.name.like(f"{entity.name} Priority % SLA"))
                )
            ).all()
        )
    ) == 5
    assert (await db.scalar(select(SLA).where(SLA.id == slas[0].id))).tto_minutes == 999
    assert (await db.scalar(select(NotificationTemplate).where(NotificationTemplate.id == templates[0].id))).body_jinja == "Preserve existing customized content."
    assert original_target > 0 and original_body


@pytest.mark.asyncio
async def test_priority_selects_default_sla_and_routes_priority_one_to_default_team(real_postgres_http) -> None:
    client = real_postgres_http["client"]
    state = real_postgres_http["state"]
    db = real_postgres_http["db"]
    entity = real_postgres_http["entities"]["A"]
    await seed_entity_defaults(db, entity)
    state["user"] = real_postgres_http["users"]["A_requester"]
    incident = await db.scalar(select(TicketTypeDefinition).where(TicketTypeDefinition.itil_class == "INCIDENT"))
    title = f"Priority one defaults {uuid4().hex}"
    response = await client.post(
        "/api/v1/tickets",
        json={
            "title": title,
            "description": "A high-priority ticket with enough details.",
            "type": "INCIDENT",
            "priority": "critical",
            "ticket_type_id": incident.id,
            "custom_fields": {"summary": title, "details": "High-priority details."},
        },
    )
    assert response.status_code == 201, response.text
    ticket = response.json()
    expected_sla = await db.scalar(
        select(SLA).where(SLA.entity_id == entity.id, SLA.name == f"{entity.name} Priority 1 SLA")
    )
    default_team = await db.scalar(select(Group).where(Group.entity_id == entity.id, Group.name == f"{entity.name} Service Desk"))
    assert ticket["priority"] == 1
    assert ticket["sla_id"] == expected_sla.id
    assert ticket["tto_due_at"] and ticket["ttr_due_at"]
    team_assignment = await db.scalar(
        select(TicketActor).where(
            TicketActor.ticket_id == ticket["id"],
            TicketActor.actor_type == "GROUP",
            TicketActor.actor_id == default_team.id,
            TicketActor.role == "ASSIGNEE",
        )
    )
    assert team_assignment is not None


def test_startup_requires_jwt_secret_even_in_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(ValidationError, match="JWT_SECRET is required from the environment in every mode"):
        Settings(_env_file=None, debug=True)


@pytest.mark.asyncio
async def test_configurable_ticket_workflow_requires_approval_before_transition(real_postgres_http) -> None:
    client = real_postgres_http["client"]
    db = real_postgres_http["db"]
    state = real_postgres_http["state"]
    requester = real_postgres_http["users"]["A_requester"]
    technician = real_postgres_http["users"]["A_technician"]
    administrator = real_postgres_http["users"]["A_administrator"]
    incident = await real_postgres_http["db"].scalar(
        select(TicketTypeDefinition).where(TicketTypeDefinition.itil_class == "INCIDENT")
    )

    state["user"] = requester
    title = f"Approval workflow {uuid4().hex}"
    created = await client.post(
        "/api/v1/tickets",
        json={
            "title": title,
            "description": "A ticket to verify configurable state approvals.",
            "type": "INCIDENT",
            "ticket_type_id": incident.id,
            "custom_fields": {
                "summary": title,
                "details": "Approval workflow verification.",
            },
        },
    )
    assert created.status_code == 201, created.text
    ticket_id = created.json()["id"]
    created_ticket = await db.get(Ticket, ticket_id)
    assert created_ticket is not None
    created_ticket.assignee_id = technician.id
    await db.flush()

    state["user"] = administrator
    workflow_response = await client.put(
        "/api/v1/workflows/ticket",
        json={
            "states": ["NEW", "RESOLVED", "CLOSED"],
            "transitions": {"NEW": ["RESOLVED"], "RESOLVED": ["CLOSED"]},
            "approval_requirements": {"RESOLVED": 1},
            "is_active": True,
        },
    )
    assert workflow_response.status_code == 200, workflow_response.text

    blocked = await client.patch(f"/api/v1/tickets/{ticket_id}", json={"status": "RESOLVED"})
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "Required approvals are incomplete"

    approval_response = await client.post(
        "/api/v1/approvals",
        json={
            "record_type": "ticket",
            "record_id": ticket_id,
            "transition_to": "RESOLVED",
            "approver_user_id": technician.id,
            "step": 1,
        },
    )
    assert approval_response.status_code == 201, approval_response.text
    approval_id = approval_response.json()["id"]

    state["user"] = real_postgres_http["users"]["B_technician"]
    cross_entity_decision = await client.post(
        f"/api/v1/approvals/{approval_id}/decision",
        json={"decision": "APPROVED"},
    )
    assert cross_entity_decision.status_code == 404

    state["user"] = technician
    pending = await client.get("/api/v1/approvals/mine")
    assert pending.status_code == 200, pending.text
    assert pending.json()[0]["id"] == approval_id
    decision = await client.post(
        f"/api/v1/approvals/{approval_id}/decision",
        json={"decision": "APPROVED", "note": "Reviewed and approved."},
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "APPROVED"

    transitioned = await client.patch(f"/api/v1/tickets/{ticket_id}", json={"status": "RESOLVED"})
    assert transitioned.status_code == 200, transitioned.text
    assert transitioned.json()["status"] == "RESOLVED"
    approval = await real_postgres_http["db"].get(WorkflowApproval, approval_id)
    assert approval is not None and approval.decided_by == technician.id


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
