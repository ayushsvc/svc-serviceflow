import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.deps import current_user, get_db
from app.main import app
from app.models import (
    BusinessRule,
    Entity,
    Group,
    NotificationTemplate,
    Profile,
    SLA,
    TicketTemplate,
    TicketTemplateUsage,
    TicketTypeDefinition,
    User,
)
from app.seed import seed_entity_defaults


@pytest.fixture
async def removal_http(monkeypatch):
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    connection = await engine.connect()
    transaction = await connection.begin()
    db = AsyncSession(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
    suffix = uuid4().hex
    try:
        profiles = {
            profile.name: profile
            for profile in (
                await db.scalars(select(Profile).where(Profile.name.in_(("Administrator", "Technician", "Requester"))))
            ).all()
        }
        entities = {key: Entity(name=f"Template removal {key} {suffix}") for key in ("A", "B")}
        db.add_all(entities.values())
        await db.flush()
        users = {
            f"{key}_{role.lower()}": User(
                email=f"template-removal-{key}-{role.lower()}-{suffix}@example.test",
                password_hash="test-only",
                name=f"Removal {key} {role}",
                profile=profiles[role],
                entity=entities[key],
            )
            for key in ("A", "B")
            for role in ("Administrator", "Technician", "Requester")
        }
        db.add_all(users.values())
        await db.flush()
        for entity in entities.values():
            await seed_entity_defaults(db, entity)
        await db.flush()
        incident = await db.scalar(
            select(TicketTypeDefinition)
            .where(
                TicketTypeDefinition.itil_class == "INCIDENT",
                TicketTypeDefinition.is_active.is_(True),
                TicketTypeDefinition.entity_id.is_(None),
            )
            .order_by(TicketTypeDefinition.id)
            .limit(1)
        )
        assert incident is not None
        users_by_id = {user.id: user for user in users.values()}

        async def user_override(request: Request) -> User:
            return users_by_id[int(request.headers["x-test-user"])]

        async def db_override():
            yield db

        monkeypatch.setattr("app.api.tickets.send_ticket_notification.delay", lambda *args, **kwargs: None)
        monkeypatch.setattr("app.api.tickets.dispatch_ticket_update_effects", lambda *args, **kwargs: None)
        app.dependency_overrides[current_user] = user_override
        app.dependency_overrides[get_db] = db_override
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            yield {
                "client": client,
                "db": db,
                "entities": entities,
                "users": users,
                "headers": {key: {"x-test-user": str(user.id)} for key, user in users.items()},
                "incident_id": incident.id,
            }
    finally:
        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(get_db, None)
        await db.close()
        if transaction.is_active:
            await transaction.rollback()
        await connection.close()
        await engine.dispose()


def _ticket_payload(context, *, requester: bool, extra: dict | None = None) -> dict:
    payload = {
        "title": f"Template removal {uuid4().hex}",
        "description": "A valid description with enough detail.",
        "type": "INCIDENT",
        "ticket_type_id": context["incident_id"],
    }
    if requester:
        payload["priority"] = "medium"
    return {**payload, **(extra or {})}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/ticket-templates"),
        ("GET", "/api/v1/ticket-templates/1"),
        ("POST", "/api/v1/ticket-templates"),
        ("PATCH", "/api/v1/ticket-templates/1"),
        ("DELETE", "/api/v1/ticket-templates/1"),
        ("GET", "/api/v1/templates"),
        ("POST", "/api/v1/templates"),
        ("PATCH", "/api/v1/templates/1"),
        ("DELETE", "/api/v1/templates/1"),
    ],
)
async def test_removed_ticket_template_endpoints_return_404(removal_http, method, path) -> None:
    response = await removal_http["client"].request(
        method, path, headers=removal_http["headers"]["A_administrator"], json={} if method in {"POST", "PATCH"} else None
    )
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_notification_templates_endpoint_is_untouched(removal_http) -> None:
    response = await removal_http["client"].get(
        "/api/v1/notification-templates", headers=removal_http["headers"]["A_administrator"]
    )
    assert response.status_code == 200, response.text
    assert len(response.json()) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("user_key", ["A_requester", "A_technician", "A_administrator"])
async def test_ticket_create_works_without_template_id_and_rejects_it(removal_http, user_key) -> None:
    client = removal_http["client"]
    headers = removal_http["headers"][user_key]
    created = await client.post(
        "/api/v1/tickets", headers=headers, json=_ticket_payload(removal_http, requester=user_key.endswith("requester"))
    )
    assert created.status_code == 201, created.text
    assert "template_id" not in created.json()

    rejected = await client.post(
        "/api/v1/tickets",
        headers=headers,
        json=_ticket_payload(removal_http, requester=user_key.endswith("requester"), extra={"template_id": 1}),
    )
    assert rejected.status_code == 422, rejected.text
    assert "template_id" in str(rejected.json()["detail"])


@pytest.mark.asyncio
async def test_ticket_with_old_template_reference_still_loads(removal_http) -> None:
    db = removal_http["db"]
    entity = removal_http["entities"]["A"]
    requester = removal_http["users"]["A_requester"]
    template = TicketTemplate(
        entity_id=entity.id,
        ticket_type_id=removal_http["incident_id"],
        name=f"Legacy template {uuid4().hex}",
        description="Left in the database after the feature was removed.",
        prefilled={"summary": "Legacy"},
        show_on_portal=True,
    )
    db.add(template)
    await db.flush()
    db.add(TicketTemplateUsage(entity_id=entity.id, user_id=requester.id, template_id=template.id))
    await db.flush()

    client = removal_http["client"]
    created = await client.post(
        "/api/v1/tickets", headers=removal_http["headers"]["A_requester"], json=_ticket_payload(removal_http, requester=True)
    )
    assert created.status_code == 201, created.text
    ticket_id = created.json()["id"]
    detail = await client.get(f"/api/v1/tickets/{ticket_id}", headers=removal_http["headers"]["A_requester"])
    assert detail.status_code == 200, detail.text
    listing = await client.get("/api/v1/tickets", headers=removal_http["headers"]["A_administrator"])
    assert listing.status_code == 200, listing.text
    assert await db.get(TicketTemplate, template.id) is not None


@pytest.mark.asyncio
async def test_seed_twice_is_noop_when_ticket_template_rows_exist(removal_http) -> None:
    db = removal_http["db"]
    entity = removal_http["entities"]["A"]
    db.add(
        TicketTemplate(
            entity_id=entity.id,
            ticket_type_id=removal_http["incident_id"],
            name=f"Existing template {uuid4().hex}",
            prefilled={},
        )
    )
    await db.flush()

    async def counts() -> dict[str, int]:
        return {
            "ticket_templates": await db.scalar(select(func.count(TicketTemplate.id)).where(TicketTemplate.entity_id == entity.id)),
            "notification_templates": await db.scalar(
                select(func.count(NotificationTemplate.id)).where(NotificationTemplate.entity_id == entity.id)
            ),
            "slas": await db.scalar(select(func.count(SLA.id)).where(SLA.entity_id == entity.id)),
            "rules": await db.scalar(select(func.count(BusinessRule.id)).where(BusinessRule.entity_id == entity.id)),
            "groups": await db.scalar(select(func.count(Group.id)).where(Group.entity_id == entity.id)),
        }

    before = await counts()
    assert before["ticket_templates"] == 1
    assert before["notification_templates"] == 5
    await seed_entity_defaults(db, entity)
    await db.flush()
    await seed_entity_defaults(db, entity)
    await db.flush()
    assert await counts() == before


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
