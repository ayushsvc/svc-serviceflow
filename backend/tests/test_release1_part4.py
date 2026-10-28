from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.deps import current_user, get_db
from app.main import app
from app.models import Category, Entity, Profile, Ticket, TicketFollowup, TicketTypeDefinition, TicketWorkflow, User
from app.services.ticket_filters import STATUS_LABELS, status_label

ADMIN_RIGHTS = {
    "*": True,
    "ticket:view_all": True,
    "ticket:view_queue": True,
    "ticket:assign_any": True,
    "ticket:bulk_edit": True,
    "ticket:self_claim": True,
    "ticket:view_unassigned": True,
    "tickets:update": True,
}
TECHNICIAN_RIGHTS = {
    "ticket:view_queue": True,
    "ticket:bulk_edit": True,
    "tickets:update": True,
    "ticket:self_claim": False,
    "ticket:view_unassigned": False,
}
BASE = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
async def part4(monkeypatch, tmp_path):
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        db = AsyncSession(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        suffix = uuid4().hex
        profiles = {
            "requester": Profile(name=f"Part4 requester {suffix}", interface="self_service", rights={"ticket:own_only": True}),
            "admin": Profile(name=f"Part4 admin {suffix}", interface="helpdesk", rights=ADMIN_RIGHTS),
            "tech": Profile(name=f"Part4 technician {suffix}", interface="helpdesk", rights=TECHNICIAN_RIGHTS),
        }
        entities = {key: Entity(name=f"Part4 {key} {suffix}") for key in ("A", "B")}
        db.add_all([*profiles.values(), *entities.values()])
        await db.flush()
        users: dict[str, User] = {}
        for key, entity, profile in (
            ("A_requester", "A", "requester"),
            ("A_requester2", "A", "requester"),
            ("A_admin", "A", "admin"),
            ("A_tech", "A", "tech"),
            ("A_tech2", "A", "tech"),
            ("B_requester", "B", "requester"),
            ("B_admin", "B", "admin"),
            ("B_tech", "B", "tech"),
        ):
            users[key] = User(
                email=f"part4-{key.lower()}-{suffix}@example.test",
                password_hash="test-only",
                name=f"Name {key}",
                profile=profiles[profile],
                entity=entities[entity],
            )
            db.add(users[key])
        await db.flush()
        categories = {
            "A1": Category(name=f"Part4 cat A1 {suffix}", entity_id=entities["A"].id, level=1),
            "A2": Category(name=f"Part4 cat A2 {suffix}", entity_id=entities["A"].id, level=1),
            "B1": Category(name=f"Part4 cat B1 {suffix}", entity_id=entities["B"].id, level=1),
        }
        types = {
            "A1": TicketTypeDefinition(name=f"Part4 type A1 {suffix}", itil_class="INCIDENT", entity_id=entities["A"].id),
            "A2": TicketTypeDefinition(name=f"Part4 type A2 {suffix}", itil_class="REQUEST", entity_id=entities["A"].id),
            "B1": TicketTypeDefinition(name=f"Part4 type B1 {suffix}", itil_class="INCIDENT", entity_id=entities["B"].id),
        }
        db.add_all([*categories.values(), *types.values()])
        await db.flush()
        state = {"user": users["A_admin"]}

        async def override_user() -> User:
            return state["user"]

        async def override_db():
            yield db

        app.dependency_overrides[current_user] = override_user
        app.dependency_overrides[get_db] = override_db
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                yield {"client": client, "db": db, "entities": entities, "users": users, "state": state, "categories": categories, "types": types}
        finally:
            app.dependency_overrides.pop(current_user, None)
            app.dependency_overrides.pop(get_db, None)
            await db.close()
            if transaction.is_active:
                await transaction.rollback()
            await engine.dispose()


async def make(
    ctx,
    *,
    entity: str = "A",
    requester: str | None = None,
    assignee: str | None = None,
    status: str = "NEW",
    priority: int = 3,
    type_key: str | None = None,
    category_key: str | None = None,
    opened_at: datetime = BASE,
) -> int:
    ticket = Ticket(
        title=f"Part 4 ticket {uuid4().hex[:8]}",
        description="<p>Filter test ticket.</p>",
        type="INCIDENT",
        status=status,
        urgency=3,
        impact=3,
        priority=priority,
        entity_id=ctx["entities"][entity].id,
        requester_id=ctx["users"][requester or f"{entity}_requester"].id,
        assignee_id=ctx["users"][assignee].id if assignee else None,
        ticket_type_id=ctx["types"][type_key].id if type_key else None,
        category_id=ctx["categories"][category_key].id if category_key else None,
        opened_at=opened_at,
    )
    ctx["db"].add(ticket)
    await ctx["db"].flush()
    return ticket.id


def act_as(ctx, key: str) -> None:
    ctx["state"]["user"] = ctx["users"][key]


async def fetch(ctx, user: str, params: list[tuple[str, object]] | None = None, path: str = "/api/v1/tickets"):
    act_as(ctx, user)
    query = [("page_size", 100), *(params or [])]
    return await ctx["client"].get(path, params=query)


async def ids(ctx, user: str, params: list[tuple[str, object]] | None = None) -> set[int]:
    response = await fetch(ctx, user, params)
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


async def seed_matrix(ctx) -> dict[str, int]:
    return {
        "new_low": await make(ctx, status="NEW", priority=5),
        "assigned_lowmed": await make(ctx, status="ASSIGNED", priority=4, assignee="A_tech", type_key="A1", category_key="A1"),
        "planned_medium": await make(ctx, status="PLANNED", priority=3, assignee="A_tech2", type_key="A2", category_key="A2", requester="A_requester2"),
        "pending_high": await make(ctx, status="PENDING", priority=2, assignee="A_tech", type_key="A1", category_key="A2"),
        "solved_critical": await make(ctx, status="SOLVED", priority=1, assignee="A_tech2", requester="A_requester2"),
        "closed_medium": await make(ctx, status="CLOSED", priority=3, assignee="A_tech"),
    }


def iso(value: datetime) -> str:
    return value.isoformat()


@pytest.mark.asyncio
async def test_status_filter_accepts_labels_and_stored_values_and_multi_select(part4) -> None:
    m = await seed_matrix(part4)
    assert await ids(part4, "A_admin", [("status", "Open")]) == {m["new_low"]}
    assert await ids(part4, "A_admin", [("status", "open")]) == {m["new_low"]}
    assert await ids(part4, "A_admin", [("status", "PLANNED")]) == {m["planned_medium"]}
    assert await ids(part4, "A_admin", [("status", "In Progress")]) == {m["planned_medium"]}
    assert await ids(part4, "A_admin", [("status", "On Hold")]) == {m["pending_high"]}
    assert await ids(part4, "A_admin", [("status", "Resolved"), ("status", "Closed")]) == {m["solved_critical"], m["closed_medium"]}
    assert await ids(part4, "A_admin", [("status", "Open"), ("status", "Assigned")]) == {m["new_low"], m["assigned_lowmed"]}


@pytest.mark.asyncio
async def test_status_label_mapping_is_one_table_and_ticket_rows_carry_it(part4) -> None:
    m = await seed_matrix(part4)
    assert [label for label, _ in STATUS_LABELS] == ["Open", "Assigned", "In Progress", "On Hold", "Resolved", "Closed"]
    response = await fetch(part4, "A_admin")
    labels = {item["id"]: item["status_label"] for item in response.json()["items"]}
    assert labels[m["new_low"]] == "Open"
    assert labels[m["planned_medium"]] == "In Progress"
    assert labels[m["pending_high"]] == "On Hold"
    assert labels[m["solved_critical"]] == "Resolved"
    stored = {item["id"]: item["status"] for item in response.json()["items"]}
    assert stored[m["planned_medium"]] == "PLANNED"


@pytest.mark.asyncio
async def test_workflow_defined_states_map_to_the_nearest_label_and_filter_by_it(part4) -> None:
    assert status_label("Waiting for vendor") == "On Hold"
    assert status_label("in_review") == "In Progress"
    assert status_label("Cancelled") == "Closed"
    assert status_label("Fix verified") == "Resolved"
    part4["db"].add(
        TicketWorkflow(
            entity_id=part4["entities"]["A"].id,
            record_type="ticket",
            states=["NEW", "Waiting for vendor", "In review", "CLOSED"],
            transitions={},
            approval_requirements={},
            is_active=True,
        )
    )
    waiting = await make(part4, status="Waiting for vendor")
    review = await make(part4, status="In review")
    hold = await make(part4, status="PENDING")
    assert await ids(part4, "A_admin", [("status", "On Hold")]) == {waiting, hold}
    assert await ids(part4, "A_admin", [("status", "In Progress")]) == {review}
    assert await ids(part4, "A_admin", [("status", "Waiting for vendor")]) == {waiting}
    rows = {item["id"]: item["status_label"] for item in (await fetch(part4, "A_admin")).json()["items"]}
    assert rows[waiting] == "On Hold" and rows[review] == "In Progress"


@pytest.mark.asyncio
async def test_priority_filter_low_matches_four_and_five(part4) -> None:
    m = await seed_matrix(part4)
    assert await ids(part4, "A_admin", [("priority", "Low")]) == {m["new_low"], m["assigned_lowmed"]}
    assert await ids(part4, "A_admin", [("priority", "low")]) == {m["new_low"], m["assigned_lowmed"]}
    assert await ids(part4, "A_admin", [("priority", "Medium")]) == {m["planned_medium"], m["closed_medium"]}
    assert await ids(part4, "A_admin", [("priority", "High")]) == {m["pending_high"]}
    assert await ids(part4, "A_admin", [("priority", "Critical")]) == {m["solved_critical"]}
    assert await ids(part4, "A_admin", [("priority", "High"), ("priority", "Critical")]) == {m["pending_high"], m["solved_critical"]}
    assert await ids(part4, "A_admin", [("priority", 4)]) == {m["assigned_lowmed"]}
    assert await ids(part4, "A_admin", [("priority", 5)]) == {m["new_low"]}


@pytest.mark.asyncio
async def test_assignee_requester_type_and_category_filters_alone_and_multi_select(part4) -> None:
    m = await seed_matrix(part4)
    users = part4["users"]
    assert await ids(part4, "A_admin", [("assignee_id", users["A_tech"].id)]) == {m["assigned_lowmed"], m["pending_high"], m["closed_medium"]}
    assert await ids(part4, "A_admin", [("assignee_id", users["A_tech"].id), ("assignee_id", users["A_tech2"].id)]) == {
        m["assigned_lowmed"], m["pending_high"], m["closed_medium"], m["planned_medium"], m["solved_critical"],
    }
    assert await ids(part4, "A_admin", [("requester_id", users["A_requester2"].id)]) == {m["planned_medium"], m["solved_critical"]}
    assert await ids(part4, "A_admin", [("ticket_type_id", part4["types"]["A1"].id)]) == {m["assigned_lowmed"], m["pending_high"]}
    assert await ids(part4, "A_admin", [("ticket_type_id", part4["types"]["A1"].id), ("ticket_type_id", part4["types"]["A2"].id)]) == {
        m["assigned_lowmed"], m["pending_high"], m["planned_medium"],
    }
    assert await ids(part4, "A_admin", [("category_id", part4["categories"]["A2"].id)]) == {m["planned_medium"], m["pending_high"]}
    assert await ids(part4, "A_admin", [("category_id", part4["categories"]["A1"].id), ("category_id", part4["categories"]["A2"].id)]) == {
        m["assigned_lowmed"], m["planned_medium"], m["pending_high"],
    }


@pytest.mark.asyncio
async def test_filters_combine_with_and_and_search(part4) -> None:
    m = await seed_matrix(part4)
    users = part4["users"]
    combined = [("assignee_id", users["A_tech"].id), ("priority", "Medium"), ("status", "Closed")]
    assert await ids(part4, "A_admin", combined) == {m["closed_medium"]}
    assert await ids(part4, "A_admin", [("assignee_id", users["A_tech"].id), ("priority", "Critical")]) == set()
    assert await ids(part4, "A_admin", [("category_id", part4["categories"]["A2"].id), ("status", "On Hold"), ("ticket_type_id", part4["types"]["A1"].id)]) == {m["pending_high"]}
    assert await ids(part4, "A_admin", [("status", "Open"), ("search", f"{m['new_low']}")]) == {m["new_low"]}
    assert await ids(part4, "A_admin", [("status", "Closed"), ("search", f"{m['new_low']}")]) == set()


@pytest.mark.asyncio
async def test_created_date_range_is_inclusive_and_timezone_aware(part4) -> None:
    early = await make(part4, opened_at=BASE - timedelta(days=5))
    edge_start = await make(part4, opened_at=BASE)
    middle = await make(part4, opened_at=BASE + timedelta(days=1))
    edge_end = await make(part4, opened_at=BASE + timedelta(days=2))
    late = await make(part4, opened_at=BASE + timedelta(days=9))
    window = [("created_from", iso(BASE)), ("created_to", iso(BASE + timedelta(days=2)))]
    assert await ids(part4, "A_admin", window) == {edge_start, middle, edge_end}
    assert await ids(part4, "A_admin", [("created_from", iso(BASE + timedelta(microseconds=1)))]) == {middle, edge_end, late}
    assert await ids(part4, "A_admin", [("created_to", iso(BASE - timedelta(microseconds=1)))]) == {early}
    ist = timezone_offset_ist()
    same_instant = BASE.astimezone(ist)
    assert await ids(part4, "A_admin", [("created_from", iso(same_instant)), ("created_to", iso(same_instant))]) == {edge_start}


def timezone_offset_ist():
    from datetime import timezone

    return timezone(timedelta(hours=5, minutes=30))


@pytest.mark.asyncio
async def test_updated_date_range_uses_last_activity_and_is_inclusive(part4) -> None:
    quiet = await make(part4, opened_at=BASE - timedelta(days=20))
    active = await make(part4, opened_at=BASE - timedelta(days=20))
    touched = BASE - timedelta(days=2)
    part4["db"].add(TicketFollowup(ticket_id=active, author_id=part4["users"]["A_admin"].id, body="<p>Update</p>", is_private=True, created_at=touched))
    await part4["db"].flush()
    assert await ids(part4, "A_admin", [("updated_from", iso(BASE - timedelta(days=3)))]) == {active}
    assert await ids(part4, "A_admin", [("updated_from", iso(touched)), ("updated_to", iso(touched))]) == {active}
    assert await ids(part4, "A_admin", [("updated_from", iso(touched + timedelta(microseconds=1))), ("updated_to", iso(BASE))]) == set()
    assert quiet in await ids(part4, "A_admin", [("updated_to", iso(BASE - timedelta(days=10)))])
    assert active not in await ids(part4, "A_admin", [("updated_to", iso(BASE - timedelta(days=10)))])


@pytest.mark.asyncio
async def test_empty_attachment_only_followups_do_not_count_as_updates(part4) -> None:
    ticket = await make(part4, opened_at=BASE - timedelta(days=20))
    part4["db"].add(TicketFollowup(ticket_id=ticket, author_id=part4["users"]["A_admin"].id, body="", is_private=False, created_at=BASE))
    await part4["db"].flush()
    assert ticket not in await ids(part4, "A_admin", [("updated_from", iso(BASE - timedelta(days=1)))])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        [("status", "Bogus")],
        [("status", "")],
        [("priority", "Urgent")],
        [("priority", 0)],
        [("priority", 6)],
        [("assignee_id", "abc")],
        [("assignee_id", 0)],
        [("requester_id", -4)],
        [("ticket_type_id", 99999999999)],
        [("category_id", "1.5")],
        [("created_from", "2026-03-10T00:00:00")],
        [("created_to", "2026-03-10")],
        [("updated_from", "not-a-date")],
        [("updated_to", "2026-03-10T00:00:00")],
        [("created_from", "2026-03-12T00:00:00+00:00"), ("created_to", "2026-03-10T00:00:00+00:00")],
        [("updated_from", "2026-03-12T00:00:00+00:00"), ("updated_to", "2026-03-10T00:00:00+00:00")],
    ],
)
async def test_malformed_filter_values_return_422(part4, params) -> None:
    response = await fetch(part4, "A_admin", params)
    assert response.status_code == 422, response.text
    counts = await fetch(part4, "A_admin", params, path="/api/v1/tickets/queue-counts")
    assert counts.status_code == 422


