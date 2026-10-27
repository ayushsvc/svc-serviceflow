import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app import seed_demo as demo
from app.core.config import get_settings
from app.demo_data import DEMO_TICKETS, DEMO_TITLES
from app.models import (
    AuditLog,
    Entity,
    GroupMember,
    Profile,
    Ticket,
    TicketDocument,
    TicketFollowup,
    TicketTask,
    TicketWorkflow,
    User,
)
from app.seed import seed_entity_defaults
from app.services.attachments import validate_attachment
from app.workers import tasks as worker_tasks

ADMIN_RIGHTS = {
    "*": True,
    "ticket:view_all": True,
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


@pytest.fixture
async def demo_env(monkeypatch, tmp_path):
    monkeypatch.setattr("app.api.attachments.UPLOAD_ROOT", tmp_path)
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        db = AsyncSession(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        suffix = uuid4().hex[:10]
        profiles = {
            "requester": Profile(name=f"Demo requester {suffix}", interface="self_service", rights={"ticket:own_only": True}),
            "technician": Profile(name=f"Demo technician {suffix}", interface="helpdesk", rights=TECHNICIAN_RIGHTS),
            "admin": Profile(name=f"Demo admin {suffix}", interface="helpdesk", rights=ADMIN_RIGHTS),
        }
        entity = Entity(name=f"Demo entity {suffix}")
        db.add_all([*profiles.values(), entity])
        await db.flush()
        seeded = {
            "admin": User(email=f"demo-admin-{suffix}@example.test", password_hash="x", name="Admin", profile_id=profiles["admin"].id, entity_id=entity.id),
            "requester": User(email=f"demo-priya-{suffix}@example.test", password_hash="x", name="Priya", profile_id=profiles["requester"].id, entity_id=entity.id),
            "technician": User(email=f"demo-aarav-{suffix}@example.test", password_hash="x", name="Aarav", profile_id=profiles["technician"].id, entity_id=entity.id),
        }
        db.add_all(seeded.values())
        await db.flush()
        await seed_entity_defaults(db, entity)
        team = await demo._service_desk(db, entity)
        db.add(GroupMember(group_id=team.id, user_id=seeded["technician"].id))
        await db.flush()
        config = demo.DemoConfig(
            entity_name=entity.name,
            admin_email=seeded["admin"].email,
            requester_email=seeded["requester"].email,
            technician_email=seeded["technician"].email,
            requester_profile=profiles["requester"].name,
            technician_profile=profiles["technician"].name,
            tagged=(
                ("req1", f"demo.requester1.{suffix}@example.test", "Demo Requester One", "requester"),
                ("req2", f"demo.requester2.{suffix}@example.test", "Demo Requester Two", "requester"),
                ("tech1", f"demo.technician1.{suffix}@example.test", "Demo Technician One", "technician"),
            ),
        )
        try:
            yield SimpleNamespace(db=db, entity=entity, config=config, seeded=seeded, team=team, root=tmp_path)
        finally:
            await db.close()
            if transaction.is_active:
                await transaction.rollback()
            await engine.dispose()


async def demo_tickets(env) -> list[Ticket]:
    return list((await env.db.scalars(select(Ticket).where(Ticket.entity_id == env.entity.id, Ticket.title.in_(DEMO_TITLES)))).all())


async def ticket_audit_count(env, ids: list[int]) -> int:
    return await env.db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.entity == "ticket", AuditLog.entity_id.in_(ids))) or 0


