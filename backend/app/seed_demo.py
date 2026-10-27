"""Development-only demo data.

    docker compose exec -T -e DEBUG=true -e ALLOW_DEMO_SEED=1 api python -m app.seed_demo
    docker compose exec -T -e DEBUG=true -e ALLOW_DEMO_SEED=1 api python -m app.seed_demo --remove

Everything goes through the normal handlers and services; only enqueueing is suppressed.
Demo tickets are recognised by their exact fixed titles (app.demo_data), never by a prefix.
"""

import argparse
import asyncio
import io
import os
import secrets
import struct
import sys
import zipfile
import zlib
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Iterator
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.security import hash_password
from app.demo_data import DEMO_TICKETS, DEMO_TITLES, DemoTicket
from app.models import (
    SLA,
    Entity,
    Group,
    GroupMember,
    Profile,
    Ticket,
    TicketDocument,
    TicketFollowup,
    TicketTypeDefinition,
    User,
)
from app.schemas_tickets import FollowupCreate, TicketCreate
from app.services.automation import calculate_sla_deadlines
from app.services.bulk_tickets import apply_ticket_update

REQUESTER_LABELS = {1: "critical", 2: "high", 3: "medium", 5: "low"}
STATUS_PATHS = {
    "NEW": [],
    "ASSIGNED": [],
    "PLANNED": ["PLANNED"],
    "PENDING": ["PENDING"],
    "SOLVED": ["SOLVED"],
    "CLOSED": ["SOLVED", "CLOSED"],
}


class DemoSeedRefused(Exception):
    pass


class DemoSeedError(Exception):
    pass


@dataclass(frozen=True)
class DemoConfig:
    entity_name: str = "SVC Infosystems"
    admin_email: str = "admin@svcinfosys.in"
    requester_email: str = "priya@svcinfosys.in"
    technician_email: str = "aarav@svcinfosys.in"
    requester_profile: str = "Requester"
    technician_profile: str = "Technician"
    # key -> (email, display name, profile kind)
    tagged: tuple[tuple[str, str, str, str], ...] = (
        ("req1", "demo.requester1@example.test", "Demo Requester One", "requester"),
        ("req2", "demo.requester2@example.test", "Demo Requester Two", "requester"),
        ("tech1", "demo.technician1@example.test", "Demo Technician One", "technician"),
    )


@dataclass
class SeedReport:
    users_created: list[str] = field(default_factory=list)
    users_reactivated: list[str] = field(default_factory=list)
    memberships_added: list[str] = field(default_factory=list)
    tickets_created: int = 0
    tickets_existing: int = 0
    blocked: list[tuple[str, str, str]] = field(default_factory=list)  # (title, target status, reason)
    public_replies: int = 0
    documents: int = 0
    files: list[str] = field(default_factory=list)
    enqueues_suppressed: int = 0
    summary: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass
class RemoveReport:
    tickets_removed: int = 0
    followups_removed: int = 0
    documents_removed: int = 0
    files_removed: int = 0
    users_deleted: list[str] = field(default_factory=list)
    users_deactivated: list[str] = field(default_factory=list)
    memberships_removed: list[str] = field(default_factory=list)
    audit_rows_kept: int = 0


def ensure_dev_allowed() -> None:
    if not get_settings().debug:
        raise DemoSeedRefused("Refusing to run: DEBUG is not true.")
    if os.environ.get("ALLOW_DEMO_SEED") != "1":
        raise DemoSeedRefused("Refusing to run: ALLOW_DEMO_SEED=1 is not set.")


@contextmanager
def suppress_enqueues() -> Iterator[list[str]]:
    """Block every Celery enqueue path (.delay, apply_async, send_task) and record what was blocked."""
    blocked: list[str] = []

    def fake_apply_async(self, args=None, kwargs=None, **options):
        blocked.append(self.name)

    def fake_send_task(self, name, args=None, kwargs=None, **options):
        blocked.append(name)

    with patch("celery.app.task.Task.apply_async", fake_apply_async), patch("celery.Celery.send_task", fake_send_task):
        yield blocked


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def make_png() -> bytes:
    width = height = 16
    row = b"\x00" + bytes((37, 99, 235)) * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(row * height))
        + _chunk(b"IEND", b"")
    )


