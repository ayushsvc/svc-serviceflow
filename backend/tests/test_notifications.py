from app.models import TicketFollowup
from tests.test_release1_part4 import act_as, make, part4  # noqa: F401


async def _reply(ctx, ticket_id: int, author: str, body: str, private: bool = False) -> None:
    ctx["db"].add(TicketFollowup(ticket_id=ticket_id, author_id=ctx["users"][author].id, body=body, is_private=private))
    await ctx["db"].flush()


async def test_notifications_show_others_replies_on_my_tickets(part4):
    ticket_id = await make(part4, status="ASSIGNED", assignee="A_tech")
    await _reply(part4, ticket_id, "A_tech", "Public reply from tech")
    await _reply(part4, ticket_id, "A_tech", "Internal change note", private=True)
    await _reply(part4, ticket_id, "A_requester", "My own reply")

    act_as(part4, "A_requester")
    items = (await part4["client"].get("/api/v1/notifications")).json()
    assert [item["summary"] for item in items] == ["Public reply from tech"]
    assert items[0]["ticket_id"] == ticket_id and items[0]["actor_name"] == "Name A_tech"

    act_as(part4, "A_tech")
    items = (await part4["client"].get("/api/v1/notifications")).json()
    assert {item["summary"] for item in items} == {"My own reply"}


async def test_notifications_staff_see_internal_and_other_entities_never(part4):
    ticket_id = await make(part4, status="ASSIGNED", assignee="A_tech")
    await _reply(part4, ticket_id, "A_admin", "Internal from admin", private=True)
    foreign = await make(part4, entity="B", status="ASSIGNED", assignee="B_tech")
    await _reply(part4, foreign, "B_admin", "Foreign reply")

    act_as(part4, "A_tech")
    items = (await part4["client"].get("/api/v1/notifications")).json()
    assert [item["summary"] for item in items] == ["Internal from admin"]
    assert items[0]["kind"] == "internal"

    act_as(part4, "A_requester")
    assert (await part4["client"].get("/api/v1/notifications")).json() == []
    act_as(part4, "B_requester")
    assert [item["summary"] for item in (await part4["client"].get("/api/v1/notifications")).json()] == ["Foreign reply"]