@pytest.mark.asyncio
async def test_technician_filters_stay_inside_technician_visibility(part4) -> None:
    m = await seed_matrix(part4)
    users = part4["users"]
    visible = await ids(part4, "A_tech")
    assert visible == {m["assigned_lowmed"], m["pending_high"], m["closed_medium"]}
    assert await ids(part4, "A_tech", [("assignee_id", users["A_tech2"].id)]) == set()
    assert await ids(part4, "A_tech", [("status", "Open")]) == set()
    assert await ids(part4, "A_tech", [("priority", "Low")]) == {m["assigned_lowmed"]}
    assert await ids(part4, "A_tech", [("assignee_id", users["A_tech"].id), ("assignee_id", users["A_tech2"].id)]) == visible
    admin_view = await ids(part4, "A_admin", [("assignee_id", users["A_tech"].id)])
    assert admin_view == visible
    for params in ([("status", "On Hold")], [("category_id", part4["categories"]["A2"].id)], [("created_from", iso(BASE))]):
        assert await ids(part4, "A_tech", params) <= visible


@pytest.mark.asyncio
async def test_requester_may_filter_only_by_status_priority_and_created_date(part4) -> None:
    m = await seed_matrix(part4)
    users = part4["users"]
    own = {m["new_low"], m["assigned_lowmed"], m["pending_high"], m["closed_medium"]}
    assert await ids(part4, "A_requester") == own
    assert await ids(part4, "A_requester", [("status", "Open")]) == {m["new_low"]}
    assert await ids(part4, "A_requester", [("priority", "Low")]) == {m["new_low"], m["assigned_lowmed"]}
    assert await ids(part4, "A_requester", [("created_from", iso(BASE)), ("created_to", iso(BASE))]) == own
    for params in (
        [("assignee_id", users["A_tech"].id)],
        [("requester_id", users["A_requester2"].id)],
        [("ticket_type_id", part4["types"]["A1"].id)],
        [("category_id", part4["categories"]["A1"].id)],
        [("updated_from", iso(BASE))],
        [("updated_to", iso(BASE))],
    ):
        response = await fetch(part4, "A_requester", params)
        assert response.status_code == 403, (params, response.text)
    other_requester = await ids(part4, "A_requester", [("priority", "Critical")])
    assert other_requester == set()


