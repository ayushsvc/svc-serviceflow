from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.tickets import escape_ticket_search, list_tickets, ticket_team_counts
from app.deps import current_user, get_db
from app.main import app


class _Rows:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class _ListDb:
    def __init__(self) -> None:
        self.executed: list[object] = []

    async def scalar(self, _query: object) -> int:
        return 3

    async def execute(self, query: object) -> _Rows:
        self.executed.append(query)
        if "groups.id" in str(query):
            return _Rows([])
        ticket = SimpleNamespace(
            id=1, title="Visible", description="Details", type="INCIDENT", status="NEW",
            urgency=3, impact=3, priority=3, category_id=None, entity_id=1, source="web",
            requester_id=10, assignee_id=None, opened_at=datetime.now(UTC), solved_at=None,
            closed_at=None, tto_due_at=None, ttr_due_at=None, sla_id=None, custom_fields={},
            ticket_type_id=None, deleted_at=None, flagged=True,
        )
        requester = SimpleNamespace(name="Requester", is_active=False)
        return _Rows([(ticket, requester)])


def _user(interface: str = "helpdesk", entity_id: int = 1) -> SimpleNamespace:
    rights = {"ticket:view_all": True} if interface == "helpdesk" else {"ticket:own_only": True}
    return SimpleNamespace(
        id=10,
        entity_id=entity_id,
        profile=SimpleNamespace(interface=interface, name="Technician", rights=rights),
    )


def test_search_escapes_sql_wildcards() -> None:
    assert escape_ticket_search(r"100%_ok\value") == r"100\%\_ok\\value"


@pytest.mark.asyncio
async def test_list_metadata_and_sort_whitelist() -> None:
    result = await list_tickets(
        _user(),
        _ListDb(),
        ticket_status=None,
        priority=None,
        search=None,
        page=2,
        page_size=25,
        sort="priority",
        direction="asc",
        queue=None,
    )
    assert result["page"] == 2
    assert result["page_size"] == 25
    assert result["total"] == 3
    assert result["pages"] == 1
    with pytest.raises(HTTPException) as error:
        await list_tickets(
            _user(), _ListDb(), ticket_status=None, priority=None, search=None,
            page=1, page_size=25, sort="requester_password", direction="desc",
        )
    assert error.value.status_code == 422


def test_list_query_bounds_are_rejected() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/tickets?page=0")
        assert response.status_code in {401, 422}
        response = client.get("/api/v1/tickets?page_size=101")
        assert response.status_code in {401, 422}


def test_team_counts_route_is_registered() -> None:
    assert any(route.path == "/api/v1/tickets/team-counts" for route in app.routes)


@pytest.mark.asyncio
async def test_requester_response_omits_internal_indicators() -> None:
    result = await list_tickets(
        _user("self_service"),
        _ListDb(),
        ticket_status=None,
        priority=None,
        search=None,
        sort="opened_at",
        direction="desc",
        page=1,
        page_size=25,
        queue=None,
    )
    item = result["items"][0]
    assert item["requester_name"] is None
    assert item["requester_active"] is None
    assert item["requester_vip"] is None
    assert item["flagged"] is None


@pytest.mark.asyncio
async def test_team_counts_query_is_entity_scoped() -> None:
    db = _ListDb()
    await ticket_team_counts(_user(entity_id=99), db)
    assert "entity_id" in str(db.executed[0])


@pytest.mark.asyncio
async def test_list_query_scopes_entity_requester_and_soft_delete() -> None:
    db = _ListDb()
    await list_tickets(
        _user("self_service", entity_id=99),
        db,
        ticket_status=None,
        priority=None,
        search="50%_",
        sort="opened_at",
        direction="desc",
        page=1,
        page_size=25,
        queue=None,
    )
    query = str(db.executed[-1])
    assert "entity_id" in query
    assert "requester_id" in query
    assert "deleted_at" in query
    assert "ESCAPE" in query


def test_dependency_overrides_are_not_left_in_place() -> None:
    assert current_user not in app.dependency_overrides
    assert get_db not in app.dependency_overrides


class _HttpDb:
    def __init__(self, user: SimpleNamespace) -> None:
        self.user = user

    def _search_matches_ticket(self, query: object) -> bool:
        if self.user.profile.interface != "self_service":
            return True
        params = query.compile().params.values()
        patterns = [
            value
            for value in params
            if isinstance(value, str) and value.startswith("%") and value.endswith("%")
        ]
        if not patterns:
            return self.user.entity_id == 1 and self.user.id == 10
        search = patterns[0].strip("%").replace("\\%", "%").replace("\\_", "_")
        return search.casefold() in "101 Entity one ticket Ordinary details".casefold()

    async def scalar(self, query: object) -> int:
        return int(self.user.entity_id == 1 and self.user.id == 10 and self._search_matches_ticket(query))

    async def execute(self, query: object) -> _Rows:
        if (
            self.user.entity_id != 1
            or (self.user.profile.interface == "self_service" and self.user.id != 10)
            or not self._search_matches_ticket(query)
        ):
            return _Rows([])
        ticket = SimpleNamespace(
            id=101, title="Entity one ticket", description="Ordinary details", type="INCIDENT", status="NEW",
            urgency=3, impact=3, priority=3, category_id=None, entity_id=1, source="web",
            requester_id=10, assignee_id=None, opened_at=datetime.now(UTC), solved_at=None,
            closed_at=None, tto_due_at=None, ttr_due_at=None, sla_id=None, custom_fields={},
            ticket_type_id=None, deleted_at=None, flagged=False,
        )
        return _Rows([(ticket, SimpleNamespace(name="Entity one requester", is_active=True))])


def test_http_list_isolation_for_entity_and_requester() -> None:
    active_user = _user(entity_id=1)

    async def override_user() -> SimpleNamespace:
        return active_user

    async def override_db() -> _HttpDb:
        return _HttpDb(active_user)

    app.dependency_overrides[current_user] = override_user
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/tickets").json()["items"][0]["id"] == 101
            active_user.entity_id = 2
            assert client.get("/api/v1/tickets").json()["items"] == []
            active_user.entity_id = 1
            active_user.profile.interface = "self_service"
            active_user.id = 999
            assert client.get("/api/v1/tickets").json()["items"] == []
    finally:
        app.dependency_overrides.clear()


def test_http_requester_search_does_not_return_private_or_other_user_text() -> None:
    active_user = _user(interface="self_service", entity_id=1)

    async def override_user() -> SimpleNamespace:
        return active_user

    async def override_db() -> _HttpDb:
        return _HttpDb(active_user)

    app.dependency_overrides[current_user] = override_user
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/tickets", params={"search": "private technician note"})
            assert response.status_code == 200
            assert "private technician note" not in response.text
            assert "Entity one ticket" not in response.text
    finally:
        app.dependency_overrides.clear()