def make_pdf() -> bytes:
    return (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
        b"trailer<</Root 1 0 R/Size 4>>\n%%EOF\n"
    )


def make_txt() -> bytes:
    return b"Speed test results\nDownload: 4.1 Mbps\nUpload: 1.2 Mbps\nLatency: 180 ms\n"


def _ooxml(parts: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in parts.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def make_docx() -> bytes:
    return _ooxml({
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>"
        ),
        "word/document.xml": (
            '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>Mailbox quota summary: limit raised and old items archived.</w:t></w:r></w:p></w:body></w:document>"
        ),
    })


def make_xlsx() -> bytes:
    return _ooxml({
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Checklist" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>"
        ),
        "xl/worksheets/sheet1.xml": (
            '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Step</t></is></c><c r="B1" t="inlineStr"><is><t>Owner</t></is></c></row></sheetData></worksheet>'
        ),
    })


FILE_BUILDERS = {
    "png": ("screenshot.png", make_png),
    "pdf": ("error-report.pdf", make_pdf),
    "txt": ("speedtest.txt", make_txt),
    "docx": ("mailbox-summary.docx", make_docx),
    "xlsx": ("rollout-checklist.xlsx", make_xlsx),
}


def _upload_file(kind: str) -> UploadFile:
    filename, builder = FILE_BUILDERS[kind]
    return UploadFile(file=io.BytesIO(builder()), filename=filename)


async def _load_user(db: AsyncSession, email: str) -> User | None:
    return await db.scalar(select(User).options(selectinload(User.profile)).where(User.email == email))


async def _require_user(db: AsyncSession, entity: Entity, email: str, label: str) -> User:
    user = await _load_user(db, email)
    if user is None or user.entity_id != entity.id:
        raise DemoSeedError(f"The {label} ({email}) does not exist in entity {entity.name!r}; run the normal seed first.")
    return user


async def _entity(db: AsyncSession, config: DemoConfig) -> Entity:
    entity = await db.scalar(select(Entity).where(Entity.name == config.entity_name))
    if entity is None:
        raise DemoSeedError(f"Entity {config.entity_name!r} does not exist; run the normal seed first.")
    return entity


async def _service_desk(db: AsyncSession, entity: Entity) -> Group | None:
    return await db.scalar(select(Group).where(Group.entity_id == entity.id, Group.name == f"{entity.name} Service Desk"))


async def _ensure_tagged_users(db: AsyncSession, config: DemoConfig, entity: Entity, report: SeedReport) -> dict[str, User]:
    users: dict[str, User] = {}
    team = await _service_desk(db, entity)
    for key, email, name, kind in config.tagged:
        user = await _load_user(db, email)
        if user is None:
            profile_name = config.requester_profile if kind == "requester" else config.technician_profile
            profile = await db.scalar(select(Profile).where(Profile.name == profile_name))
            if profile is None:
                raise DemoSeedError(f"Profile {profile_name!r} does not exist.")
            user = User(
                email=email,
                name=name,
                profile_id=profile.id,
                entity_id=entity.id,
                password_hash=hash_password(secrets.token_urlsafe(24)),
            )
            db.add(user)
            await db.flush()
            await db.refresh(user, attribute_names=["profile"])
            report.users_created.append(email)
        else:
            if user.entity_id != entity.id:
                raise DemoSeedError(f"{email} exists in another entity.")
            if not user.is_active:
                user.is_active = True
                report.users_reactivated.append(email)
        users[key] = user
        if kind == "technician" and team is not None and await db.get(GroupMember, (team.id, user.id)) is None:
            db.add(GroupMember(group_id=team.id, user_id=user.id))
            report.memberships_added.append(f"{email} -> {team.name}")
    await db.commit()
    return users


