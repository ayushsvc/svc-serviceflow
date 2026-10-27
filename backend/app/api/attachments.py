from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from app.api.tickets import can_update_tickets, scoped_ticket
from app.deps import CurrentUser, DbSession
from app.models import Ticket, TicketDocument, User
from app.services.attachments import (
    MAX_FILE_BYTES,
    MAX_FILES_PER_TICKET,
    PREVIEW_CONTENT_TYPES,
    TOO_MANY_FILES_MESSAGE,
    AttachmentError,
    content_disposition,
    detect_document_type,
    validate_attachment,
)
from app.services.audit import record_audit

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


def stored_size(document: TicketDocument) -> int | None:
    if document.size_bytes is not None:
        return document.size_bytes
    try:
        return Path(document.path).stat().st_size
    except OSError:
        return None


def document_view(document: TicketDocument, uploader_name: str | None, user: User, ticket: Ticket) -> dict[str, object]:
    return {
        "id": document.id,
        "filename": document.filename,
        "content_type": document.content_type,
        "size_bytes": stored_size(document),
        "uploaded_by": document.uploaded_by,
        "uploaded_by_name": uploader_name,
        "created_at": document.created_at,
        "previewable": document.content_type in PREVIEW_CONTENT_TYPES,
        "can_remove": can_remove_document(user, ticket, document),
    }


async def get_active_document(db: DbSession, ticket: Ticket, document_id: int) -> TicketDocument:
    document = await db.scalar(
        select(TicketDocument).where(
            TicketDocument.id == document_id,
            TicketDocument.ticket_id == ticket.id,
            TicketDocument.removed_at.is_(None),
        )
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


def stored_file(document: TicketDocument) -> Path:
    root = UPLOAD_ROOT.resolve()
    path = Path(document.path).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Document not found")
    return path


@router.get("/tickets/{ticket_id}/documents")
async def list_documents(ticket_id: int, user: CurrentUser, db: DbSession) -> list[dict[str, object]]:
    ticket = await scoped_ticket(ticket_id, user, db)
    rows = (
        await db.execute(
            select(TicketDocument, User.name)
            .outerjoin(User, User.id == TicketDocument.uploaded_by)
            .where(TicketDocument.ticket_id == ticket.id, TicketDocument.removed_at.is_(None))
            .order_by(TicketDocument.created_at, TicketDocument.id)
        )
    ).all()
    return [document_view(document, name, user, ticket) for document, name in rows]


@router.post("/tickets/{ticket_id}/documents", status_code=status.HTTP_201_CREATED)
async def upload_document(ticket_id: int, user: CurrentUser, db: DbSession, document: UploadFile = File(...)) -> dict[str, object]:
    ticket = await scoped_ticket(ticket_id, user, db)
    content = await document.read(MAX_FILE_BYTES + 1)
    try:
        validated = validate_attachment(content, document.filename)
    except AttachmentError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error

    # The ticket row lock serialises concurrent uploads so the per-ticket limit cannot be exceeded.
    await db.scalar(select(Ticket.id).where(Ticket.id == ticket.id).with_for_update())
    existing = await db.scalar(
        select(func.count(TicketDocument.id)).where(
            TicketDocument.ticket_id == ticket.id,
            TicketDocument.removed_at.is_(None),
        )
    )
    if (existing or 0) >= MAX_FILES_PER_TICKET:
        raise HTTPException(status_code=422, detail=TOO_MANY_FILES_MESSAGE)

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
            },
        )
        await db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    await db.refresh(record)
    return document_view(record, user.name, user, ticket)


@router.get("/tickets/{ticket_id}/documents/{document_id}")
async def download_document(ticket_id: int, document_id: int, user: CurrentUser, db: DbSession) -> FileResponse:
    ticket = await scoped_ticket(ticket_id, user, db)
    document = await get_active_document(db, ticket, document_id)
    response = FileResponse(stored_file(document), media_type="application/octet-stream")
    response.headers["Content-Disposition"] = content_disposition(document.filename)
    response.headers.update(PROTECTIVE_HEADERS)
    return response


@router.get("/tickets/{ticket_id}/documents/{document_id}/preview")
async def preview_document(ticket_id: int, document_id: int, user: CurrentUser, db: DbSession) -> FileResponse:
    ticket = await scoped_ticket(ticket_id, user, db)
    document = await get_active_document(db, ticket, document_id)
    path = stored_file(document)
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
    document = await get_active_document(db, ticket, document_id)
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