@pytest.mark.asyncio
async def test_filter_options_are_entity_scoped_and_rights_filtered(part4) -> None:
    m = await seed_matrix(part4)
    await make(part4, entity="B", assignee="B_tech", type_key="B1", category_key="B1")
    users = part4["users"]

    admin = (await fetch(part4, "A_admin", path="/api/v1/tickets/filter-options")).json()
    assert [s["value"] for s in admin["statuses"]] == ["Open", "Assigned", "In Progress", "On Hold", "Resolved", "Closed"]
    assert [p["value"] for p in admin["priorities"]] == ["Low", "Medium", "High", "Critical"]
    assert next(p for p in admin["priorities"] if p["value"] == "Low")["matches"] == [4, 5]
    assert {a["id"] for a in admin["assignees"]} == {users["A_tech"].id, users["A_tech2"].id}
    assert {r["id"] for r in admin["requesters"]} == {users["A_requester"].id, users["A_requester2"].id}
    assert {c["id"] for c in admin["categories"]} == {part4["categories"]["A1"].id, part4["categories"]["A2"].id}
    names = {t["id"] for t in admin["ticket_types"]}
    assert {part4["types"]["A1"].id, part4["types"]["A2"].id} <= names and part4["types"]["B1"].id not in names
    assert all(set(person) == {"id", "name", "is_active"} for person in admin["assignees"] + admin["requesters"])

    tech = (await fetch(part4, "A_tech", path="/api/v1/tickets/filter-options")).json()
    assert {a["id"] for a in tech["assignees"]} == {users["A_tech"].id}
    assert {r["id"] for r in tech["requesters"]} == {users["A_requester"].id}
    assert m["new_low"]

    requester = (await fetch(part4, "A_requester", path="/api/v1/tickets/filter-options")).json()
    assert set(requester) == {"statuses", "priorities", "date_fields"}
    assert requester["date_fields"] == ["created"]
    assert "Name A_tech" not in str(requester)

    foreign = (await fetch(part4, "B_admin", path="/api/v1/tickets/filter-options")).json()
    assert {a["id"] for a in foreign["assignees"]} == {users["B_tech"].id}
    assert {c["id"] for c in foreign["categories"]} == {part4["categories"]["B1"].id}


