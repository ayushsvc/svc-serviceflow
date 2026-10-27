"""Attachment policy: the single place for allowed types, limits and content validation."""

import io
import re
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import and_, exists, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Ticket, TicketDocument, TicketFollowup, TicketTask, User

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES_PER_TICKET = 5
ZIP_MAX_ENTRIES = 1000
ZIP_MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
ZIP_MAX_RATIO = 100
ZIP_ENTRY_RATIO_MIN_BYTES = 1024 * 1024
CONTENT_TYPES_MAX_BYTES = 1024 * 1024
MAX_FILENAME_LENGTH = 200

FILE_TOO_LARGE_MESSAGE = "File exceeds the 10 MB limit."
TOO_MANY_FILES_MESSAGE = "A ticket can have at most 5 attachments. Remove one before adding another."
GENERIC_LIMIT_MESSAGE = "Attachment limit reached"


@dataclass(frozen=True)
class AttachmentKind:
    key: str
    content_type: str
    extensions: frozenset[str]
    previewable: bool = False


ATTACHMENT_KINDS: dict[str, AttachmentKind] = {
    "pdf": AttachmentKind("pdf", "application/pdf", frozenset({".pdf"})),
    "png": AttachmentKind("png", "image/png", frozenset({".png"}), previewable=True),
    "jpg": AttachmentKind("jpg", "image/jpeg", frozenset({".jpg", ".jpeg"}), previewable=True),
    "txt": AttachmentKind("txt", "text/plain", frozenset({".txt"})),
    "csv": AttachmentKind("csv", "text/csv", frozenset({".csv"})),
    "log": AttachmentKind("log", "text/plain", frozenset({".log"})),
    "docx": AttachmentKind(
        "docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        frozenset({".docx"}),
    ),
    "xlsx": AttachmentKind(
        "xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        frozenset({".xlsx"}),
    ),
}
ALLOWED_EXTENSIONS = frozenset().union(*(kind.extensions for kind in ATTACHMENT_KINDS.values()))
TEXT_EXTENSIONS = {".txt": "txt", ".csv": "csv", ".log": "log"}
PREVIEW_CONTENT_TYPES = frozenset(kind.content_type for kind in ATTACHMENT_KINDS.values() if kind.previewable)

_SCRIPT_SIGNATURES = (b"#!",)
_BINARY_EXECUTABLE_SIGNATURES = (b"MZ", b"\x7fELF", b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe")
_MARKUP_PREFIXES = ("<svg", "<!doctype", "<html", "<?xml", "<script", "<head", "<body", "<iframe")
_ARCHIVE_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"Rar!", b"7z\xbc\xaf", b"\x1f\x8b")
_ARCHIVE_SUFFIXES = (".zip", ".rar", ".7z", ".gz", ".tgz", ".tar", ".jar", ".docx", ".xlsx", ".pptx")
_OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class AttachmentError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class ValidatedAttachment:
    kind: AttachmentKind
    filename: str
    extension: str

    @property
    def content_type(self) -> str:
        return self.kind.content_type


def normalise_filename(raw: str | None) -> str:
    if not raw or "/" in raw or "\\" in raw or "\x00" in raw:
        raise AttachmentError(422, "Invalid file name.")
    name = re.sub(r"[\x00-\x1f\x7f\"<>|:*?]", "_", raw).strip(" .")
    if not name or len(name) > MAX_FILENAME_LENGTH:
        raise AttachmentError(422, "Invalid file name.")
    return name


def _is_valid_text(content: bytes) -> bool:
    if b"\x00" in content:
        return False
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _sniff(content: bytes) -> str | None:
    if content.startswith(b"%PDF-"):
        return "pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if content.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return "zip"
    if content.startswith(_OLE_SIGNATURE):
        return "ole"
    if content.startswith(_SCRIPT_SIGNATURES):
        return "executable"
    text = _is_valid_text(content)
    if content.startswith(_BINARY_EXECUTABLE_SIGNATURES) and not text:
        return "executable"
    return "text" if text else None


def detect_document_type(content: bytes) -> str | None:
    """Content-type of a payload by magic bytes only; the strict checks live in validate_attachment."""
    sniffed = _sniff(content)
    return {
        "pdf": "application/pdf",
        "png": "image/png",
        "jpg": "image/jpeg",
        "text": "text/plain",
    }.get(sniffed or "")


def _looks_like_markup(content: bytes) -> bool:
    head = content[:2048].decode("utf-8", errors="ignore").lstrip("\ufeff \t\r\n").lower()
    return head.startswith(_MARKUP_PREFIXES)


