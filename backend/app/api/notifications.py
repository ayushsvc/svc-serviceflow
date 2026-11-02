from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import or_, select

from app.api.tickets import ticket_visibility_scope
from app.deps import CurrentUser, DbSession
from app.models import Ticket, TicketFollowup, User
from app.services.attachments import can_see_private_items, live_followup_filter

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(user: CurrentUser, db: DbSession, days: int = Query(14, ge=1, le=60), limit: int = Query(30, ge=1, le=100)) -> list[dict[str, object]]:
    """In-app activity feed derived from existing data (no stored notification rows).

    Items are updates by other people on tickets the caller can see and is involved in
    (as requester or assignee). Private items are returned to staff only.
    """
    since = datetime.now(UTC) - timedelta(days=days)
    query = (
        select(TicketFollowup, Ticket, User.name)
        .join(Ticket, Ticket.id == TicketFollowup.ticket_id)
        .join(User, User.id == TicketFollowup.author_id)
        .where(
            Ticket.entity_id == user.entity_id,
            Ticket.deleted_at.is_(None),
            ticket_visibility_scope(user),
            or_(Ticket.requester_id == user.id, Ticket.assignee_id == user.id),
            TicketFollowup.author_id != user.id,
            TicketFollowup.created_at >= since,
            live_followup_filter(),
        )
        .order_by(TicketFollowup.created_at.desc())
        .limit(limit)
    )
    if not can_see_private_items(user):
        query = query.where(TicketFollowup.is_private.is_(False))
    rows = (await db.execute(query)).all()
    return [
        {
            "id": f"followup-{followup.id}",
            "ticket_id": ticket.id,
            "ticket_title": ticket.title,
            "kind": "internal" if followup.is_private else "reply",
            "actor_name": author_name,
            "summary": (followup.body or "Attached files")[:200],
            "created_at": followup.created_at,
        }
        for followup, ticket, author_name in rows
    ]