@pytest.mark.asyncio
async def test_filters_never_cross_entities(part4) -> None:
    m = await seed_matrix(part4)
    foreign_ticket = await make(part4, entity="B", status="NEW", priority=5, assignee="B_tech", type_key="B1", category_key="B1")
    users = part4["users"]
    assert foreign_ticket not in await ids(part4, "A_admin", [("priority", "Low")])
    assert await ids(part4, "A_admin", [("assignee_id", users["B_tech"].id)]) == set()
    assert await ids(part4, "A_admin", [("category_id", part4["categories"]["B1"].id)]) == set()
    assert await ids(part4, "A_admin", [("ticket_type_id", part4["types"]["B1"].id)]) == set()
    assert await ids(part4, "A_admin", [("requester_id", users["B_requester"].id)]) == set()
    assert await ids(part4, "B_admin", [("priority", "Low")]) == {foreign_ticket}
    assert await ids(part4, "B_admin", [("assignee_id", users["A_tech"].id)]) == set()
    counts = (await fetch(part4, "B_admin", [("priority", "Low")], path="/api/v1/tickets/queue-counts")).json()
    assert counts["all"] == 1
    assert m["new_low"] not in await ids(part4, "B_admin")


@pytest.mark.asyncio
async def test_filters_work_with_pagination_and_sorting(part4) -> None:
    for priority in (5, 4, 5, 4, 5):
        await make(part4, priority=priority, status="NEW")
    await make(part4, priority=2)
    act_as(part4, "A_admin")
    first = (await part4["client"].get("/api/v1/tickets", params=[("priority", "Low"), ("page_size", 2), ("page", 1), ("sort", "priority"), ("direction", "asc")])).json()
    third = (await part4["client"].get("/api/v1/tickets", params=[("priority", "Low"), ("page_size", 2), ("page", 3), ("sort", "priority"), ("direction", "asc")])).json()
    assert first["total"] == 5 and first["pages"] == 3
    assert [item["priority"] for item in first["items"]] == [4, 4]
    assert len(third["items"]) == 1 and third["items"][0]["priority"] == 5


