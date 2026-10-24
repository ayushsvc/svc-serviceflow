import asyncio
import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage

from croniter import croniter
from jinja2 import Template
from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.config import get_settings
from app.models import AuditLog, NotificationTemplate, RecurringTicket, Ticket, User
from app.services.content import sanitize_rich_text
from app.services.tickets import calculate_priority
from app.workers.celery_app import celery_app

_worker_loop: asyncio.AbstractEventLoop | None = None


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
    breached_ticket_ids: list[int] = []
    async with SessionLocal() as db:
        now = datetime.now(UTC)
        result = await db.scalars(
            select(Ticket).where(
                Ticket.status.not_in(["PENDING", "SOLVED", "CLOSED"]),
                ((Ticket.tto_due_at < now) | (Ticket.ttr_due_at < now)),
            )
        )
        for ticket in result:
            ticket.priority = max(ticket.priority, 4)
            notified = await db.scalar(
                select(AuditLog.id).where(
                    AuditLog.entity == "sla_notification",
                    AuditLog.entity_id == ticket.id,
                    AuditLog.action == "breach_sent",
                )
            )
            if notified is None:
                db.add(
                    AuditLog(
                        entity="sla_notification",
                        entity_id=ticket.id,
                        action="breach_sent",
                        diff={},
                    )
                )
                breached_ticket_ids.append(ticket.id)
        await db.commit()
    for ticket_id in breached_ticket_ids:
        send_ticket_notification.delay(ticket_id, "sla_breach")


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


async def _send_ticket_notification(ticket_id: int, event: str) -> None:
    async with SessionLocal() as db:
        ticket = await db.scalar(select(Ticket).where(Ticket.id == ticket_id))
        if ticket is None:
            return
        recipient = await db.scalar(select(User).where(User.id == ticket.requester_id))
        template = await db.scalar(select(NotificationTemplate).where(NotificationTemplate.entity_id == ticket.entity_id, NotificationTemplate.event == event))
        if recipient is None or template is None:
            return
        context = {"ticket": ticket, "user": recipient, "event": event}
        message = EmailMessage()
        message["From"] = get_settings().mail_from
        message["To"] = recipient.email
        message["Subject"] = Template(template.subject).render(**context)
        message.set_content(Template(template.body_jinja).render(**context))
        with smtplib.SMTP(get_settings().smtp_host, get_settings().smtp_port, timeout=10) as smtp:
            smtp.send_message(message)
