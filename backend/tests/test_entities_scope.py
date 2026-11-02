from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.deps import current_user, get_db
from app.main import app
from app.models import Entity, Profile, User


@pytest.fixture
async def scope():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        db = AsyncSession(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        suffix = uuid4().hex
        profiles = {
            "requester": Profile(name=f"Scope requester {suffix}", interface="self_service", rights={"ticket:own_only": True}),
            "tech": Profile(name=f"Scope tech {suffix}", interface="helpdesk", rights={"ticket:view_queue": True}),
            "admin": Profile(name=f"Scope admin {suffix}", interface="helpdesk", rights={"*": True}),
        }
        entities = {"A": Entity(name=f"Scope A {suffix}"), "B": Entity(name=f"Scope B {suffix}")}
        db.add_all([*profiles.values(), *entities.values()])
        await db.flush()
        child = Entity(name=f"Scope A child {suffix}", parent_id=entities["A"].id)
        db.add(child)
        await db.flush()
        users = {}
        for key, entity, profile in (
            ("A_requester", "A", "requester"), ("A_tech", "A", "tech"), ("A_admin", "A", "admin"),
            ("B_requester", "B", "requester"), ("B_tech", "B", "tech"),
        ):
            users[key] = User(email=f"scope-{key.lower()}-{suffix}@example.com", password_hash="x", name=f"Name {key}", profile=profiles[profile], entity=entities[entity])
            db.add(users[key])
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
                yield {"client": client, "entities": entities, "child": child, "users": users, "state": state}
        finally:
            app.dependency_overrides.pop(current_user, None)
            app.dependency_overrides.pop(get_db, None)
            await db.close()
            if transaction.is_active:
                await transaction.rollback()
            await engine.dispose()


@pytest.mark.parametrize("key,own,other", [("A_requester", "A", "B"), ("A_tech", "A", "B"), ("B_requester", "B", "A"), ("B_tech", "B", "A")])
async def test_entities_list_never_shows_other_tenants(scope, key, own, other):
    scope["state"]["user"] = scope["users"][key]
    response = await scope["client"].get("/api/v1/entities")
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()}
    assert ids == {scope["entities"][own].id}
    assert scope["entities"][other].name not in response.text
    assert str(scope["entities"][other].id) not in {str(i) for i in ids}


async def test_entities_list_for_admin_right_includes_descendants_only(scope):
    scope["state"]["user"] = scope["users"]["A_admin"]
    response = await scope["client"].get("/api/v1/entities")
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()}
    assert ids == {scope["entities"]["A"].id, scope["child"].id}
    assert scope["entities"]["B"].name not in response.text


@pytest.mark.parametrize("key,own", [("A_requester", "A"), ("A_tech", "A"), ("B_requester", "B"), ("B_tech", "B")])
async def test_auth_me_returns_only_callers_entity(scope, key, own):
    scope["state"]["user"] = scope["users"][key]
    response = await scope["client"].get("/api/v1/auth/me")
    assert response.status_code == 200
    body = response.json()
    assert body["entity"] == {"id": scope["entities"][own].id, "name": scope["entities"][own].name}
    assert body["entity_id"] == scope["entities"][own].id
    other = "B" if own == "A" else "A"
    assert scope["entities"][other].name not in response.text
