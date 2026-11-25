import asyncio
from datetime import UTC, datetime
from email.message import EmailMessage

from croniter import croniter
from jinja2 import Template
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.db import SessionLocal
from app.core.config import get_settings
from app.demo_data import DEMO_TITLES
from app.models import AuditLog, NotificationTemplate, RecurringTicket, Ticket, User
from app.services.content import sanitize_rich_text
from app.services.email import send_email
from app.services.tickets import calculate_priority, priority_label
from app.workers.celery_app import celery_app

_worker_loop: asyncio.AbstractEventLoop | None = None


def _notification_ticket(ticket: Ticket, *, requester: bool) -> dict[str, object]:
    values = {column.name: getattr(ticket, column.name) for column in Ticket.__table__.columns}
    label = priority_label(ticket.priority, requester=requester)
    values["priority_label"] = label
    if requester:
        values["priority"] = label
    return values


def _run_async(coroutine):
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_worker_loop)
    return _worker_loop.run_until_complete(coroutine)


@celery_app.task
def check_sla_breaches() -> dict[str, str]:
    _run_async(_check_sla_breaches())
    return {"status": "checked"}


async def _check_sla_breaches() -> None:
    from app.services.sla_engine import NUMERIC_TO_PRIORITY
    from app.services.sla_service import evaluate_ticket_live, get_ticket_policy_and_calendar

    breached_ticket_ids: list[int] = []
    warn1_ticket_ids: list[int] = []
    warn2_ticket_ids: list[int] = []

    # In development the fixed demo tickets are exempt (exact titles only); production is unchanged.
    exempt_titles = sorted(DEMO_TITLES) if get_settings().debug else []
    async with SessionLocal() as db:
        now = datetime.now(UTC)
        query = select(Ticket).where(
            Ticket.status.not_in(["SOLVED", "CLOSED"]),
        )
        if exempt_titles:
            query = query.where(Ticket.title.not_in(exempt_titles))
        result = list(await db.scalars(query))

        for ticket in result:
            eval_res = await evaluate_ticket_live(ticket, db)
            health = eval_res.get("health", "on_track")
            res_info = eval_res.get("resolution", {})
            resp_info = eval_res.get("response", {})
            res_pct = res_info.get("pct", 0.0)

            # 1. Check Response Breach
            if resp_info.get("breached") and not ticket.response_breach_triggered:
                ticket.response_breach_triggered = True
                db.add(
                    AuditLog(
                        entity="sla",
                        entity_id=ticket.id,
                        action="sla_response_breached",
                        diff={"tto_due_at": ticket.tto_due_at.isoformat() if ticket.tto_due_at else None},
                    )
                )

            # 2. Check Resolution Breach (100% or overdue)
            if res_info.get("breached") or health == "breached":
                if not ticket.breach_triggered:
                    ticket.breach_triggered = True
                    policy, _ = await get_ticket_policy_and_calendar(ticket, db)
                    auto_escalate = getattr(policy, "auto_escalate_on_breach", True) if policy else True
                    if auto_escalate and ticket.priority > 1:
                        ticket.priority = max(ticket.priority - 1, 1)
                        ticket.priority_code = NUMERIC_TO_PRIORITY.get(ticket.priority, "P1")
                    db.add(
                        AuditLog(
                            entity="sla",
                            entity_id=ticket.id,
                            action="sla_breached",
                            diff={
                                "priority": ticket.priority,
                                "priority_code": ticket.priority_code,
                                "ttr_due_at": ticket.ttr_due_at.isoformat() if ticket.ttr_due_at else None,
                            },
                        )
                    )
                    breached_ticket_ids.append(ticket.id)

            # 3. Check Stage 2 Warning (75%)
            elif (res_pct >= 75.0 or health == "warning_2") and not ticket.warn2_triggered:
                ticket.warn2_triggered = True
                db.add(
                    AuditLog(
                        entity="sla",
                        entity_id=ticket.id,
                        action="sla_warning_75",
                        diff={"resolution_pct": res_pct},
                    )
                )
                warn2_ticket_ids.append(ticket.id)

            # 4. Check Stage 1 Warning (50%)
            elif (res_pct >= 50.0 or health == "warning_1") and not ticket.warn1_triggered:
                ticket.warn1_triggered = True
                db.add(
                    AuditLog(
                        entity="sla",
                        entity_id=ticket.id,
                        action="sla_warning_50",
                        diff={"resolution_pct": res_pct},
                    )
                )
                warn1_ticket_ids.append(ticket.id)

        await db.commit()

    for ticket_id in breached_ticket_ids:
        send_ticket_notification.delay(ticket_id, "sla_breach")
    for ticket_id in warn2_ticket_ids:
        send_ticket_notification.delay(ticket_id, "sla_warning_2")
    for ticket_id in warn1_ticket_ids:
        send_ticket_notification.delay(ticket_id, "sla_warning_1")


