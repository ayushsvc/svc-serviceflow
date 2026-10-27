from app.services.attachments import detect_document_type


def test_attachment_type_detection_uses_content_signatures() -> None:
    assert detect_document_type(b"%PDF-1.7") == "application/pdf"
    assert detect_document_type(b"\x89PNG\r\n\x1a\nrest") == "image/png"
    assert detect_document_type(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert detect_document_type(b"plain UTF-8 text") == "text/plain"
    assert detect_document_type(b"\x00binary") is None


def test_attachment_content_does_not_infer_pdf_from_extension() -> None:
    assert detect_document_type(b"not a pdf") != "application/pdf"