@pytest.mark.asyncio
async def test_queue_and_team_counts_agree_with_the_filtered_list(part4) -> None:
    m = await seed_matrix(part4)
    users = part4["users"]
    filters = [("priority", "Low"), ("priority", "Medium")]
    unfiltered = (await fetch(part4, "A_admin", path="/api/v1/tickets/queue-counts")).json()
    counts = (await fetch(part4, "A_admin", filters, path="/api/v1/tickets/queue-counts")).json()
    assert counts["all"] == len(await ids(part4, "A_admin", filters)) == 4
    assert counts["unassigned"] == len(await ids(part4, "A_admin", [*filters, ("queue", "unassigned")])) == 1
    assert counts["mine"] == len(await ids(part4, "A_admin", [*filters, ("queue", "mine")]))
    assert unfiltered["all"] == 6 and counts["all"] < unfiltered["all"]
    teams = (await fetch(part4, "A_admin", filters, path="/api/v1/tickets/team-counts")).json()
    assert next(row for row in teams if row["team_id"] is None)["count"] == counts["unassigned"]

    tech_counts = (await fetch(part4, "A_tech", [("status", "Closed")], path="/api/v1/tickets/queue-counts")).json()
    assert tech_counts["all"] == len(await ids(part4, "A_tech", [("status", "Closed")])) == 1
    assert tech_counts["mine"] == len(await ids(part4, "A_tech", [("status", "Closed"), ("queue", "mine")])) == 1
    assert m["closed_medium"] and users["A_tech"].id