async def _type_id(db: AsyncSession, itil_class: str, entity_id: int) -> int:
    type_id = await db.scalar(
        select(TicketTypeDefinition.id)
        .where(
            TicketTypeDefinition.itil_class == itil_class,
            (TicketTypeDefinition.entity_id == entity_id) | (TicketTypeDefinition.entity_id.is_(None)),
            TicketTypeDefinition.is_active.is_(True),
        )
        .order_by(TicketTypeDefinition.id)
        .limit(1)
    )
    if type_id is None:
        raise DemoSeedError(f"No active ticket type of class {itil_class}.")
    return type_id


async def _create(db: AsyncSession, spec: DemoTicket, owner: User, admin: User, entity: Entity) -> int:
    from app.api.tickets import create_ticket

    type_id = await _type_id(db, spec.type, entity.id)
    if spec.type in {"INCIDENT", "REQUEST"}:
        payload = TicketCreate(
            title=spec.title,
            description=spec.description,
            type=spec.type,
            priority=REQUESTER_LABELS[spec.priority],
            ticket_type_id=type_id,
        )
        created = await create_ticket(payload, owner, db)
    else:
        payload = TicketCreate(
            title=spec.title,
            description=spec.description,
            type="PROBLEM" if spec.type == "PROBLEM" else "INCIDENT",
            priority=spec.priority,
            ticket_type_id=type_id,
            requester_id=owner.id,
        )
        created = await create_ticket(payload, admin, db)
    return int(created["id"])


async def _update(db: AsyncSession, ticket_id: int, admin: User, values: dict, action: str) -> None:
    ticket = await db.get(Ticket, ticket_id)
    await apply_ticket_update(db, ticket, admin, values, audit_action=action)
    await db.commit()


async def _reply(db: AsyncSession, ticket_id: int, author: User, text: str) -> int:
    from app.api.tickets import add_followup

    followup = await add_followup(ticket_id, FollowupCreate(body=f"<p>{text}</p>"), author, db)
    return followup.id


async def _upload(db: AsyncSession, ticket_id: int, uploader: User, kind: str, followup_id: int | None, report: SeedReport) -> None:
    from app.api.attachments import upload_document

    await upload_document(ticket_id, uploader, db, _upload_file(kind), followup_id, None)
    report.documents += 1


async def _backdate(db: AsyncSession, ticket_id: int, spec: DemoTicket) -> None:
    now = datetime.now(UTC)
    ticket = await db.get(Ticket, ticket_id)
    ticket.opened_at = now - timedelta(hours=spec.age_hours)
    sla = await db.get(SLA, ticket.sla_id) if ticket.sla_id else None
    if sla is not None:
        # Deadlines are recomputed from the back-dated opening time, so breach state is realistic.
        calculate_sla_deadlines(ticket, sla.tto_minutes, sla.ttr_minutes, sla.calendar)
    ticket.solved_at = None
    ticket.closed_at = None
    end = now - timedelta(minutes=5)
    if spec.status in {"SOLVED", "CLOSED"}:
        due = ticket.ttr_due_at or ticket.opened_at + timedelta(hours=8)
        solved = due + timedelta(hours=3) if spec.breached else ticket.opened_at + (due - ticket.opened_at) / 2
        solved = max(min(solved, now - timedelta(hours=2)), ticket.opened_at + timedelta(minutes=30))
        ticket.solved_at = solved
        end = solved
        if spec.status == "CLOSED":
            ticket.closed_at = min(solved + timedelta(days=1), now - timedelta(minutes=30))
            end = ticket.closed_at
    followups = list((await db.scalars(select(TicketFollowup).where(TicketFollowup.ticket_id == ticket_id).order_by(TicketFollowup.id))).all())
    stamps: dict[int, datetime] = {}
    for index, followup in enumerate(followups):
        followup.created_at = ticket.opened_at + (end - ticket.opened_at) * (index + 1) / (len(followups) + 1)
        stamps[followup.id] = followup.created_at
    for document in (await db.scalars(select(TicketDocument).where(TicketDocument.ticket_id == ticket_id))).all():
        document.created_at = stamps[document.followup_id] + timedelta(minutes=1) if document.followup_id in stamps else ticket.opened_at + timedelta(minutes=5)
    await db.commit()