@celery_app.task
def create_recurring_tickets() -> dict[str, int]:
    created = _run_async(_create_recurring_tickets())
    return {"created": created}


async def _create_recurring_tickets() -> int:
    async with SessionLocal() as db:
        now = datetime.now(UTC)
        recurring_items = list(await db.scalars(select(RecurringTicket).where(RecurringTicket.is_active.is_(True), RecurringTicket.next_run_at <= now)))
        for recurring in recurring_items:
            db.add(Ticket(title=recurring.title, description=sanitize_rich_text(recurring.description), entity_id=recurring.entity_id, requester_id=recurring.requester_id, urgency=recurring.urgency, impact=recurring.impact, priority=calculate_priority(recurring.urgency, recurring.impact), source="recurring"))
            recurring.next_run_at = croniter(recurring.cron_expr, now).get_next(datetime)
        await db.commit()
        return len(recurring_items)


@celery_app.task
def send_ticket_notification(ticket_id: int, event: str) -> dict[str, str]:
    _run_async(_send_ticket_notification(ticket_id, event))
    return {"status": "sent"}


@celery_app.task
def send_user_ticket_notification(ticket_id: int, user_id: int, event: str) -> dict[str, str]:
    _run_async(_send_user_ticket_notification(ticket_id, user_id, event))
    return {"status": "sent"}


async def _send_ticket_notification(ticket_id: int, event: str) -> None:
    async with SessionLocal() as db:
        ticket = await db.scalar(select(Ticket).where(Ticket.id == ticket_id))
        if ticket is None:
            return
        recipient = await db.scalar(
            select(User).options(selectinload(User.profile)).where(User.id == ticket.requester_id)
        )
        template = await db.scalar(select(NotificationTemplate).where(NotificationTemplate.entity_id == ticket.entity_id, NotificationTemplate.event == event))
        if recipient is None or template is None:
            return
        requester = (recipient.profile.rights or {}).get("ticket:own_only") is True
        context = {"ticket": _notification_ticket(ticket, requester=requester), "user": recipient, "event": event}
        message = EmailMessage()
        message["From"] = get_settings().mail_from
        message["To"] = recipient.email
        message["Subject"] = Template(template.subject).render(**context)
        message.set_content(Template(template.body_jinja).render(**context))
        send_email(message)


async def _send_user_ticket_notification(ticket_id: int, user_id: int, event: str) -> None:
    async with SessionLocal() as db:
        ticket = await db.scalar(select(Ticket).where(Ticket.id == ticket_id))
        recipient = await db.scalar(
            select(User)
            .options(selectinload(User.profile))
            .where(User.id == user_id, User.is_active.is_(True))
        )
        if ticket is None or recipient is None or recipient.entity_id != ticket.entity_id:
            return
        template = await db.scalar(
            select(NotificationTemplate).where(
                NotificationTemplate.entity_id == ticket.entity_id,
                NotificationTemplate.event == event,
            )
        )
        if template is None and event in {"assigned_to_you", "assignment_removed", "team_assigned"}:
            template = await db.scalar(
                select(NotificationTemplate).where(
                    NotificationTemplate.entity_id == ticket.entity_id,
                    NotificationTemplate.event == "assigned",
                )
            )
        if template is None:
            return
        requester = (recipient.profile.rights or {}).get("ticket:own_only") is True
        context = {"ticket": _notification_ticket(ticket, requester=requester), "user": recipient, "event": event}
        message = EmailMessage()
        message["From"] = get_settings().mail_from
        message["To"] = recipient.email
        message["Subject"] = Template(template.subject).render(**context)
        message.set_content(Template(template.body_jinja).render(**context))
        send_email(message)


