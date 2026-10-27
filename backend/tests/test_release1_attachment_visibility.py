import pytest
from sqlalchemy import select

from app.models import TicketDocument, TicketFollowup
from app.services.attachments import MAX_FILES_PER_TICKET
from test_release1_part2 import attachments_http, new_ticket  # noqa: F401

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 32
TEXT = b"hello attachment"


@pytest.fixture
def notifications(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr("app.api.tickets.send_ticket_notification.delay", lambda *args, **kwargs: calls.append(args))
    return calls


def as_user(context, key: str) -> None:
    context["state"]["user"] = context["users"][key]


async def upload_to(context, ticket_id: int, name: str, content: bytes, *, user: str, **fields):
    as_user(context, user)
    return await context["client"].post(
        f"/api/v1/tickets/{ticket_id}/documents",
        files={"document": (name, content, "application/octet-stream")},
        data={key: str(value) for key, value in fields.items()},
    )


async def post_followup(context, ticket_id: int, *, user: str, **payload):
    as_user(context, user)
    return await context["client"].post(f"/api/v1/tickets/{ticket_id}/followups", json=payload)


async def get(context, path: str, *, user: str):
    as_user(context, user)
    return await context["client"].get(f"/api/v1/tickets/{path}")


async def private_attachment(context, ticket_id: int) -> tuple[int, int]:
    followup = await post_followup(context, ticket_id, user="A_staff", body="internal only", is_private=True)
    assert followup.status_code == 201
    response = await upload_to(context, ticket_id, "secret.png", PNG, user="A_staff", followup_id=followup.json()["id"])
    assert response.status_code == 201
    return followup.json()["id"], response.json()["id"]


@pytest.mark.asyncio
async def test_requester_cannot_list_download_preview_or_count_private_followup_attachment(attachments_http) -> None:
    ticket_id = await new_ticket(attachments_http)
    _, document_id = await private_attachment(attachments_http, ticket_id)
    public = await upload_to(attachments_http, ticket_id, "public.txt", TEXT, user="A_requester")
    assert public.status_code == 201

    staff_list = await get(attachments_http, f"{ticket_id}/documents", user="A_staff")
    assert {item["filename"] for item in staff_list.json()} == {"secret.png", "public.txt"}

    requester_list = await get(attachments_http, f"{ticket_id}/documents", user="A_requester")
    assert [item["filename"] for item in requester_list.json()] == ["public.txt"]
    assert len(requester_list.json()) == 1
    assert (await get(attachments_http, f"{ticket_id}/documents/{document_id}", user="A_requester")).status_code == 404
    assert (await get(attachments_http, f"{ticket_id}/documents/{document_id}/preview", user="A_requester")).status_code == 404
    as_user(attachments_http, "A_requester")
    assert (await attachments_http["client"].delete(f"/api/v1/tickets/{ticket_id}/documents/{document_id}")).status_code == 404
    assert (await get(attachments_http, f"{ticket_id}/documents/{document_id}", user="A_staff")).status_code == 200
    assert (await get(attachments_http, f"{ticket_id}/documents/{document_id}/preview", user="A_staff")).status_code == 200


@pytest.mark.asyncio
async def test_public_reply_and_ticket_level_attachments_stay_visible_to_the_requester(attachments_http, notifications) -> None:
    ticket_id = await new_ticket(attachments_http)
    reply = await post_followup(attachments_http, ticket_id, user="A_staff", body="Please see the attached file.")
    assert reply.status_code == 201
    attached = await upload_to(attachments_http, ticket_id, "reply.txt", TEXT, user="A_staff", followup_id=reply.json()["id"])
    ticket_level = await upload_to(attachments_http, ticket_id, "ticket.txt", TEXT, user="A_staff")
    assert attached.status_code == 201 and ticket_level.status_code == 201

    names = {item["filename"] for item in (await get(attachments_http, f"{ticket_id}/documents", user="A_requester")).json()}
    assert names == {"reply.txt", "ticket.txt"}
    assert (await get(attachments_http, f"{ticket_id}/documents/{attached.json()['id']}", user="A_requester")).status_code == 200


@pytest.mark.asyncio
async def test_requester_cannot_attach_to_private_followups_or_tasks_and_mismatched_links_return_404(attachments_http) -> None:
    ticket_id = await new_ticket(attachments_http)
    other_ticket = await new_ticket(attachments_http)
    foreign_ticket = await new_ticket(attachments_http, entity="B")
    private_id, _ = await private_attachment(attachments_http, ticket_id)
    as_user(attachments_http, "A_staff")
    task = await attachments_http["client"].post(f"/api/v1/tickets/{ticket_id}/tasks", json={"body": "Do the thing"})
    assert task.status_code == 201
    other_followup = await post_followup(attachments_http, other_ticket, user="A_staff", body="another ticket")
    foreign_followup = await post_followup(attachments_http, foreign_ticket, user="B_staff", body="foreign ticket")

    for fields in (
        {"followup_id": private_id},
        {"task_id": task.json()["id"]},
        {"followup_id": other_followup.json()["id"]},
        {"followup_id": foreign_followup.json()["id"]},
        {"followup_id": 999999999},
    ):
        response = await upload_to(attachments_http, ticket_id, "x.txt", TEXT, user="A_requester", **fields)
        assert response.status_code == 404, fields
        assert response.json()["detail"] == "Not found"
    assert (await upload_to(attachments_http, ticket_id, "x.txt", TEXT, user="A_staff", followup_id=other_followup.json()["id"])).status_code == 404
    assert (await upload_to(attachments_http, ticket_id, "x.txt", TEXT, user="A_staff", followup_id=private_id, task_id=task.json()["id"])).status_code == 422


@pytest.mark.asyncio
async def test_requester_cannot_read_tasks_or_task_attachments(attachments_http) -> None:
    ticket_id = await new_ticket(attachments_http)
    as_user(attachments_http, "A_staff")
    task = await attachments_http["client"].post(f"/api/v1/tickets/{ticket_id}/tasks", json={"body": "Internal task"})
    assert task.status_code == 201
    upload = await upload_to(attachments_http, ticket_id, "task.png", PNG, user="A_staff", task_id=task.json()["id"])
    assert upload.status_code == 201
    document_id = upload.json()["id"]

    assert (await get(attachments_http, f"{ticket_id}/tasks", user="A_requester")).status_code == 403
    timeline = (await get(attachments_http, f"{ticket_id}/timeline", user="A_requester")).json()
    assert all(item["kind"] != "task" for item in timeline)
    assert (await get(attachments_http, f"{ticket_id}/documents", user="A_requester")).json() == []
    assert (await get(attachments_http, f"{ticket_id}/documents/{document_id}", user="A_requester")).status_code == 404
    assert (await get(attachments_http, f"{ticket_id}/documents/{document_id}/preview", user="A_requester")).status_code == 404

    assert (await get(attachments_http, f"{ticket_id}/tasks", user="A_staff")).status_code == 200
    assert any(item["kind"] == "task" for item in (await get(attachments_http, f"{ticket_id}/timeline", user="A_staff")).json())
    assert (await get(attachments_http, f"{ticket_id}/documents/{document_id}", user="A_staff")).status_code == 200


@pytest.mark.asyncio
async def test_limit_counts_hidden_files_but_the_requester_message_is_generic(attachments_http) -> None:
    ticket_id = await new_ticket(attachments_http)
    followup = await post_followup(attachments_http, ticket_id, user="A_staff", body="internal", is_private=True)
    for index in range(MAX_FILES_PER_TICKET - 1):
        response = await upload_to(attachments_http, ticket_id, f"hidden{index}.txt", TEXT, user="A_staff", followup_id=followup.json()["id"])
        assert response.status_code == 201
    assert (await upload_to(attachments_http, ticket_id, "visible.txt", TEXT, user="A_requester")).status_code == 201

    blocked = await upload_to(attachments_http, ticket_id, "one-more.txt", TEXT, user="A_requester")
    assert blocked.status_code == 422
    assert blocked.json()["detail"] == "Attachment limit reached"
    staff_blocked = await upload_to(attachments_http, ticket_id, "one-more.txt", TEXT, user="A_staff")
    assert staff_blocked.status_code == 422
    assert str(MAX_FILES_PER_TICKET) in staff_blocked.json()["detail"]


@pytest.mark.asyncio
async def test_attachment_only_reply_shows_attached_files_and_notifies_after_the_first_attachment(attachments_http, notifications) -> None:
    ticket_id = await new_ticket(attachments_http)
    reply = await post_followup(attachments_http, ticket_id, user="A_staff", body="", expects_attachments=True)
    assert reply.status_code == 201
    reply_id = reply.json()["id"]
    assert notifications == []
    for user in ("A_requester", "A_staff"):
        assert [item for item in (await get(attachments_http, f"{ticket_id}/timeline", user=user)).json() if item["kind"] == "followup"] == []
        assert (await get(attachments_http, f"{ticket_id}/followups", user=user)).json() == []

    for name in ("first.txt", "second.txt"):
        assert (await upload_to(attachments_http, ticket_id, name, TEXT, user="A_staff", followup_id=reply_id)).status_code == 201
    assert notifications == [(ticket_id, "public_reply")]

    entries = [item for item in (await get(attachments_http, f"{ticket_id}/timeline", user="A_requester")).json() if item["kind"] == "followup"]
    assert len(entries) == 1 and entries[0]["body"] == "Attached 2 files"
    assert len((await get(attachments_http, f"{ticket_id}/followups", user="A_requester")).json()) == 1


@pytest.mark.asyncio
async def test_attachment_only_reply_with_every_upload_failing_stays_hidden_and_sends_no_email(attachments_http, notifications) -> None:
    ticket_id = await new_ticket(attachments_http)
    reply = await post_followup(attachments_http, ticket_id, user="A_requester", body="", expects_attachments=True)
    assert reply.status_code == 201
    failed = await upload_to(attachments_http, ticket_id, "run.txt", b"#!/bin/sh\necho hi", user="A_requester", followup_id=reply.json()["id"])
    assert failed.status_code in {415, 422}
    assert notifications == []
    for user in ("A_requester", "A_staff"):
        assert [item for item in (await get(attachments_http, f"{ticket_id}/timeline", user=user)).json() if item["kind"] == "followup"] == []
        assert (await get(attachments_http, f"{ticket_id}/followups", user=user)).json() == []
    rows = (await attachments_http["db"].scalars(select(TicketFollowup).where(TicketFollowup.ticket_id == ticket_id))).all()
    assert len(rows) == 1  # hidden, not deleted


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["", "   ", "<p><br></p>"])
async def test_empty_reply_without_the_attachment_flag_returns_422(attachments_http, notifications, body) -> None:
    ticket_id = await new_ticket(attachments_http)
    assert (await post_followup(attachments_http, ticket_id, user="A_requester", body=body)).status_code == 422
    assert (await post_followup(attachments_http, ticket_id, user="A_requester", body=body, expects_attachments=False)).status_code == 422
    assert (await post_followup(attachments_http, ticket_id, user="A_requester")).status_code == 422
    assert notifications == []


