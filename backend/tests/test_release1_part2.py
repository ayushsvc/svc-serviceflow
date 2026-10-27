import asyncio
import io
import zipfile
from uuid import uuid4

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.deps import current_user, get_db
from app.main import app
from app.models import AuditLog, Entity, Profile, Ticket, TicketDocument, User
from app.services.attachments import MAX_FILE_BYTES, MAX_FILES_PER_TICKET

WORD_MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
EXCEL_MAIN = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
WORD_MACRO_MAIN = "application/vnd.ms-word.document.macroEnabled.main+xml"
EXCEL_MACRO_MAIN = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 13 + b"\x00" * 4 + b"\x00\x00\x00\x00IEND\xaeB`\x82"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"


def mark_encrypted(archive_bytes: bytes) -> bytes:
    data = bytearray(archive_bytes)
    index = data.find(b"PK\x01\x02")
    while index != -1:
        data[index + 8] |= 0x01
        index = data.find(b"PK\x01\x02", index + 4)
    return bytes(data)


def ooxml(part: str, main_type: str, *, extra: dict[str, bytes] | None = None) -> bytes:
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/{part}" ContentType="{main_type}"/></Types>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", "<Relationships/>")
        archive.writestr(part, "<document>Hello</document>")
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


def docx(**kwargs) -> bytes:
    return ooxml("word/document.xml", WORD_MAIN, **kwargs)


def xlsx(**kwargs) -> bytes:
    return ooxml("xl/workbook.xml", EXCEL_MAIN, **kwargs)


def plain_zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