@pytest.mark.parametrize(
    ("debug", "flag", "argv"),
    [(False, "1", []), (True, None, []), (True, "0", []), (False, "1", ["--remove"]), (True, None, ["--remove"])],
)
def test_demo_seed_refuses_outside_dev_before_opening_any_session(monkeypatch, capsys, debug, flag, argv) -> None:
    monkeypatch.setattr(demo, "get_settings", lambda: SimpleNamespace(debug=debug))
    if flag is None:
        monkeypatch.delenv("ALLOW_DEMO_SEED", raising=False)
    else:
        monkeypatch.setenv("ALLOW_DEMO_SEED", flag)

    def no_session(*args, **kwargs):
        raise AssertionError("a database session was opened")

    monkeypatch.setattr("app.core.db.SessionLocal", no_session)
    assert demo.main(argv) == 1
    assert "Refusing to run" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_demo_seed_creates_expected_data_and_second_run_creates_nothing(demo_env) -> None:
    env = demo_env
    report = await demo.seed_demo(env.db, env.config)
    assert report.blocked == []
    assert report.tickets_created == 15 and report.tickets_existing == 0
    assert len(report.users_created) == 3
    assert len(report.memberships_added) == 1

    tickets = await demo_tickets(env)
    assert len(tickets) == 15
    assert {t.type for t in tickets} == {"INCIDENT", "REQUEST", "PROBLEM", "CHANGE"}
    assert {t.status for t in tickets} == {"NEW", "ASSIGNED", "PLANNED", "PENDING", "SOLVED", "CLOSED"}
    assert {t.priority for t in tickets} == {1, 2, 3, 5}
    users = {u.id: u for u in (await env.db.scalars(select(User).where(User.entity_id == env.entity.id))).all()}
    owned = [t for t in tickets if users[t.requester_id].email == env.config.requester_email]
    assigned = [t for t in tickets if t.assignee_id and users[t.assignee_id].email == env.config.technician_email]
    assert len(owned) >= 4 and len(assigned) >= 4
    assert {users[t.requester_id].email for t in tickets} >= {email for _, email, _, kind in env.config.tagged if kind == "requester"}
    assert sum(1 for t in tickets if t.assignee_id is None) == 4
    assert all(t.status == "NEW" for t in tickets if t.assignee_id is None)

    now = datetime.now(UTC)
    assert all(now - timedelta(days=30) <= t.opened_at <= now for t in tickets)
    assert max(now - t.opened_at for t in tickets) > timedelta(days=25)
    by_title = {t.title: t for t in tickets}
    breached = by_title["[DEMO] Sales mailbox quota exceeded"]
    on_time = by_title["[DEMO] Access to the Finance shared folder"]
    assert breached.solved_at > breached.ttr_due_at
    assert on_time.solved_at <= on_time.ttr_due_at
    assert by_title["[DEMO] Install VS Code for the developers"].closed_at is not None

    ids = [t.id for t in tickets]
    documents = list((await env.db.scalars(select(TicketDocument).where(TicketDocument.ticket_id.in_(ids)))).all())
    assert len(documents) == 5 and len({d.ticket_id for d in documents}) == 4
    assert sum(1 for d in documents if d.followup_id is not None) == 1
    assert {d.content_type for d in documents} == {
        "image/png",
        "application/pdf",
        "text/plain",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    assert len(report.files) == 5 and all(str(env.root) in path for path in report.files)
    assert report.enqueues_suppressed > 0

    followups = list((await env.db.scalars(select(TicketFollowup).where(TicketFollowup.ticket_id.in_(ids)))).all())
    assert report.public_replies == sum(1 for f in followups if not f.is_private) >= 3
    assert all(f.body.startswith("<p>Ticket updated (") or f.body.startswith("Ticket updated (") for f in followups if f.is_private)
    assert await env.db.scalar(select(func.count()).select_from(TicketTask).where(TicketTask.ticket_id.in_(ids))) == 0

    assert report.summary["status"] == {"NEW": 4, "ASSIGNED": 3, "PLANNED": 2, "PENDING": 2, "SOLVED": 2, "CLOSED": 2}

    audit_before = await ticket_audit_count(env, ids)
    files_before = sorted(path.name for path in env.root.iterdir())
    second = await demo.seed_demo(env.db, env.config)
    assert second.tickets_created == 0 and second.tickets_existing == 15
    assert second.users_created == [] and second.memberships_added == [] and second.documents == 0
    assert len(await demo_tickets(env)) == 15
    assert await ticket_audit_count(env, ids) == audit_before
    assert sorted(path.name for path in env.root.iterdir()) == files_before


@pytest.mark.asyncio
async def test_demo_remove_deletes_only_demo_rows_files_and_keeps_audit(demo_env) -> None:
    env = demo_env
    await demo.seed_demo(env.db, env.config)
    ids = [t.id for t in await demo_tickets(env)]
    audit_before = await ticket_audit_count(env, ids)
    assert audit_before > 0

    lookalike = Ticket(
        title="[DEMO] Not one of the fixed demo titles",
        description="<p>A real ticket that only shares the prefix.</p>",
        type="INCIDENT", status="NEW", priority=3, urgency=3, impact=3,
        entity_id=env.entity.id, requester_id=env.seeded["requester"].id,
    )
    ordinary = Ticket(
        title="Ordinary ticket kept by remove",
        description="<p>Another real ticket with an attachment.</p>",
        type="INCIDENT", status="NEW", priority=3, urgency=3, impact=3,
        entity_id=env.entity.id, requester_id=env.seeded["requester"].id,
    )
    env.db.add_all([lookalike, ordinary])
    await env.db.flush()
    kept_file = env.root / "ordinary.png"
    kept_file.write_bytes(demo.make_png())
    env.db.add(TicketDocument(ticket_id=ordinary.id, filename="ordinary.png", path=str(kept_file), uploaded_by=env.seeded["requester"].id, content_type="image/png", size_bytes=10))
    await env.db.commit()
    demo_files = [path for path in env.root.iterdir() if path != kept_file]
    assert len(demo_files) == 5

    result = await demo.remove_demo(env.db, env.config)
    assert result.tickets_removed == 15 and result.documents_removed == 5 and result.files_removed == 5
    assert result.audit_rows_kept == audit_before
    assert await demo_tickets(env) == []
    remaining = {t.title for t in (await env.db.scalars(select(Ticket).where(Ticket.entity_id == env.entity.id))).all()}
    assert remaining == {lookalike.title, ordinary.title}
    assert kept_file.exists() and not any(path.exists() for path in demo_files)
    assert await ticket_audit_count(env, ids) == audit_before

    tagged_emails = {email for _, email, _, _ in env.config.tagged}
    deactivated, deleted = set(result.users_deactivated), set(result.users_deleted)
    assert deactivated | deleted == tagged_emails and not deactivated & deleted
    assert deactivated and len(result.memberships_removed) == 1
    tagged = (await env.db.scalars(select(User).where(User.email.in_(tagged_emails)))).all()
    assert {u.email for u in tagged} == tagged_emails - deleted and not any(user.is_active for user in tagged)
    members = {m.user_id for m in (await env.db.scalars(select(GroupMember).where(GroupMember.group_id == env.team.id))).all()}
    assert members == {env.seeded["technician"].id}
    for user in env.seeded.values():
        await env.db.refresh(user)
        assert user.is_active

    again = await demo.remove_demo(env.db, env.config)
    assert again.tickets_removed == 0 and again.files_removed == 0

    reseeded = await demo.seed_demo(env.db, env.config)
    assert reseeded.tickets_created == 15
    assert set(reseeded.users_reactivated) == deactivated and set(reseeded.users_created) == deleted


@pytest.mark.asyncio
async def test_demo_remove_deletes_unreferenced_tagged_users_and_never_touches_seeded_users(demo_env) -> None:
    env = demo_env
    suffix = uuid4().hex[:8]
    extra_email = f"demo.extra.{suffix}@example.test"
    config = demo.DemoConfig(**{**env.config.__dict__, "tagged": (*env.config.tagged, ("extra", extra_email, "Demo Extra", "requester"))})
    extra = User(email=extra_email, password_hash="x", name="Demo Extra", profile_id=env.seeded["requester"].profile_id, entity_id=env.entity.id)
    env.db.add(extra)
    await env.db.commit()
    result = await demo.remove_demo(env.db, config)
    assert result.users_deleted == [extra_email]
    assert await env.db.scalar(select(User).where(User.email == extra_email)) is None
    for user in env.seeded.values():
        assert await env.db.get(User, user.id) is not None


@pytest.mark.asyncio
async def test_demo_seed_reports_a_workflow_block_instead_of_bypassing_it(demo_env) -> None:
    env = demo_env
    env.db.add(
        TicketWorkflow(
            entity_id=env.entity.id,
            record_type="ticket",
            states=["NEW", "ASSIGNED"],
            transitions={"NEW": ["ASSIGNED"], "ASSIGNED": []},
            approval_requirements={},
            is_active=True,
        )
    )
    await env.db.commit()
    report = await demo.seed_demo(env.db, env.config)
    blocked_targets = {target for _, target, _ in report.blocked}
    assert blocked_targets <= {"PLANNED", "PENDING", "SOLVED"} and blocked_targets
    tickets = await demo_tickets(env)
    assert {t.status for t in tickets} <= {"NEW", "ASSIGNED"}
    assert all(reason for _, _, reason in report.blocked)


@pytest.mark.asyncio
async def test_every_enqueue_path_is_suppressed_and_counted() -> None:
    with demo.suppress_enqueues() as blocked:
        worker_tasks.send_ticket_notification.delay(1, "created")
        worker_tasks.send_ticket_notification.apply_async(args=(1, "assigned"))
        worker_tasks.celery_app.send_task("app.workers.tasks.send_ticket_notification", args=(1, "x"))
    assert len(blocked) == 3


def test_generated_demo_files_pass_the_real_attachment_validators() -> None:
    for kind, (filename, builder) in demo.FILE_BUILDERS.items():
        validated = validate_attachment(builder(), filename)
        assert validated.kind.key == kind


class _SessionOf:
    def __init__(self, db) -> None:
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *exc) -> bool:
        return False


