import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.tickets import can_update_tickets, scoped_ticket
from app.deps import CurrentUser, DbSession
from app.models import Ticket, TicketDocument, User
from app.services.attachments import (
    MAX_FILE_BYTES,
    MAX_FILES_PER_TICKET,
    PREVIEW_CONTENT_TYPES,
    AttachmentError,
    content_disposition,
    detect_document_type,
    get_visible_document,
    infer_content_type,
    limit_message,
    list_visible_documents,
    live_document_count,
    resolve_attachment_source,
    validate_attachment,
)
from app.services.audit import record_audit
from app.workers.tasks import send_ticket_notification

logger = logging.getLogger(__name__)
router = APIRouter(tags=["attachments"])
UPLOAD_ROOT = Path("/app/data/documents")
PROTECTIVE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cache-Control": "private, no-store",
}


def can_remove_document(user: User, ticket: Ticket, document: TicketDocument) -> bool:
    if can_update_tickets(user):
        return True
    return document.uploaded_by == user.id and ticket.status == "NEW"


def locate_stored_file(document: TicketDocument) -> tuple[Path | None, str]:
    """Resolve the stored path; only files inside the uploads root are ever usable.

    Returns (path, "ok") or (None, "missing" | "outside").
    """
    root = UPLOAD_ROOT.resolve()
    try:
        path = Path(document.path).resolve()
        if root not in path.parents:
            return None, "outside"
        if not path.is_file():
            return None, "missing"
    except (OSError, ValueError):
        return None, "missing"
    return path, "ok"


def stored_size(document: TicketDocument) -> int | None:
    if document.size_bytes is not None:
        return document.size_bytes
    path, _ = locate_stored_file(document)
    if path is None:
        return None
    try:
        return path.stat().st_size
    except OSError:
        return None


def effective_content_type(document: TicketDocument) -> str | None:
    if document.content_type:
        return document.content_type
    path, _ = locate_stored_file(document)
    return infer_content_type(path, document.filename)


def document_view(document: TicketDocument, uploader_name: str | None, user: User, ticket: Ticket) -> dict[str, object]:
    content_type = effective_content_type(document)
    missing = locate_stored_file(document)[1] == "missing"
    return {
        "id": document.id,
        "filename": document.filename,
        "content_type": content_type,
        "size_bytes": stored_size(document),
        "uploaded_by": document.uploaded_by,
        "uploaded_by_name": uploader_name,
        "created_at": document.created_at,
        "followup_id": document.followup_id,
        "task_id": document.task_id,
        "previewable": content_type in PREVIEW_CONTENT_TYPES and locate_stored_file(document)[0] is not None,
        "file_missing": missing,
        "can_remove": can_remove_document(user, ticket, document),
    }


def stored_file(document: TicketDocument, ticket: Ticket) -> Path:
    path, state = locate_stored_file(document)
    if path is None:
        logger.warning(
            "Attachment file unavailable: document_id=%s ticket_id=%s entity_id=%s reason=%s",
            document.id,
            ticket.id,
            ticket.entity_id,
            "outside_uploads_root" if state == "outside" else "missing",
        )
        raise HTTPException(status_code=404, detail="Not found")
    return path


@router.get("/tickets/{ticket_id}/documents")
async def list_documents(ticket_id: int, user: CurrentUser, db: DbSession) -> list[dict[str, object]]:
    ticket = await scoped_ticket(ticket_id, user, db)
    rows = await list_visible_documents(db, ticket, user)
    return [document_view(document, name, user, ticket) for document, name in rows]