async def _build_ticket(db, spec: DemoTicket, users: dict[str, User], admin: User, entity: Entity, report: SeedReport) -> None:
    owner = users[spec.owner]
    tech = users.get(spec.assignee) if spec.assignee else None
    ticket_id = await _create(db, spec, owner, admin, entity)
    await db.commit()
    if spec.type == "CHANGE":
        await _update(db, ticket_id, admin, {"ticket_type_id": await _type_id(db, "CHANGE", entity.id)}, "updated")
    if tech is not None:
        await _update(db, ticket_id, admin, {"assignee_id": tech.id}, "assigned")
    for kind, uploader in spec.attachments:
        await _upload(db, ticket_id, owner if uploader == "owner" else admin, kind, None, report)
    for reply in spec.replies:
        author = owner if reply.who == "owner" else tech
        followup_id = await _reply(db, ticket_id, author, reply.text)
        report.public_replies += 1
        if reply.attach:
            await _upload(db, ticket_id, author, reply.attach, followup_id, report)
    for target in STATUS_PATHS[spec.status]:
        try:
            await _update(db, ticket_id, admin, {"status": target}, "updated")
        except HTTPException as error:
            await db.rollback()
            for obj in {id(o): o for o in (*users.values(), admin, entity)}.values():
                await db.refresh(obj)
                if isinstance(obj, User):
                    await db.refresh(obj, attribute_names=["profile"])
            report.blocked.append((spec.title, target, str(error.detail)))
            break
    await _backdate(db, ticket_id, spec)


async def summarise(db: AsyncSession, config: DemoConfig) -> dict[str, dict[str, int]]:
    entity = await _entity(db, config)
    tickets = (
        await db.scalars(select(Ticket).where(Ticket.entity_id == entity.id, Ticket.title.in_(DEMO_TITLES)))
    ).all()
    names = {user.id: user.email for user in (await db.scalars(select(User).where(User.entity_id == entity.id))).all()}
    return {
        "requester": dict(Counter(names[t.requester_id] for t in tickets)),
        "assignee": dict(Counter(names[t.assignee_id] if t.assignee_id else "(unassigned)" for t in tickets)),
        "status": dict(Counter(t.status for t in tickets)),
        "priority": dict(Counter(str(t.priority) for t in tickets)),
        "type": dict(Counter(t.type for t in tickets)),
    }


async def seed_demo(db: AsyncSession, config: DemoConfig | None = None) -> SeedReport:
    config = config or DemoConfig()
    report = SeedReport()
    entity = await _entity(db, config)
    admin = await _require_user(db, entity, config.admin_email, "administrator")
    seeded_requester = await _require_user(db, entity, config.requester_email, "seeded requester")
    seeded_technician = await _require_user(db, entity, config.technician_email, "seeded technician")
    with suppress_enqueues() as blocked:
        users = await _ensure_tagged_users(db, config, entity, report)
        users["priya"] = seeded_requester
        users["aarav"] = seeded_technician
        existing = set(
            (await db.scalars(select(Ticket.title).where(Ticket.entity_id == entity.id, Ticket.title.in_(DEMO_TITLES)))).all()
        )
        for spec in DEMO_TICKETS:
            if spec.title in existing:
                report.tickets_existing += 1
                continue
            await _build_ticket(db, spec, users, admin, entity, report)
            report.tickets_created += 1
    report.enqueues_suppressed = len(blocked)
    from app.api.attachments import locate_stored_file

    for document in (
        await db.scalars(
            select(TicketDocument)
            .join(Ticket, Ticket.id == TicketDocument.ticket_id)
            .where(Ticket.entity_id == entity.id, Ticket.title.in_(DEMO_TITLES))
        )
    ).all():
        path, state = locate_stored_file(document)
        if path is not None:
            report.files.append(str(path))
    report.summary = await summarise(db, config)
    return report