async def _auto_close_solved_tickets(auto_close_hours: int = 120) -> list[int]:
    """HaloITSM-style automatic closure of resolved tickets after inactivity window (default 5 days)."""
    from datetime import timedelta
    closed_ids: list[int] = []
    async with SessionLocal() as db:
        now = datetime.now(UTC)
        cutoff = now - timedelta(hours=auto_close_hours)
        query = select(Ticket).where(
            Ticket.status == "SOLVED",
            Ticket.solved_at.is_not(None),
            Ticket.solved_at <= cutoff,
        )
        tickets = list((await db.scalars(query)).all())
        for ticket in tickets:
            ticket.status = "CLOSED"
            ticket.closed_at = now
            closed_ids.append(ticket.id)
            db.add(
                AuditLog(
                    entity="ticket",
                    entity_id=ticket.id,
                    action="closed",
                    actor_id=ticket.assignee_id or ticket.user_id,
                    diff={"status": ["CLOSED", "SOLVED"], "reason": f"Auto-closed after {auto_close_hours}h in SOLVED"},
                )
            )
        if tickets:
            await db.commit()
    return closed_ids


async def _nudge_pending_tickets(pending_hours: int = 48) -> list[int]:
    """Check tickets waiting in PENDING and record nudge reminder if awaiting customer for > 48h."""
    from datetime import timedelta
    nudged_ids: list[int] = []
    async with SessionLocal() as db:
        now = datetime.now(UTC)
        query = select(Ticket).where(Ticket.status == "PENDING")
        tickets = list((await db.scalars(query)).all())
        for ticket in tickets:
            custom = dict(ticket.custom_fields or {})
            pending_since_str = custom.get("pending_since")
            if not pending_since_str:
                continue
            try:
                pending_since = datetime.fromisoformat(str(pending_since_str))
                if pending_since.tzinfo is None:
                    pending_since = pending_since.replace(tzinfo=UTC)
            except Exception:
                continue

            if now - pending_since >= timedelta(hours=pending_hours):
                last_nudged = custom.get("last_nudged_at")
                if not last_nudged:
                    custom["last_nudged_at"] = now.isoformat()
                    ticket.custom_fields = custom
                    nudged_ids.append(ticket.id)
                    db.add(
                        AuditLog(
                            entity="ticket",
                            entity_id=ticket.id,
                            action="nudge_pending",
                            actor_id=ticket.assignee_id or ticket.user_id,
                            diff={"reason": f"Ticket on hold in PENDING for > {pending_hours}h awaiting update"},
                        )
                    )
        if nudged_ids:
            await db.commit()
    return nudged_ids


def _safe_schedule(coro):
    try:
        loop = asyncio.get_running_loop()
        return loop.create_task(coro)
    except RuntimeError:
        return _run_async(coro)


# Native in-process execution for .delay() without needing external Celery workers or Redis brokers
send_ticket_notification.delay = lambda ticket_id, event, *a, **kw: _safe_schedule(_send_ticket_notification(ticket_id, event))
send_user_ticket_notification.delay = lambda ticket_id, user_id, event, *a, **kw: _safe_schedule(_send_user_ticket_notification(ticket_id, user_id, event))
check_sla_breaches.delay = lambda *a, **kw: _safe_schedule(_check_sla_breaches())
create_recurring_tickets.delay = lambda *a, **kw: _safe_schedule(_create_recurring_tickets())


