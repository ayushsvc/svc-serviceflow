import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.config import get_settings
from app.deps import current_user, get_db
from app.main import app
from app.models import (
    AuditLog,
    Entity,
    Group,
    GroupMember,
    Profile,
    TicketTemplate,
    TicketTemplateUsage,
    TicketTypeDefinition,
    User,
)
from app.seed import DEFAULT_TICKET_TEMPLATES, seed_ticket_templates


@pytest.fixture
async def template_http():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    connection = await engine.connect()
    transaction = await connection.begin()
    db = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    suffix = uuid4().hex
    try:
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
        entities = {
            key: Entity(name=f"Template picker {key} {suffix}")
            for key in ("A", "B")
        }
        db.add_all(entities.values())
        await db.flush()
        users = {
            f"{key}_{role.lower()}": User(
                email=f"template-picker-{key}-{role.lower()}-{suffix}@example.test",
                password_hash="test-only",
                name=f"Template {key} {role}",
                profile=profiles[role],
                entity=entities[key],
            )
            for key in ("A", "B")
            for role in ("Administrator", "Technician", "Requester")
        }
        db.add_all(users.values())
        await db.flush()
        teams = {
            "A": Group(name=f"Template team A {suffix}", entity_id=entities["A"].id),
            "A_other": Group(name=f"Template team A other {suffix}", entity_id=entities["A"].id),
            "B": Group(name=f"Template team B {suffix}", entity_id=entities["B"].id),
        }
        db.add_all(teams.values())
        await db.flush()
        db.add_all(
            (
                GroupMember(group_id=teams["A"].id, user_id=users["A_technician"].id),
                GroupMember(group_id=teams["B"].id, user_id=users["B_technician"].id),
            )
        )
        ticket_types = {}
        for class_name in ("INCIDENT", "REQUEST", "PROBLEM", "CHANGE"):
            ticket_types[class_name] = await db.scalar(
                select(TicketTypeDefinition)
                .where(
                    TicketTypeDefinition.itil_class == class_name,
                    TicketTypeDefinition.is_active.is_(True),
                    TicketTypeDefinition.entity_id.is_(None),
                )
                .order_by(TicketTypeDefinition.id)
                .limit(1)
            )
            assert ticket_types[class_name] is not None
        ids = {key: user.id for key, user in users.items()}
        entity_ids = {key: entity.id for key, entity in entities.items()}
        team_ids = {key: team.id for key, team in teams.items()}
        type_ids = {key: ticket_type.id for key, ticket_type in ticket_types.items()}
        users_by_id = {
            user.id: user for user in users.values()
        }

        async def user_override(request: Request) -> User:
            return users_by_id[int(request.headers["x-test-user"])]

        async def db_override():
            yield db

        app.dependency_overrides[current_user] = user_override
        app.dependency_overrides[get_db] = db_override
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield {
                "client": client,
                "engine": engine,
                "db": db,
                "suffix": suffix,
                "ids": ids,
                "entity_ids": entity_ids,
                "team_ids": team_ids,
                "type_ids": type_ids,
            }
    finally:
        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(get_db, None)
        await db.close()
        if transaction.is_active:
            await transaction.rollback()
        await connection.close()
        await engine.dispose()


def auth(template_http, user: str) -> dict[str, str]:
    return {"x-test-user": str(template_http["ids"][user])}


async def add_template(
    template_http,
    *,
    entity: str = "A",
    name: str | None = None,
    ticket_type: str = "INCIDENT",
    team: str | None = None,
    show_on_portal: bool = True,
    prefilled: dict | None = None,
    description: str = "A helpful one-line description.",
) -> int:
    db = template_http["db"]
    template = TicketTemplate(
        entity_id=template_http["entity_ids"][entity],
        name=name or f"Picker template {uuid4().hex}",
        description=description,
        ticket_type_id=template_http["type_ids"][ticket_type],
        team_id=template_http["team_ids"][team] if team else None,
        prefilled=prefilled or {"summary": "Template summary", "details": "Template details"},
        is_active=True,
        show_on_portal=show_on_portal,
    )
    db.add(template)
    await db.flush()
    return template.id