def _read_head(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    try:
        with archive.open(info) as handle:
            return handle.read(8)
    except (zipfile.BadZipFile, RuntimeError, zlib.error, NotImplementedError, EOFError) as exc:
        raise AttachmentError(422, "The Office file is corrupt or encrypted.") from exc


def _inspect_ooxml(content: bytes) -> str:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise AttachmentError(422, "The archive is corrupt.") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > ZIP_MAX_ENTRIES:
            raise AttachmentError(422, "The Office file contains too many entries.")
        names: dict[str, zipfile.ZipInfo] = {}
        total_uncompressed = 0
        total_compressed = 0
        for info in infos:
            name = info.filename
            parts = name.split("/")
            if "\\" in name or "\x00" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in parts:
                raise AttachmentError(422, "The Office file contains an unsafe entry path.")
            lowered = name.lower()
            if lowered in names:
                raise AttachmentError(422, "The Office file contains duplicate entries.")
            names[lowered] = info
            if info.flag_bits & 0x1:
                raise AttachmentError(422, "Encrypted Office files are not allowed.")
            total_uncompressed += info.file_size
            total_compressed += info.compress_size
            if info.file_size >= ZIP_ENTRY_RATIO_MIN_BYTES and (
                info.compress_size == 0 or info.file_size / info.compress_size > ZIP_MAX_RATIO
            ):
                raise AttachmentError(422, "The Office file expands too much when unpacked.")
        if total_uncompressed > ZIP_MAX_UNCOMPRESSED_BYTES:
            raise AttachmentError(422, "The Office file expands too much when unpacked.")
        if total_uncompressed / max(len(content), 1) > ZIP_MAX_RATIO or total_compressed > len(content):
            raise AttachmentError(422, "The Office file expands too much when unpacked.")

        content_types_info = names.get("[content_types].xml")
        if content_types_info is None:
            raise AttachmentError(415, "ZIP archives are not allowed.")
        if content_types_info.file_size > CONTENT_TYPES_MAX_BYTES:
            raise AttachmentError(422, "The Office file is invalid.")
        try:
            content_types = archive.read(content_types_info).decode("utf-8", errors="replace").lower()
        except (zipfile.BadZipFile, RuntimeError, zlib.error, NotImplementedError, EOFError) as exc:
            raise AttachmentError(422, "The Office file is corrupt or encrypted.") from exc

        if (
            "macroenabled" in content_types
            or "vbaproject" in content_types
            or any(name.endswith("vbaproject.bin") or name.endswith("vbadata.xml") for name in names)
        ):
            raise AttachmentError(415, "Macro-enabled Office files are not allowed.")

        for lowered, info in names.items():
            if info.is_dir():
                continue
            if lowered.endswith(_ARCHIVE_SUFFIXES) or _read_head(archive, info).startswith(_ARCHIVE_SIGNATURES):
                raise AttachmentError(422, "Archives nested inside Office files are not allowed.")

        if "wordprocessingml.document.main+xml" in content_types and "word/document.xml" in names:
            return "docx"
        if "spreadsheetml.sheet.main+xml" in content_types and "xl/workbook.xml" in names:
            return "xlsx"
        raise AttachmentError(415, "Only DOCX and XLSX Office files are allowed.")


def validate_attachment(content: bytes, filename: str | None) -> ValidatedAttachment:
    """Validate by content; the name only has to agree with what the content really is."""
    name = normalise_filename(filename)
    if not content:
        raise AttachmentError(422, "File is empty.")
    if len(content) > MAX_FILE_BYTES:
        raise AttachmentError(413, FILE_TOO_LARGE_MESSAGE)
    suffix = PurePosixPath(name).suffix.lower()
    sniffed = _sniff(content)

    if sniffed in {"executable", "ole"}:
        raise AttachmentError(415, "Executables, scripts and legacy or encrypted Office files are not allowed.")
    if sniffed == "png" and content[12:16] != b"IHDR":
        raise AttachmentError(422, "The PNG image is corrupt.")
    if sniffed == "zip":
        kind_key = _inspect_ooxml(content)
    elif sniffed == "text":
        if _looks_like_markup(content):
            raise AttachmentError(415, "SVG, HTML and script files are not allowed.")
        kind_key = TEXT_EXTENSIONS.get(suffix)
        if kind_key is None:
            raise AttachmentError(415, "Unsupported file type. Allowed: PDF, PNG, JPG, TXT, CSV, LOG, DOCX, XLSX.")
    elif sniffed is None:
        if suffix in TEXT_EXTENSIONS:
            raise AttachmentError(422, "The file is not valid text.")
        if suffix in ALLOWED_EXTENSIONS:
            raise AttachmentError(422, f"The file content is not a valid {suffix[1:].upper()} file.")
        raise AttachmentError(415, "Unsupported file type. Allowed: PDF, PNG, JPG, TXT, CSV, LOG, DOCX, XLSX.")
    else:
        kind_key = sniffed

    kind = ATTACHMENT_KINDS[kind_key]
    if suffix not in kind.extensions:
        raise AttachmentError(415, "The file extension does not match the file content.")
    return ValidatedAttachment(kind=kind, filename=name, extension=suffix)


def content_disposition(filename: str, *, inline: bool = False) -> str:
    fallback = re.sub(r"[^A-Za-z0-9._ -]", "_", filename) or "download"
    disposition = "inline" if inline else "attachment"
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


def can_see_private_items(user: User) -> bool:
    # Same rule as require_helpdesk: private notes, private followups and tasks are staff-only.
    return user.profile.interface == "helpdesk"


def visible_document_filter(user: User):
    """The one SQL condition deciding which documents a user may see."""
    if can_see_private_items(user):
        return true()
    private_followups = select(TicketFollowup.id).where(TicketFollowup.is_private.is_(True))
    return and_(
        TicketDocument.task_id.is_(None),
        or_(TicketDocument.followup_id.is_(None), TicketDocument.followup_id.not_in(private_followups)),
    )


def live_followup_filter():
    """A followup is shown only if it has text or at least one live attachment."""
    return or_(
        TicketFollowup.body != "",
        exists(
            select(TicketDocument.id).where(
                TicketDocument.followup_id == TicketFollowup.id,
                TicketDocument.removed_at.is_(None),
            )
        ),
    )


async def get_visible_document(db: AsyncSession, ticket: Ticket, user: User, document_id: int) -> TicketDocument:
    document = await db.scalar(
        select(TicketDocument).where(
            TicketDocument.id == document_id,
            TicketDocument.ticket_id == ticket.id,
            TicketDocument.removed_at.is_(None),
            visible_document_filter(user),
        )
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Not found")
    return document


async def list_visible_documents(db: AsyncSession, ticket: Ticket, user: User):
    return (
        await db.execute(
            select(TicketDocument, User.name)
            .outerjoin(User, User.id == TicketDocument.uploaded_by)
            .where(
                TicketDocument.ticket_id == ticket.id,
                TicketDocument.removed_at.is_(None),
                visible_document_filter(user),
            )
            .order_by(TicketDocument.created_at, TicketDocument.id)
        )
    ).all()


async def resolve_attachment_source(
    db: AsyncSession, ticket: Ticket, user: User, followup_id: int | None, task_id: int | None
) -> tuple[TicketFollowup | None, TicketTask | None]:
    """Validate the followup or task an upload is attached to; any mismatch is a plain 404."""
    if followup_id is not None and task_id is not None:
        raise HTTPException(status_code=422, detail="Attach to a followup or a task, not both")
    not_found = HTTPException(status_code=404, detail="Not found")
    staff = can_see_private_items(user)
    if followup_id is not None:
        followup = await db.scalar(
            select(TicketFollowup).where(TicketFollowup.id == followup_id, TicketFollowup.ticket_id == ticket.id)
        )
        if followup is None or (not staff and (followup.is_private or followup.author_id != user.id)):
            raise not_found
        return followup, None
    if task_id is not None:
        task = await db.scalar(select(TicketTask).where(TicketTask.id == task_id, TicketTask.ticket_id == ticket.id))
        if task is None or not staff:
            raise not_found
        return None, task
    return None, None


async def live_document_count(db: AsyncSession, ticket_id: int, *, followup_id: int | None = None) -> int:
    query = select(func.count(TicketDocument.id)).where(
        TicketDocument.ticket_id == ticket_id, TicketDocument.removed_at.is_(None)
    )
    if followup_id is not None:
        query = query.where(TicketDocument.followup_id == followup_id)
    return (await db.scalar(query)) or 0


def limit_message(user: User) -> str:
    return TOO_MANY_FILES_MESSAGE if can_see_private_items(user) else GENERIC_LIMIT_MESSAGE


def infer_content_type(path: Path | None, filename: str) -> str | None:
    """Content type for legacy rows that were stored without one."""
    head = b""
    if path is not None:
        try:
            with path.open("rb") as handle:
                head = handle.read(16)
        except OSError:
            head = b""
    detected = detect_document_type(head) if head else None
    if detected:
        return detected
    suffix = PurePosixPath(filename).suffix.lower()
    for kind in ATTACHMENT_KINDS.values():
        if suffix in kind.extensions:
            return kind.content_type
    return None