@router.post("/tickets/{ticket_id}/documents", status_code=status.HTTP_201_CREATED)
async def upload_document(
    ticket_id: int,
    user: CurrentUser,
    db: DbSession,
    document: UploadFile = File(...),
    followup_id: int | None = Form(default=None),
    task_id: int | None = Form(default=None),
) -> dict[str, object]:
    ticket = await scoped_ticket(ticket_id, user, db)
    followup, task = await resolve_attachment_source(db, ticket, user, followup_id, task_id)
    content = await document.read(MAX_FILE_BYTES + 1)
    try:
        validated = validate_attachment(content, document.filename)
    except AttachmentError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error

    # The ticket row lock serialises concurrent uploads so the per-ticket limit cannot be exceeded.
    # The count includes documents the user cannot see; only the message differs.
    await db.scalar(select(Ticket.id).where(Ticket.id == ticket.id).with_for_update())
    if await live_document_count(db, ticket.id) >= MAX_FILES_PER_TICKET:
        raise HTTPException(status_code=422, detail=limit_message(user))
    first_for_reply = followup is not None and await live_document_count(db, ticket.id, followup_id=followup.id) == 0

    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_ROOT / f"{uuid4().hex}{validated.extension}"
    path.write_bytes(content)
    try:
        record = TicketDocument(
            ticket_id=ticket.id,
            filename=validated.filename,
            path=str(path),
            uploaded_by=user.id,
            content_type=validated.content_type,
            size_bytes=len(content),
            followup_id=followup.id if followup else None,
            task_id=task.id if task else None,
        )
        db.add(record)
        await db.flush()
        await record_audit(
            db,
            entity="ticket_document",
            entity_id=record.id,
            action="uploaded",
            actor_id=user.id,
            diff={
                "ticket_id": ticket.id,
                "filename": record.filename,
                "size_bytes": len(content),
                "content_type": validated.content_type,
                "actor_id": user.id,
                "followup_id": record.followup_id,
                "task_id": record.task_id,
            },
        )
        await db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    await db.refresh(record)
    if followup is not None and first_for_reply and not followup.is_private and followup.body == "":
        send_ticket_notification.delay(ticket.id, "public_reply")
    return document_view(record, user.name, user, ticket)


@router.get("/tickets/{ticket_id}/documents/{document_id}")
async def download_document(ticket_id: int, document_id: int, user: CurrentUser, db: DbSession) -> FileResponse:
    ticket = await scoped_ticket(ticket_id, user, db)
    document = await get_visible_document(db, ticket, user, document_id)
    response = FileResponse(stored_file(document, ticket), media_type="application/octet-stream")
    response.headers["Content-Disposition"] = content_disposition(document.filename)
    response.headers.update(PROTECTIVE_HEADERS)
    return response


@router.get("/tickets/{ticket_id}/documents/{document_id}/preview")
async def preview_document(ticket_id: int, document_id: int, user: CurrentUser, db: DbSession) -> FileResponse:
    ticket = await scoped_ticket(ticket_id, user, db)
    document = await get_visible_document(db, ticket, user, document_id)
    path = stored_file(document, ticket)
    with path.open("rb") as handle:
        detected = detect_document_type(handle.read(16))
    if detected not in PREVIEW_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail="Preview is only available for PNG and JPG images")
    response = FileResponse(path, media_type=detected)
    response.headers["Content-Disposition"] = content_disposition(document.filename, inline=True)
    response.headers.update(PROTECTIVE_HEADERS)
    return response


@router.delete("/tickets/{ticket_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_document(ticket_id: int, document_id: int, user: CurrentUser, db: DbSession) -> None:
    ticket = await scoped_ticket(ticket_id, user, db)
    document = await get_visible_document(db, ticket, user, document_id)
    if not can_remove_document(user, ticket, document):
        raise HTTPException(status_code=403, detail="You cannot remove this attachment")
    document.removed_at = datetime.now(UTC)
    document.removed_by = user.id
    await record_audit(
        db,
        entity="ticket_document",
        entity_id=document.id,
        action="removed",
        actor_id=user.id,
        diff={
            "ticket_id": ticket.id,
            "filename": document.filename,
            "size_bytes": stored_size(document),
            "actor_id": user.id,
        },
    )
    await db.commit()