@pytest.mark.asyncio
async def test_template_visibility_filters_roles_teams_and_entities(template_http) -> None:
    ids = {
        "global": await add_template(template_http, name="Global A"),
        "team_a": await add_template(template_http, name="Team A", team="A"),
        "team_other": await add_template(
            template_http, name="Other team A", team="A_other", show_on_portal=False
        ),
        "problem": await add_template(
            template_http, name="Problem", ticket_type="PROBLEM", show_on_portal=True
        ),
        "change": await add_template(
            template_http, name="Change", ticket_type="CHANGE", show_on_portal=True
        ),
        "foreign": await add_template(template_http, entity="B", name="Foreign"),
    }
    client = template_http["client"]
    requester = await client.get("/api/v1/ticket-templates", headers=auth(template_http, "A_requester"))
    technician = await client.get("/api/v1/ticket-templates", headers=auth(template_http, "A_technician"))
    administrator = await client.get("/api/v1/ticket-templates", headers=auth(template_http, "A_administrator"))
    assert requester.status_code == technician.status_code == administrator.status_code == 200
    requester_ids = {item["id"] for item in requester.json()}
    technician_ids = {item["id"] for item in technician.json()}
    administrator_ids = {item["id"] for item in administrator.json()}
    assert requester_ids == {ids["global"], ids["team_a"]}
    assert technician_ids == {
        ids["global"],
        ids["team_a"],
        ids["problem"],
        ids["change"],
    }
    assert administrator_ids == set(ids.values()) - {ids["foreign"]}
    assert ids["team_other"] not in technician_ids
    assert ids["problem"] not in requester_ids and ids["change"] not in requester_ids
    assert ids["foreign"] not in administrator_ids
    assert (await client.get(
        f"/api/v1/ticket-templates/{ids['foreign']}",
        headers=auth(template_http, "A_administrator"),
    )).status_code == 404
    assert (await client.get(
        f"/api/v1/ticket-templates/{ids['team_other']}",
        headers=auth(template_http, "A_technician"),
    )).status_code == 404


@pytest.mark.asyncio
async def test_requester_template_detail_filters_hidden_fields_and_records_recent_use(template_http) -> None:
    template_id = await add_template(
        template_http,
        prefilled={
            "summary": "Requester summary",
            "details": "Requester details",
            "team": template_http["team_ids"]["A"],
            "agent": template_http["ids"]["A_technician"],
            "sla": 123,
            "flag": True,
            "source": "EMAIL",
            "user": template_http["ids"]["A_administrator"],
            "organisation": template_http["entity_ids"]["B"],
        },
    )
    response = await template_http["client"].get(
        f"/api/v1/ticket-templates/{template_id}",
        headers=auth(template_http, "A_requester"),
    )
    assert response.status_code == 200, response.text
    values = response.json()["prefilled"]
    assert values["summary"] == "Requester summary"
    assert values["details"] == "Requester details"
    assert not set(values) & {"team", "agent", "sla", "flag", "source", "user", "organisation"}
    usage = await template_http["db"].scalar(
        select(TicketTemplateUsage).where(
            TicketTemplateUsage.entity_id == template_http["entity_ids"]["A"],
            TicketTemplateUsage.user_id == template_http["ids"]["A_requester"],
            TicketTemplateUsage.template_id == template_id,
        )
    )
    assert usage is not None and usage.uses == 1
    listing = await template_http["client"].get(
        "/api/v1/ticket-templates",
        headers=auth(template_http, "A_requester"),
    )
    item = next(template for template in listing.json() if template["id"] == template_id)
    assert item["is_recent"] is True