async def run_breach_job(monkeypatch, env, *, debug: bool) -> list[int]:
    sent: list[int] = []
    monkeypatch.setattr(worker_tasks, "SessionLocal", lambda: _SessionOf(env.db))
    monkeypatch.setattr(worker_tasks, "get_settings", lambda: SimpleNamespace(debug=debug))
    monkeypatch.setattr(worker_tasks.send_ticket_notification, "delay", lambda ticket_id, event: sent.append(ticket_id))
    await worker_tasks._check_sla_breaches()
    return sent


async def breached_pair(env) -> tuple[Ticket, Ticket]:
    past = datetime.now(UTC) - timedelta(hours=3)
    tickets = []
    for title in (DEMO_TICKETS[0].title, f"Ordinary breached ticket {uuid4().hex[:6]}"):
        ticket = Ticket(
            title=title, description="<p>Overdue ticket for the breach job.</p>", type="INCIDENT", status="ASSIGNED",
            priority=3, urgency=3, impact=3, entity_id=env.entity.id, requester_id=env.seeded["requester"].id,
            assignee_id=env.seeded["technician"].id, tto_due_at=past, ttr_due_at=past,
        )
        env.db.add(ticket)
        tickets.append(ticket)
    await env.db.commit()
    return tickets[0], tickets[1]


@pytest.mark.asyncio
async def test_breach_job_skips_demo_titles_only_when_debug_is_true(demo_env, monkeypatch) -> None:
    env = demo_env
    demo_ticket, plain_ticket = await breached_pair(env)
    sent = await run_breach_job(monkeypatch, env, debug=True)
    await env.db.refresh(demo_ticket)
    await env.db.refresh(plain_ticket)
    assert demo_ticket.priority == 3 and demo_ticket.id not in sent
    assert await env.db.scalar(select(AuditLog.id).where(AuditLog.entity == "sla_notification", AuditLog.entity_id == demo_ticket.id)) is None
    assert plain_ticket.priority == 4 and plain_ticket.id in sent


@pytest.mark.asyncio
async def test_breach_job_processes_demo_titled_tickets_normally_when_debug_is_false(demo_env, monkeypatch) -> None:
    env = demo_env
    demo_ticket, plain_ticket = await breached_pair(env)
    sent = await run_breach_job(monkeypatch, env, debug=False)
    await env.db.refresh(demo_ticket)
    await env.db.refresh(plain_ticket)
    assert demo_ticket.priority == 4 and demo_ticket.id in sent
    assert plain_ticket.priority == 4 and plain_ticket.id in sent