async def remove_demo(db: AsyncSession, config: DemoConfig | None = None) -> RemoveReport:
    from app.api.attachments import locate_stored_file
    from app.models import AuditLog

    config = config or DemoConfig()
    report = RemoveReport()
    entity = await _entity(db, config)
    tickets = (await db.scalars(select(Ticket).where(Ticket.entity_id == entity.id, Ticket.title.in_(DEMO_TITLES)))).all()
    ids = [ticket.id for ticket in tickets]
    documents = (await db.scalars(select(TicketDocument).where(TicketDocument.ticket_id.in_(ids)))).all() if ids else []
    stored = [path for path, _ in (locate_stored_file(document) for document in documents) if path is not None]
    report.documents_removed = len(documents)
    if ids:
        report.followups_removed = await db.scalar(select(func.count()).select_from(TicketFollowup).where(TicketFollowup.ticket_id.in_(ids))) or 0
        report.audit_rows_kept = await db.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.entity == "ticket", AuditLog.entity_id.in_(ids))
        ) or 0
        # The ticket's followups, documents, actors, links and field values are removed by their ON DELETE CASCADE foreign keys.
        await db.execute(delete(Ticket).where(Ticket.id.in_(ids), Ticket.entity_id == entity.id))
        report.tickets_removed = len(ids)
    await db.commit()
    for path in stored:
        path.unlink(missing_ok=True)
        report.files_removed += 1

    team = await _service_desk(db, entity)
    for _, email, _, _ in config.tagged:
        user = await _load_user(db, email)
        if user is None or user.entity_id != entity.id:
            continue
        if team is not None:
            removed = await db.execute(delete(GroupMember).where(GroupMember.group_id == team.id, GroupMember.user_id == user.id))
            if removed.rowcount:
                report.memberships_removed.append(f"{email} -> {team.name}")
        try:
            async with db.begin_nested():
                await db.execute(delete(User).where(User.id == user.id))
            report.users_deleted.append(email)
        except IntegrityError:
            # Still referenced (for example by append-only audit rows): keep the row, deactivate it.
            user = await _load_user(db, email)
            user.is_active = False
            report.users_deactivated.append(email)
    await db.commit()
    return report


def _print_summary(summary: dict[str, dict[str, int]]) -> None:
    for name, counts in summary.items():
        print(f"  by {name}: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))


async def _run(remove: bool) -> int:
    from app.core.db import SessionLocal

    async with SessionLocal() as db:
        if remove:
            removed = await remove_demo(db)
            print(f"Removed: tickets={removed.tickets_removed} followups={removed.followups_removed} documents={removed.documents_removed} files={removed.files_removed}")
            print(f"Tagged users deleted: {removed.users_deleted or 'none'}")
            print(f"Tagged users deactivated (still referenced, e.g. by audit rows): {removed.users_deactivated or 'none'}")
            print(f"Group memberships removed: {removed.memberships_removed or 'none'}")
            print(f"Audit rows kept (append-only): {removed.audit_rows_kept}")
            return 0
        report = await seed_demo(db)
    print(f"Tickets created={report.tickets_created} already present={report.tickets_existing}")
    print(f"Users created={report.users_created or 'none'} reactivated={report.users_reactivated or 'none'}")
    print(f"Group memberships added: {report.memberships_added or 'none'}")
    print(f"Public replies={report.public_replies} documents={report.documents} files written={len(report.files)}")
    print(f"Enqueues suppressed (no emails sent): {report.enqueues_suppressed}")
    _print_summary(report.summary)
    for title, target, reason in report.blocked:
        print(f"BLOCKED {title!r} at {target}: {reason}", file=sys.stderr)
    return 2 if report.blocked else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create or remove development demo data.")
    parser.add_argument("--remove", action="store_true", help="delete exactly the fixed demo tickets and their files")
    args = parser.parse_args(argv)
    try:
        ensure_dev_allowed()
    except DemoSeedRefused as error:
        print(str(error), file=sys.stderr)
        return 1
    try:
        return asyncio.run(_run(args.remove))
    except DemoSeedError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