@pytest.mark.asyncio
async def test_template_prefill_is_validated_sanitized_and_ticket_create_revalidates(template_http) -> None:
    client = template_http["client"]
    admin_headers = auth(template_http, "A_administrator")
    type_id = template_http["type_ids"]["INCIDENT"]
    invalid_template = await client.post(
        "/api/v1/ticket-templates",
        headers=admin_headers,
        json={
            "name": "Invalid choice",
            "ticket_type_id": type_id,
            "prefilled": {"source": "NOT_A_REAL_OPTION"},
        },
    )
    assert invalid_template.status_code == 422

    attack = '<p>Safe</p><script>alert(1)</script><img src=x onerror="alert(2)">'
    created = await client.post(
        "/api/v1/ticket-templates",
        headers=admin_headers,
        json={
            "name": "Sanitizer template",
            "description": "Template description.",
            "ticket_type_id": type_id,
            "show_on_portal": True,
            "prefilled": {"summary": "Safe summary", "details": attack, "source": "EMAIL"},
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["prefilled"]["details"] == "<p>Safe</p>"
    assert "<script" not in created.text and "onerror" not in created.text

    requester_headers = auth(template_http, "A_requester")
    detail = await client.get(
        f"/api/v1/ticket-templates/{created.json()['id']}",
        headers=requester_headers,
    )
    assert detail.status_code == 200, detail.text
    ticket = await client.post(
        "/api/v1/tickets",
        headers=requester_headers,
        json={
            "title": "Ticket using a template",
            "description": "A sufficiently long ticket description from a selected template.",
            "type": "INCIDENT",
            "ticket_type_id": type_id,
            "custom_fields": detail.json()["prefilled"],
        },
    )
    assert ticket.status_code == 201, ticket.text
    assert ticket.json()["custom_fields"]["details"] == "<p>Safe</p>"

    invalid_ticket = await client.post(
        "/api/v1/tickets",
        headers=admin_headers,
        json={
            "title": "Ticket with invalid option",
            "description": "A sufficiently long ticket description with an invalid option.",
            "type": "INCIDENT",
            "ticket_type_id": type_id,
            "custom_fields": {
                "summary": "Invalid option",
                "details": "A sufficiently long details value.",
                "source": "NOT_A_REAL_OPTION",
            },
        },
    )
    assert invalid_ticket.status_code == 422


@pytest.mark.asyncio
async def test_recent_order_is_per_user_and_empty_filter_returns_empty_list(template_http) -> None:
    first = await add_template(template_http, name="Recent first")
    second = await add_template(template_http, name="Recent second")
    client = template_http["client"]
    for template_id in (first, second):
        response = await client.get(
            f"/api/v1/ticket-templates/{template_id}",
            headers=auth(template_http, "A_requester"),
        )
        assert response.status_code == 200
    listing = await client.get(
        "/api/v1/ticket-templates",
        headers=auth(template_http, "A_requester"),
    )
    assert listing.json()[0]["id"] == second
    assert listing.json()[0]["is_recent"] is True
    other_user = await client.get(
        "/api/v1/ticket-templates",
        headers=auth(template_http, "B_requester"),
        params={"ticket_type_id": template_http["type_ids"]["PROBLEM"]},
    )
    assert other_user.status_code == 200
    assert other_user.json() == []
    empty = await client.get(
        "/api/v1/ticket-templates",
        headers=auth(template_http, "A_requester"),
        params={"q": "no such template exists"},
    )
    assert empty.status_code == 200
    assert empty.json() == []


@pytest.mark.asyncio
async def test_template_admin_crud_is_audited_and_entity_scoped(template_http) -> None:
    client = template_http["client"]
    template_id = await add_template(template_http, entity="B", name="Entity B template")
    foreign_id = await client.patch(
        f"/api/v1/ticket-templates/{template_id}",
        headers=auth(template_http, "A_administrator"),
        json={
            "name": "Changed from A",
            "ticket_type_id": template_http["type_ids"]["INCIDENT"],
            "prefilled": {},
        },
    )
    assert foreign_id.status_code == 404
    foreign_delete = await client.delete(
        f"/api/v1/ticket-templates/{template_id}",
        headers=auth(template_http, "A_administrator"),
    )
    assert foreign_delete.status_code == 404
    denied = await client.post(
        "/api/v1/ticket-templates",
        headers=auth(template_http, "A_technician"),
        json={
            "name": "Technician cannot administer",
            "ticket_type_id": template_http["type_ids"]["INCIDENT"],
        },
    )
    assert denied.status_code == 403

    created = await client.post(
        "/api/v1/ticket-templates",
        headers=auth(template_http, "A_administrator"),
        json={
            "name": "CRUD create",
            "description": "Initial description",
            "ticket_type_id": template_http["type_ids"]["INCIDENT"],
            "prefilled": {},
        },
    )
    assert created.status_code == 201, created.text
    template_id = created.json()["id"]
    updated = await client.patch(
        f"/api/v1/ticket-templates/{template_id}",
        headers=auth(template_http, "A_administrator"),
        json={
            "name": "CRUD updated",
            "description": "Updated description",
            "ticket_type_id": template_http["type_ids"]["INCIDENT"],
            "prefilled": {},
            "is_active": False,
            "show_on_portal": True,
            "sort_order": 12,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["is_active"] is False
    actions = list(
        (
            await template_http["db"].scalars(
                select(AuditLog.action).where(
                    AuditLog.entity == "ticket_template",
                    AuditLog.entity_id == template_id,
                )
            )
        ).all()
    )
    assert "created" in actions and "updated" in actions
    deleted = await client.delete(
        f"/api/v1/ticket-templates/{template_id}",
        headers=auth(template_http, "A_administrator"),
    )
    assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_seeded_templates_are_idempotent_and_preserve_admin_edits(template_http) -> None:
    db = template_http["db"]
    entity = await db.get(Entity, template_http["entity_ids"]["A"])
    assert entity is not None
    created = await seed_ticket_templates(db, entity)
    assert len(created) == sum(len(items) for items in DEFAULT_TICKET_TEMPLATES.values())
    await db.flush()
    seeded = await db.scalar(
        select(TicketTemplate).where(
            TicketTemplate.entity_id == entity.id,
            TicketTemplate.name == "Cannot log in / password reset",
        )
    )
    assert seeded is not None
    seeded.description = "Customized by an administrator."
    await db.flush()
    assert await seed_ticket_templates(db, entity) == []
    await db.refresh(seeded)
    assert seeded.description == "Customized by an administrator."
    counts = dict(
        (
            await db.execute(
                select(TicketTypeDefinition.itil_class, func.count(TicketTemplate.id))
                .join(TicketTemplate, TicketTemplate.ticket_type_id == TicketTypeDefinition.id)
                .where(TicketTemplate.entity_id == entity.id)
                .group_by(TicketTypeDefinition.itil_class)
            )
        ).all()
    )
    assert counts == {key: len(value) for key, value in DEFAULT_TICKET_TEMPLATES.items()}


@pytest.mark.asyncio
async def test_template_picker_migration_upgrades_and_downgrades() -> None:
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0015_ticket_template_picker.py"
    )
    spec = importlib.util.spec_from_file_location("ticket_template_picker_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql("CREATE TEMP TABLE entities (id INTEGER PRIMARY KEY)")
            await connection.exec_driver_sql("CREATE TEMP TABLE users (id INTEGER PRIMARY KEY)")
            await connection.exec_driver_sql(
                "CREATE TEMP TABLE ticket_types (id INTEGER PRIMARY KEY, entity_id INTEGER)"
            )
            await connection.exec_driver_sql(
                """
                CREATE TEMP TABLE ticket_templates (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR(160) NOT NULL,
                    ticket_type_id INTEGER NOT NULL,
                    team_id INTEGER,
                    prefilled JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
            await connection.exec_driver_sql("SET LOCAL search_path TO pg_temp, public")
            await connection.exec_driver_sql("INSERT INTO entities VALUES (11)")
            await connection.exec_driver_sql(
                "INSERT INTO ticket_types (id, entity_id) VALUES (21, 11), (22, NULL)"
            )
            await connection.exec_driver_sql(
                "INSERT INTO ticket_templates (id, name, ticket_type_id) VALUES (31, 'Owned', 21), (32, 'Global type', 22)"
            )

            def run_upgrade(sync_connection) -> None:
                original_op = migration.op
                migration.op = Operations(MigrationContext.configure(sync_connection))
                try:
                    migration.upgrade()
                finally:
                    migration.op = original_op

            await connection.run_sync(run_upgrade)
            rows = (
                await connection.exec_driver_sql(
                    "SELECT id, entity_id, is_active, show_on_portal, sort_order FROM ticket_templates ORDER BY id"
                )
            ).all()
            assert rows == [(31, 11, True, False, 0), (32, None, True, False, 0)]
            usage_table = await connection.exec_driver_sql(
                "SELECT to_regclass('pg_temp.ticket_template_usage')"
            )
            assert usage_table.scalar_one() == "ticket_template_usage"

            def run_downgrade(sync_connection) -> None:
                original_op = migration.op
                migration.op = Operations(MigrationContext.configure(sync_connection))
                try:
                    migration.downgrade()
                finally:
                    migration.op = original_op

            await connection.run_sync(run_downgrade)
            columns = (
                await connection.exec_driver_sql(
                    """
                    SELECT attname FROM pg_attribute
                    WHERE attrelid = 'pg_temp.ticket_templates'::regclass
                      AND attnum > 0 AND NOT attisdropped
                    """
                )
            ).scalars().all()
            assert "entity_id" not in columns
            assert "description" not in columns
            dropped_usage = await connection.exec_driver_sql(
                "SELECT to_regclass('pg_temp.ticket_template_usage')"
            )
            assert dropped_usage.scalar_one() is None
    finally:
        await engine.dispose()
