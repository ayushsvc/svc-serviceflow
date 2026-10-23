import importlib.util
from uuid import uuid4
from types import SimpleNamespace
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import HTTPException
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.tickets import validate_profile_config
from app.core.config import get_settings
from app.deps import current_user, get_db
from app.main import app
from app.models import AuditLog, Entity, Group, ListProfile, Profile, Ticket, TicketFollowup, TicketTypeDefinition, User


@pytest.mark.asyncio
async def test_migration_deduplicates_profile_names_and_downgrades() -> None:
    migration_path = Path(__file__).parents[1] / "alembic" / "versions" / "0013_list_profile_constraints.py"
    spec = importlib.util.spec_from_file_location("list_profile_constraints", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    test_engine = create_async_engine(get_settings().database_url, poolclass=NullPool)

    try:
        async with test_engine.begin() as connection:
            await connection.exec_driver_sql(
                """
                CREATE TEMP TABLE list_profiles (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    entity_id INTEGER NOT NULL,
                    kind VARCHAR(20) NOT NULL,
                    name VARCHAR(120) NOT NULL,
                    config JSONB NOT NULL,
                    is_default BOOLEAN NOT NULL DEFAULT FALSE,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            await connection.exec_driver_sql(
                """
                INSERT INTO list_profiles (id, user_id, entity_id, kind, name, config)
                VALUES
                    (1, 10, 20, 'filter', 'Queue', '{}'),
                    (2, 10, 20, 'filter', 'Queue', '{}'),
                    (3, 10, 20, 'filter', 'Queue (2)', '{}')
                """
            )

            def run_upgrade(sync_connection) -> None:
                original_op = migration.op
                migration.op = Operations(MigrationContext.configure(sync_connection))
                try:
                    migration.upgrade()
                finally:
                    migration.op = original_op

            await connection.run_sync(run_upgrade)
            rows = (await connection.exec_driver_sql("SELECT id, name FROM list_profiles ORDER BY id")).all()
            assert rows == [(1, "Queue"), (2, "Queue (3)"), (3, "Queue (2)")]

            def run_downgrade(sync_connection) -> None:
                original_op = migration.op
                migration.op = Operations(MigrationContext.configure(sync_connection))
                try:
                    migration.downgrade()
                finally:
                    migration.op = original_op

            await connection.run_sync(run_downgrade)
    finally:
        await test_engine.dispose()


def test_filter_profile_config_allow_list() -> None:
    assert validate_profile_config(
        "filter",
        {"search": "vpn", "status": "NEW", "sort": "priority", "direction": "desc", "page_size": 25},
    )["sort"] == "priority"


def test_column_profile_config_rejects_unknown_or_duplicate_columns() -> None:
    with pytest.raises(HTTPException) as unknown:
        validate_profile_config("column", {"columns": ["title", "password_hash"]})
    assert unknown.value.status_code == 422
    with pytest.raises(HTTPException) as duplicate:
        validate_profile_config("column", {"columns": ["title", "title"]})
    assert duplicate.value.status_code == 422


def test_filter_profile_config_rejects_invalid_sort_and_page_size() -> None:
    with pytest.raises(HTTPException):
        validate_profile_config("filter", {"sort": "password_hash"})
    with pytest.raises(HTTPException):
        validate_profile_config("filter", {"page_size": 101})


def test_profile_routes_are_registered() -> None:
    from app.main import app

    paths = {route.path for route in app.routes}
    assert "/api/v1/tickets/list-profiles" in paths
    assert "/api/v1/tickets/list-profiles/{profile_id}" in paths


def test_profile_scope_requires_user_and_entity() -> None:
    user_a = SimpleNamespace(id=1, entity_id=10)
    user_b = SimpleNamespace(id=2, entity_id=20)
    assert (user_a.id, user_a.entity_id) != (user_b.id, user_b.entity_id)


@pytest.fixture
async def real_postgres_http():
    test_engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    async with test_engine.connect() as connection:
        transaction = await connection.begin()
        db = AsyncSession(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        suffix = uuid4().hex
        entity_a = Entity(name=f"Slice2 entity A {suffix}")
        entity_b = Entity(name=f"Slice2 entity B {suffix}")
        requester_profile = Profile(name=f"Slice2 requester {suffix}", interface="self_service")
        helpdesk_profile = Profile(name=f"Slice2 helpdesk {suffix}", interface="helpdesk")
        db.add_all([entity_a, entity_b, requester_profile, helpdesk_profile])
        await db.flush()
        foreign_group = Group(name=f"Entity B team {suffix}", entity_id=entity_b.id)
        db.add(foreign_group)
        await db.flush()
        foreign_type = TicketTypeDefinition(
            name=f"Entity B type {suffix}",
            itil_class="INCIDENT",
            default_priority=3,
            default_team_id=foreign_group.id,
            entity_id=entity_b.id,
        )
        db.add(foreign_type)
        await db.flush()

        users = {
            "a_requester": User(email=f"a-requester-{suffix}@example.test", password_hash="test", name="A requester", profile=requester_profile, entity=entity_a),
            "a_helpdesk": User(email=f"a-helpdesk-{suffix}@example.test", password_hash="test", name="A helpdesk", profile=helpdesk_profile, entity=entity_a),
            "b_requester": User(email=f"b-requester-{suffix}@example.test", password_hash="test", name="B requester", profile=requester_profile, entity=entity_b),
            "b_helpdesk": User(email=f"b-helpdesk-{suffix}@example.test", password_hash="test", name="B helpdesk", profile=helpdesk_profile, entity=entity_b),
        }
        db.add_all(users.values())
        await db.flush()

        requester_ticket = Ticket(
            title="A requester visible ticket",
            description="Ordinary public ticket details",
            entity_id=entity_a.id,
            requester_id=users["a_requester"].id,
        )
        other_user_ticket = Ticket(
            title="Other user cross-user marker",
            description="Other user's ticket details",
            entity_id=entity_a.id,
            requester_id=users["a_helpdesk"].id,
            ticket_type_id=foreign_type.id,
        )
        entity_b_tickets = [
            Ticket(
                title=f"Entity B ticket {index}",
                description="Entity B only",
                entity_id=entity_b.id,
                requester_id=users["b_requester"].id,
                ticket_type_id=foreign_type.id,
            )
            for index in range(3)
        ]
        db.add_all([requester_ticket, other_user_ticket, *entity_b_tickets])
        await db.flush()
        db.add(
            TicketFollowup(
                ticket_id=requester_ticket.id,
                author_id=users["a_helpdesk"].id,
                body="private technician note marker",
                is_private=True,
            )
        )
        await db.flush()

        state = {"user": users["a_helpdesk"]}

        async def override_user() -> User:
            return state["user"]

        async def override_db():
            yield db

        app.dependency_overrides[current_user] = override_user
        app.dependency_overrides[get_db] = override_db
        data = {
            "client": None,
            "db": db,
            "entities": {"a": entity_a, "b": entity_b},
            "foreign_group": foreign_group,
            "users": users,
            "tickets": {"a_requester": requester_ticket, "a_other": other_user_ticket},
            "state": state,
        }
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                data["client"] = client
                yield data
        finally:
            app.dependency_overrides.pop(current_user, None)
            app.dependency_overrides.pop(get_db, None)
            await db.close()
            if transaction.is_active:
                await transaction.rollback()
            await test_engine.dispose()


async def _create_profile(client: AsyncClient, name: str, kind: str = "filter", config: dict | None = None, **extra: object):
    return await client.post(
        "/api/v1/tickets/list-profiles",
        json={"name": name, "kind": kind, "config": config or {"sort": "priority"}, **extra},
    )


@pytest.mark.asyncio
async def test_http_profile_crud_default_uniqueness_and_audit(real_postgres_http) -> None:
    client = real_postgres_http["client"]
    user = real_postgres_http["users"]["a_helpdesk"]
    state = real_postgres_http["state"]

    first = await _create_profile(client, "Priority queue", is_default=True)
    second = await _create_profile(client, "Recent queue")
    assert first.status_code == 201
    assert first.json()["entity_id"] == user.entity_id
    assert second.status_code == 201

    duplicate = await _create_profile(client, "Priority queue")
    assert duplicate.status_code == 409
    default_response = await client.post(f"/api/v1/tickets/list-profiles/{second.json()['id']}/set-default")
    assert default_response.status_code == 200
    assert default_response.json()["is_default"] is True
    first_after = await client.get(f"/api/v1/tickets/list-profiles/{first.json()['id']}")
    assert first_after.json()["is_default"] is False

    listed = await client.get("/api/v1/tickets/list-profiles")
    assert {item["id"] for item in listed.json()} == {first.json()["id"], second.json()["id"]}
    assert (await client.get(f"/api/v1/tickets/list-profiles/{second.json()['id']}")).status_code == 200
    updated = await client.patch(
        f"/api/v1/tickets/list-profiles/{second.json()['id']}",
        json={"name": "Updated queue", "config": {"status": "NEW", "page_size": 10}},
    )
    assert updated.status_code == 200
    assert updated.json()["config"] == {"status": "NEW", "page_size": 10}
    assert (await client.post(f"/api/v1/tickets/list-profiles/{second.json()['id']}/set-default")).status_code == 200

    profile_id = second.json()["id"]
    assert (await client.delete(f"/api/v1/tickets/list-profiles/{profile_id}")).status_code == 204
    audit_rows = list(
        (
            await real_postgres_http["db"].scalars(
                select(AuditLog).where(
                    AuditLog.entity == "list_profile",
                    AuditLog.entity_id == profile_id,
                    AuditLog.actor_id == user.id,
                )
            )
        ).all()
    )
    assert {entry.action for entry in audit_rows} == {"created", "updated", "set_default", "deleted"}
    assert state["user"].entity_id == user.entity_id


@pytest.mark.asyncio
async def test_http_profiles_are_private_across_users_and_entities(real_postgres_http) -> None:
    client = real_postgres_http["client"]
    state = real_postgres_http["state"]
    own_profile = await _create_profile(client, "Private filter", is_default=True)
    state["user"] = real_postgres_http["users"]["b_helpdesk"]
    other_entity_profile = await _create_profile(client, "Other entity filter", is_default=True)
    assert own_profile.status_code == other_entity_profile.status_code == 201

    for alias in ("a_requester", "b_requester"):
        state["user"] = real_postgres_http["users"][alias]
        assert (await client.get("/api/v1/tickets/list-profiles")).json() == []
        profile_id = own_profile.json()["id"]
        assert (await client.get(f"/api/v1/tickets/list-profiles/{profile_id}")).status_code == 404
        assert (await client.patch(f"/api/v1/tickets/list-profiles/{profile_id}", json={"name": "Hijacked"})).status_code == 404
        assert (await client.delete(f"/api/v1/tickets/list-profiles/{profile_id}")).status_code == 404
        assert (await client.post(f"/api/v1/tickets/list-profiles/{profile_id}/set-default")).status_code == 404

    state["user"] = real_postgres_http["users"]["a_helpdesk"]
    cross_entity_id = other_entity_profile.json()["id"]
    assert (await client.get(f"/api/v1/tickets/list-profiles/{cross_entity_id}")).status_code == 404
    assert (await client.patch(f"/api/v1/tickets/list-profiles/{cross_entity_id}", json={"name": "Hijacked"})).status_code == 404
    assert (await client.delete(f"/api/v1/tickets/list-profiles/{cross_entity_id}")).status_code == 404
    assert (await client.post(f"/api/v1/tickets/list-profiles/{cross_entity_id}/set-default")).status_code == 404


@pytest.mark.asyncio
async def test_http_profile_config_rejects_unknown_oversize_deep_and_invalid_values(real_postgres_http) -> None:
    client = real_postgres_http["client"]
    configs = [
        {"unknown": "value"},
        {"search": "x" * 8193},
        {"search": {"one": {"two": {"three": "too deep"}}}},
        {"search": None},
        {"sort": "password_hash"},
        {"page_size": True},
        {"entity_id": 1},
    ]
    for config in configs:
        response = await _create_profile(client, f"Invalid {uuid4().hex}", config=config)
        assert response.status_code == 422, config

    for config in ({"columns": ["password_hash"]}, {"columns": ["title", "title"]}, {"columns": []}):
        response = await _create_profile(client, f"Invalid columns {uuid4().hex}", kind="column", config=config)
        assert response.status_code == 422, config

    entity_override = await client.post(
        "/api/v1/tickets/list-profiles",
        json={"name": "Payload entity", "kind": "filter", "config": {}, "entity_id": 999},
    )
    assert entity_override.status_code == 422


@pytest.mark.asyncio
async def test_http_profile_database_partial_unique_default_index(real_postgres_http) -> None:
    client = real_postgres_http["client"]
    db = real_postgres_http["db"]
    user = real_postgres_http["users"]["a_helpdesk"]
    response = await _create_profile(client, "Database default", is_default=True)
    assert response.status_code == 201

    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(
                ListProfile(
                    user_id=user.id,
                    entity_id=user.entity_id,
                    kind="filter",
                    name="Bypass code",
                    config={},
                    is_default=True,
                )
            )
            await db.flush()

    defaults = await db.scalar(
        select(func.count()).select_from(ListProfile).where(
            ListProfile.user_id == user.id,
            ListProfile.kind == "filter",
            ListProfile.is_default.is_(True),
        )
    )
    assert defaults == 1


@pytest.mark.asyncio
async def test_http_ticket_and_team_counts_are_entity_scoped(real_postgres_http) -> None:
    client = real_postgres_http["client"]
    state = real_postgres_http["state"]

    state["user"] = real_postgres_http["users"]["a_helpdesk"]
    tickets_a = await client.get("/api/v1/tickets")
    counts_a = await client.get("/api/v1/tickets/team-counts")
    assert tickets_a.status_code == counts_a.status_code == 200
    assert tickets_a.json()["total"] == 2
    assert sum(item["count"] for item in counts_a.json()) == 2
    assert all(item["team_id"] != real_postgres_http["foreign_group"].id for item in counts_a.json())
    assert all(ticket["entity_id"] == real_postgres_http["entities"]["a"].id for ticket in tickets_a.json()["items"])

    state["user"] = real_postgres_http["users"]["b_helpdesk"]
    tickets_b = await client.get("/api/v1/tickets")
    counts_b = await client.get("/api/v1/tickets/team-counts")
    assert tickets_b.json()["total"] == 3
    assert sum(item["count"] for item in counts_b.json()) == 3
    assert counts_b.json() == [{"team_id": real_postgres_http["foreign_group"].id, "count": 3}]
    assert all(ticket["entity_id"] == real_postgres_http["entities"]["b"].id for ticket in tickets_b.json()["items"])


@pytest.mark.asyncio
async def test_http_requester_search_excludes_private_notes_and_other_users(real_postgres_http) -> None:
    client = real_postgres_http["client"]
    state = real_postgres_http["state"]
    state["user"] = real_postgres_http["users"]["a_requester"]

    for marker in ("private technician note marker", "Other user cross-user marker", "Entity B ticket"):
        response = await client.get("/api/v1/tickets", params={"search": marker})
        assert response.status_code == 200
        assert response.json()["items"] == []