@pytest.fixture
async def attachments_http(monkeypatch, tmp_path):
    monkeypatch.setattr("app.api.attachments.UPLOAD_ROOT", tmp_path)
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        db = AsyncSession(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        suffix = uuid4().hex
        requester_profile = Profile(
            name=f"Release 1 part 2 requester {suffix}",
            interface="self_service",
            rights={"ticket:own_only": True},
        )
        staff_profile = Profile(
            name=f"Release 1 part 2 staff {suffix}",
            interface="helpdesk",
            rights={"ticket:view_all": True, "tickets:update": True},
        )
        entities = {key: Entity(name=f"Release 1 part 2 {key} {suffix}") for key in ("A", "B")}
        db.add_all([requester_profile, staff_profile, *entities.values()])
        await db.flush()
        users = {}
        for key, entity in entities.items():
            for role, profile in (("requester", requester_profile), ("staff", staff_profile)):
                account = User(
                    email=f"part2-{key}-{role}-{suffix}@example.test",
                    password_hash="test-only",
                    name=f"{key} {role}",
                    profile=profile,
                    entity=entity,
                )
                users[f"{key}_{role}"] = account
                db.add(account)
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
                yield {"client": client, "db": db, "entities": entities, "users": users, "state": state, "root": tmp_path}
        finally:
            app.dependency_overrides.pop(current_user, None)
            app.dependency_overrides.pop(get_db, None)
            await db.close()
            if transaction.is_active:
                await transaction.rollback()
            await engine.dispose()


async def new_ticket(context, *, entity: str = "A", status: str = "NEW") -> int:
    ticket = Ticket(
        title=f"Attachment ticket {uuid4().hex}",
        description="A ticket used to test attachments.",
        type="INCIDENT",
        status=status,
        urgency=3,
        impact=3,
        priority=3,
        entity_id=context["entities"][entity].id,
        requester_id=context["users"][f"{entity}_requester"].id,
    )
    context["db"].add(ticket)
    await context["db"].flush()
    return ticket.id


async def upload(context, ticket_id: int, name: str, content: bytes, *, user: str = "A_requester", mime: str = "application/octet-stream"):
    context["state"]["user"] = context["users"][user]
    return await context["client"].post(
        f"/api/v1/tickets/{ticket_id}/documents",
        files={"document": (name, content, mime)},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "content", "content_type"),
    [
        ("doc.pdf", PDF, "application/pdf"),
        ("shot.png", PNG, "image/png"),
        ("photo.jpg", JPEG, "image/jpeg"),
        ("photo.JPEG", JPEG, "image/jpeg"),
        ("notes.txt", b"plain notes\n", "text/plain"),
        ("data.csv", b"id,name\n1,Ada\n", "text/csv"),
        ("server.log", b"2026-01-01 ERROR something <html> broke\n", "text/plain"),
        ("letter.docx", docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("sheet.xlsx", xlsx(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ],
)
async def test_each_allowed_type_is_accepted_and_stored_under_a_generated_name(attachments_http, name, content, content_type) -> None:
    ticket_id = await new_ticket(attachments_http)
    response = await upload(attachments_http, ticket_id, name, content, mime="application/x-msdownload")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["filename"] == name
    assert body["content_type"] == content_type
    assert body["size_bytes"] == len(content)
    stored = await attachments_http["db"].get(TicketDocument, body["id"])
    assert stored is not None and name not in stored.path
    assert (attachments_http["root"] / stored.path.rsplit("/", 1)[-1]).read_bytes() == content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "content", "status_code"),
    [
        ("setup.exe", b"MZ\x90\x00\x03\x00\x00\x00\x04\x00", 415),
        ("report.pdf", b"MZ\x90\x00\x03\x00\x00\x00\x04\x00", 415),
        ("report.txt", b"MZ\x90\x00\x03\x00\x00\x00\x04\x00", 415),
        ("run.sh", b"#!/bin/sh\nrm -rf /\n", 415),
        ("notes.txt", b"#!/bin/sh\nrm -rf /\n", 415),
        ("logo.svg", b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>', 415),
        ("logo.png", b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>', 415),
        ("logo.txt", b'<?xml version="1.0"?><svg onload="alert(1)"/>', 415),
        ("page.html", b"<!DOCTYPE html><html><script>alert(1)</script></html>", 415),
        ("page.txt", b"  <html><body>hi</body></html>", 415),
        ("archive.zip", plain_zip({"a.txt": b"hello"}), 415),
        ("archive.docx", plain_zip({"a.txt": b"hello"}), 415),
        ("picture.png", PDF, 415),
        ("legacy.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32, 415),
        ("notes.txt", b"\x00\x01\x02 binary \xff", 422),
        ("data.csv", b"id,name\n\x00\x00\n", 422),
        ("empty.txt", b"", 422),
        ("broken.png", b"\x89PNG\r\n\x1a\ngarbage", 422),
        ("broken.docx", b"PK\x03\x04 not really a zip", 422),
    ],
)
async def test_executables_scripts_markup_archives_and_mismatches_are_rejected_by_content(
    attachments_http, name, content, status_code
) -> None:
    ticket_id = await new_ticket(attachments_http)
    response = await upload(attachments_http, ticket_id, name, content, mime="image/png")
    assert response.status_code == status_code, response.text
    assert response.json()["detail"]
    assert await attachments_http["db"].scalar(select(func.count(TicketDocument.id)).where(TicketDocument.ticket_id == ticket_id)) == 0
    assert list(attachments_http["root"].iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "content", "status_code"),
    [
        ("macro.docm", docx(extra={"word/vbaProject.bin": b"\x00vba"}), 415),
        ("macro.docx", docx(extra={"word/vbaProject.bin": b"\x00vba"}), 415),
        ("macro.docm", ooxml("word/document.xml", WORD_MACRO_MAIN), 415),
        ("macro.docx", ooxml("word/document.xml", WORD_MACRO_MAIN), 415),
        ("macro.xlsm", ooxml("xl/workbook.xml", EXCEL_MACRO_MAIN), 415),
        ("macro.xlsx", ooxml("xl/workbook.xml", EXCEL_MACRO_MAIN, extra={"xl/vbaProject.bin": b"\x00vba"}), 415),
        ("bomb.docx", docx(extra={"word/bomb.bin": b"\x00" * (60 * 1024 * 1024)}), 422),
        ("entries.xlsx", xlsx(extra={f"xl/part{index}.xml": b"<a/>" for index in range(1001)}), 422),
        ("traversal.docx", docx(extra={"../evil.xml": b"<a/>"}), 422),
        ("traversal.xlsx", xlsx(extra={"xl/../../evil.xml": b"<a/>"}), 422),
        ("nested.docx", docx(extra={"word/embeddings/inner.bin": plain_zip({"a.txt": b"x"})}), 422),
        ("nested2.xlsx", xlsx(extra={"xl/embeddings/inner.zip": b"anything"}), 422),
        ("encrypted.docx", mark_encrypted(docx()), 422),
    ],
)
async def test_macro_enabled_corrupt_encrypted_nested_and_zip_bomb_office_files_are_rejected(
    attachments_http, name, content, status_code
) -> None:
    ticket_id = await new_ticket(attachments_http)
    response = await upload(attachments_http, ticket_id, name, content)
    assert response.status_code == status_code, response.text
    assert list(attachments_http["root"].iterdir()) == []


@pytest.mark.asyncio
async def test_size_limit_is_exactly_ten_megabytes_and_returns_413_above(attachments_http) -> None:
    ticket_id = await new_ticket(attachments_http)
    exact = await upload(attachments_http, ticket_id, "exact.txt", b"a" * MAX_FILE_BYTES)
    assert exact.status_code == 201, exact.text
    assert exact.json()["size_bytes"] == MAX_FILE_BYTES
    over = await upload(attachments_http, ticket_id, "over.txt", b"a" * (MAX_FILE_BYTES + 1))
    assert over.status_code == 413
    assert over.json()["detail"] == "File exceeds the 10 MB limit."


@pytest.mark.asyncio
async def test_sixth_file_is_rejected_with_a_clear_message_and_removal_frees_a_slot(attachments_http) -> None:
    ticket_id = await new_ticket(attachments_http)
    first_id = None
    for index in range(MAX_FILES_PER_TICKET):
        response = await upload(attachments_http, ticket_id, f"file{index}.txt", f"file {index}".encode())
        assert response.status_code == 201, response.text
        first_id = first_id or response.json()["id"]
    sixth = await upload(attachments_http, ticket_id, "sixth.txt", b"sixth", user="A_staff")
    assert sixth.status_code == 422
    assert "at most 5 attachments" in sixth.json()["detail"]
    requester_sixth = await upload(attachments_http, ticket_id, "sixth.txt", b"sixth")
    assert requester_sixth.status_code == 422
    assert requester_sixth.json()["detail"] == "Attachment limit reached"

    attachments_http["state"]["user"] = attachments_http["users"]["A_staff"]
    removed = await attachments_http["client"].delete(f"/api/v1/tickets/{ticket_id}/documents/{first_id}")
    assert removed.status_code == 204
    again = await upload(attachments_http, ticket_id, "sixth.txt", b"sixth")
    assert again.status_code == 201, again.text


@pytest.mark.asyncio
async def test_download_and_preview_headers_are_safe_and_downloads_are_attachments(attachments_http) -> None:
    ticket_id = await new_ticket(attachments_http)
    png = (await upload(attachments_http, ticket_id, 'shot "one".png', PNG)).json()
    text = (await upload(attachments_http, ticket_id, "notes.txt", b"hello")).json()
    client = attachments_http["client"]

    download = await client.get(f"/api/v1/tickets/{ticket_id}/documents/{png['id']}")
    assert download.status_code == 200
    assert download.content == PNG
    assert download.headers["content-disposition"].startswith("attachment;")
    assert download.headers["x-content-type-options"] == "nosniff"
    assert download.headers["content-type"] == "application/octet-stream"
    assert download.headers["content-security-policy"] == "default-src 'none'; sandbox"

    preview = await client.get(f"/api/v1/tickets/{ticket_id}/documents/{png['id']}/preview")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    assert preview.headers["content-disposition"].startswith("inline;")
    assert preview.headers["x-content-type-options"] == "nosniff"
    assert preview.headers["content-security-policy"] == "default-src 'none'; sandbox"

    not_image = await client.get(f"/api/v1/tickets/{ticket_id}/documents/{text['id']}/preview")
    assert not_image.status_code == 415

    other = await client.get(f"/api/v1/tickets/{ticket_id}/documents")
    assert other.headers["content-security-policy"] == "frame-ancestors 'none'"


@pytest.mark.asyncio
async def test_cross_entity_users_cannot_list_download_preview_or_remove(attachments_http) -> None:
    ticket_id = await new_ticket(attachments_http)
    uploaded = (await upload(attachments_http, ticket_id, "shot.png", PNG)).json()
    client = attachments_http["client"]
    for user in ("B_requester", "B_staff"):
        attachments_http["state"]["user"] = attachments_http["users"][user]
        base = f"/api/v1/tickets/{ticket_id}/documents"
        assert (await client.get(base)).status_code == 404
        assert (await client.get(f"{base}/{uploaded['id']}")).status_code == 404
        assert (await client.get(f"{base}/{uploaded['id']}/preview")).status_code == 404
        assert (await client.delete(f"{base}/{uploaded['id']}")).status_code == 404
        refused = await client.post(base, files={"document": ("x.txt", b"x", "text/plain")})
        assert refused.status_code == 404

    own_entity = await new_ticket(attachments_http, entity="B")
    attachments_http["state"]["user"] = attachments_http["users"]["B_requester"]
    mismatched = await client.get(f"/api/v1/tickets/{own_entity}/documents/{uploaded['id']}")
    assert mismatched.status_code == 404


@pytest.mark.asyncio
async def test_requester_can_remove_only_own_attachments_while_ticket_is_new(attachments_http) -> None:
    ticket_id = await new_ticket(attachments_http)
    staff_file = (await upload(attachments_http, ticket_id, "staff.txt", b"staff", user="A_staff")).json()
    own_file = (await upload(attachments_http, ticket_id, "own.txt", b"own")).json()
    later_file = (await upload(attachments_http, ticket_id, "later.txt", b"later")).json()
    client = attachments_http["client"]
    base = f"/api/v1/tickets/{ticket_id}/documents"

    listing = await client.get(base)
    flags = {item["filename"]: item["can_remove"] for item in listing.json()}
    assert flags == {"staff.txt": False, "own.txt": True, "later.txt": True}
    assert {item["uploaded_by_name"] for item in listing.json()} == {"A staff", "A requester"}

    attachments_http["state"]["user"] = attachments_http["users"]["A_requester"]
    assert (await client.delete(f"{base}/{staff_file['id']}")).status_code == 403
    assert (await client.delete(f"{base}/{own_file['id']}")).status_code == 204

    ticket = await attachments_http["db"].get(Ticket, ticket_id)
    ticket.status = "ASSIGNED"
    await attachments_http["db"].flush()
    assert (await client.delete(f"{base}/{later_file['id']}")).status_code == 403
    flags = {item["filename"]: item["can_remove"] for item in (await client.get(base)).json()}
    assert flags == {"staff.txt": False, "later.txt": False}


@pytest.mark.asyncio
async def test_staff_can_remove_any_attachment_by_right_and_removal_is_a_soft_delete(attachments_http) -> None:
    ticket_id = await new_ticket(attachments_http, status="ASSIGNED")
    uploaded = (await upload(attachments_http, ticket_id, "kept.txt", b"keep me")).json()
    client = attachments_http["client"]
    base = f"/api/v1/tickets/{ticket_id}/documents"
    stored = await attachments_http["db"].get(TicketDocument, uploaded["id"])
    stored_path = attachments_http["root"] / stored.path.rsplit("/", 1)[-1]

    attachments_http["state"]["user"] = attachments_http["users"]["A_staff"]
    assert (await client.delete(f"{base}/{uploaded['id']}")).status_code == 204
    assert (await client.delete(f"{base}/{uploaded['id']}")).status_code == 404
    assert (await client.get(f"{base}/{uploaded['id']}")).status_code == 404
    assert (await client.get(f"{base}/{uploaded['id']}/preview")).status_code == 404
    assert (await client.get(base)).json() == []

    await attachments_http["db"].refresh(stored)
    assert stored.removed_at is not None
    assert stored.removed_by == attachments_http["users"]["A_staff"].id
    assert stored_path.read_bytes() == b"keep me"


@pytest.mark.asyncio
async def test_upload_and_removal_write_audit_entries_with_name_size_and_actor(attachments_http) -> None:
    ticket_id = await new_ticket(attachments_http)
    uploaded = (await upload(attachments_http, ticket_id, "audited.txt", b"twelve bytes")).json()
    attachments_http["state"]["user"] = attachments_http["users"]["A_staff"]
    assert (await attachments_http["client"].delete(f"/api/v1/tickets/{ticket_id}/documents/{uploaded['id']}")).status_code == 204

    entries = list(
        (
            await attachments_http["db"].scalars(
                select(AuditLog).where(AuditLog.entity == "ticket_document", AuditLog.entity_id == uploaded["id"]).order_by(AuditLog.id)
            )
        ).all()
    )
    assert [entry.action for entry in entries] == ["uploaded", "removed"]
    requester = attachments_http["users"]["A_requester"]
    staff = attachments_http["users"]["A_staff"]
    assert entries[0].actor_id == requester.id
    assert entries[0].diff["filename"] == "audited.txt" and entries[0].diff["size_bytes"] == 12
    assert entries[0].diff["actor_id"] == requester.id and entries[0].diff["ticket_id"] == ticket_id
    assert entries[1].actor_id == staff.id
    assert entries[1].diff["filename"] == "audited.txt" and entries[1].diff["size_bytes"] == 12
    assert entries[1].diff["actor_id"] == staff.id


@pytest.fixture
async def committed_http(monkeypatch, tmp_path):
    monkeypatch.setattr("app.api.attachments.UPLOAD_ROOT", tmp_path)
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    suffix = uuid4().hex
    async with AsyncSession(engine, expire_on_commit=False) as db:
        profile = await db.scalar(select(Profile).where(Profile.name == "Administrator"))
        entity = Entity(name=f"Release 1 part 2 concurrent {suffix}")
        db.add(entity)
        await db.flush()
        user = User(
            email=f"part2-concurrent-{suffix}@example.test",
            password_hash="test-only",
            name="Concurrent uploader",
            profile=profile,
            entity=entity,
        )
        db.add(user)
        await db.flush()
        ticket = Ticket(
            title=f"Concurrent attachments {suffix}",
            description="A ticket used to test the attachment row lock.",
            type="INCIDENT",
            status="NEW",
            urgency=3,
            impact=3,
            priority=3,
            entity_id=entity.id,
            requester_id=user.id,
        )
        db.add(ticket)
        await db.flush()
        for index in range(MAX_FILES_PER_TICKET - 1):
            db.add(
                TicketDocument(
                    ticket_id=ticket.id,
                    filename=f"existing{index}.txt",
                    path=str(tmp_path / f"existing{index}.txt"),
                    uploaded_by=user.id,
                    content_type="text/plain",
                    size_bytes=1,
                )
            )
        await db.commit()
        ids = {"entity": entity.id, "user": user.id, "ticket": ticket.id}
    async with AsyncSession(engine, expire_on_commit=False) as db:
        loaded = await db.scalar(select(User).options(selectinload(User.profile)).where(User.id == ids["user"]))

    async def override_user(_request: Request) -> User:
        return loaded

    async def override_db():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[current_user] = override_user
    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            yield {"client": client, "engine": engine, "ids": ids}
    finally:
        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(get_db, None)
        async with AsyncSession(engine) as db:
            document_ids = list(
                (await db.scalars(select(TicketDocument.id).where(TicketDocument.ticket_id == ids["ticket"]))).all()
            )
            await db.execute(delete(AuditLog).where(AuditLog.entity == "ticket_document", AuditLog.entity_id.in_(document_ids)))
            await db.execute(delete(TicketDocument).where(TicketDocument.ticket_id == ids["ticket"]))
            await db.execute(delete(Ticket).where(Ticket.id == ids["ticket"]))
            await db.execute(delete(AuditLog).where(AuditLog.actor_id == ids["user"]))
            await db.execute(delete(User).where(User.id == ids["user"]))
            await db.execute(delete(Entity).where(Entity.id == ids["entity"]))
            await db.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_parallel_uploads_with_four_existing_files_let_exactly_one_succeed(committed_http) -> None:
    client = committed_http["client"]
    ticket_id = committed_http["ids"]["ticket"]

    def post(name: str):
        return client.post(
            f"/api/v1/tickets/{ticket_id}/documents",
            files={"document": (name, name.encode(), "text/plain")},
        )

    responses = await asyncio.wait_for(asyncio.gather(post("first.txt"), post("second.txt")), timeout=20)
    assert sorted(response.status_code for response in responses) == [201, 422]
    async with AsyncSession(committed_http["engine"]) as db:
        active = await db.scalar(
            select(func.count(TicketDocument.id)).where(
                TicketDocument.ticket_id == ticket_id, TicketDocument.removed_at.is_(None)
            )
        )
    assert active == MAX_FILES_PER_TICKET