@pytest.mark.asyncio
async def test_private_attachment_only_reply_never_notifies_and_is_staff_only(attachments_http, notifications) -> None:
    ticket_id = await new_ticket(attachments_http)
    assert (await post_followup(attachments_http, ticket_id, user="A_requester", body="", is_private=True, expects_attachments=True)).status_code == 403
    reply = await post_followup(attachments_http, ticket_id, user="A_staff", body="", is_private=True, expects_attachments=True)
    assert reply.status_code == 201
    assert (await upload_to(attachments_http, ticket_id, "note.txt", TEXT, user="A_staff", followup_id=reply.json()["id"])).status_code == 201
    assert notifications == []
    assert [item for item in (await get(attachments_http, f"{ticket_id}/timeline", user="A_requester")).json() if item["kind"] == "followup"] == []
    assert [item["body"] for item in (await get(attachments_http, f"{ticket_id}/timeline", user="A_staff")).json() if item["kind"] == "followup"] == ["Attached 1 file"]


@pytest.mark.asyncio
async def test_legacy_documents_without_metadata_list_download_and_preview(attachments_http) -> None:
    ticket_id = await new_ticket(attachments_http)
    root = attachments_http["root"]
    image = root / "legacy-image.png"
    image.write_bytes(PNG)
    blob = root / "legacy-blob.bin"
    blob.write_bytes(b"unknown legacy bytes")
    uploader = attachments_http["users"]["A_requester"]
    db = attachments_http["db"]
    db.add_all(
        [
            TicketDocument(ticket_id=ticket_id, filename="old.png", path=str(image), uploaded_by=uploader.id),
            TicketDocument(ticket_id=ticket_id, filename="old.dat", path=str(blob), uploaded_by=uploader.id),
        ]
    )
    await db.flush()

    for user in ("A_requester", "A_staff"):
        listed = {item["filename"]: item for item in (await get(attachments_http, f"{ticket_id}/documents", user=user)).json()}
        assert listed["old.png"]["size_bytes"] == len(PNG)
        assert listed["old.png"]["content_type"] == "image/png"
        assert listed["old.png"]["previewable"] is True
        assert listed["old.dat"]["size_bytes"] == len(b"unknown legacy bytes")
        assert listed["old.dat"]["previewable"] is False
        download = await get(attachments_http, f"{ticket_id}/documents/{listed['old.dat']['id']}", user=user)
        assert download.status_code == 200
        assert download.headers["content-disposition"].startswith("attachment")
        assert download.headers["x-content-type-options"] == "nosniff"
        preview = await get(attachments_http, f"{ticket_id}/documents/{listed['old.png']['id']}/preview", user=user)
        assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"
        assert (await get(attachments_http, f"{ticket_id}/documents/{listed['old.dat']['id']}/preview", user=user)).status_code == 415