@pytest.mark.asyncio
async def test_requester_team_counts_reject_staff_filters_but_accept_basic_ones(part4) -> None:
    await seed_matrix(part4)
    ok = await fetch(part4, "A_requester", [("priority", "Low")], path="/api/v1/tickets/team-counts")
    assert ok.status_code == 200
    denied = await fetch(part4, "A_requester", [("assignee_id", part4["users"]["A_tech"].id)], path="/api/v1/tickets/team-counts")
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_saved_filter_profile_round_trip_with_new_keys_and_old_profiles_stay_valid(part4) -> None:
    act_as(part4, "A_admin")
    client = part4["client"]
    users = part4["users"]
    old = await client.post("/api/v1/tickets/list-profiles", json={"name": "Old", "kind": "filter", "config": {"search": "vpn", "status": "NEW", "priority": 3, "sort": "priority", "direction": "desc", "page_size": 25}})
    assert old.status_code == 201, old.text
    config = {
        "status": ["Open", "On Hold"],
        "priority": ["Low", "Critical"],
        "assignee_id": [users["A_tech"].id, users["A_tech2"].id],
        "requester_id": users["A_requester"].id,
        "ticket_type_id": [part4["types"]["A1"].id],
        "category_id": [part4["categories"]["A1"].id, part4["categories"]["A2"].id],
        "created_from": "2026-03-01T00:00:00+05:30",
        "created_to": "2026-03-31T23:59:59Z",
        "updated_from": "2026-03-02T00:00:00+00:00",
        "updated_to": "2026-03-30T00:00:00+00:00",
        "page_size": 50,
    }
    created = await client.post("/api/v1/tickets/list-profiles", json={"name": "Everything", "kind": "filter", "config": config})
    assert created.status_code == 201, created.text
    fetched = await client.get(f"/api/v1/tickets/list-profiles/{created.json()['id']}")
    assert fetched.json()["config"] == config
    patched = await client.patch(f"/api/v1/tickets/list-profiles/{created.json()['id']}", json={"config": {**config, "priority": "High"}})
    assert patched.status_code == 200 and patched.json()["config"]["priority"] == "High"
    listed = {profile["name"]: profile["config"] for profile in (await client.get("/api/v1/tickets/list-profiles")).json()}
    assert listed["Old"]["status"] == "NEW" and listed["Old"]["priority"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config",
    [
        {"status": "Bogus"},
        {"status": ["Open", 7]},
        {"priority": "Urgent"},
        {"priority": True},
        {"priority": 9},
        {"assignee_id": [0]},
        {"category_id": ["x"]},
        {"created_from": "2026-03-01T00:00:00"},
        {"created_from": "2026-03-05T00:00:00+00:00", "created_to": "2026-03-01T00:00:00+00:00"},
        {"unknown_filter": 1},
    ],
)
async def test_saved_filter_profile_rejects_malformed_values(part4, config) -> None:
    act_as(part4, "A_admin")
    response = await part4["client"].post("/api/v1/tickets/list-profiles", json={"name": "Bad", "kind": "filter", "config": config})
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_saved_profile_config_applies_to_the_list_the_same_way_as_query_params(part4) -> None:
    m = await seed_matrix(part4)
    act_as(part4, "A_admin")
    config = {"priority": ["Low"], "assignee_id": [part4["users"]["A_tech"].id]}
    saved = (await part4["client"].post("/api/v1/tickets/list-profiles", json={"name": "Mine low", "kind": "filter", "config": config})).json()["config"]
    params = [("priority", value) for value in saved["priority"]] + [("assignee_id", value) for value in saved["assignee_id"]]
    assert await ids(part4, "A_admin", params) == {m["assigned_lowmed"]}
