"use client";

import { useCallback, useEffect, useState } from "react";

type DocumentRow = {
  id: number;
  filename: string;
  content_type: string | null;
  size_bytes: number | null;
  uploaded_by_name: string | null;
  created_at: string;
  previewable: boolean;
  file_missing: boolean;
};

function formatSize(bytes: number | null) {
  if (bytes === null || bytes === undefined) return "Unknown size";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function TicketAttachments({ ticketId, headers, refreshKey }: { ticketId: number; headers: Record<string, string>; refreshKey?: number }) {
  const [documents, setDocuments] = useState<DocumentRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [message, setMessage] = useState("");
  const [preview, setPreview] = useState<{ url: string; name: string } | null>(null);

  const load = useCallback(async () => {
    const response = await fetch(`/api/v1/tickets/${ticketId}/documents`, { headers });
    if (response.ok) setDocuments((await response.json()) as DocumentRow[]);
    else setDocuments([]);
    setLoaded(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticketId, headers.Authorization]);

  useEffect(() => { setLoaded(false); setMessage(""); void load(); }, [load, refreshKey]);
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview.url); }, [preview]);
  useEffect(() => {
    if (!preview) return undefined;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setPreview(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [preview]);

  async function fetchBlob(document: DocumentRow, suffix: string) {
    const response = await fetch(`/api/v1/tickets/${ticketId}/documents/${document.id}${suffix}`, { headers });
    if (!response.ok) { setMessage("This file is not available."); void load(); return null; }
    return response.blob();
  }
  async function download(document: DocumentRow) {
    const blob = await fetchBlob(document, "");
    if (!blob) return;
    const url = URL.createObjectURL(blob);
    const link = window.document.createElement("a");
    link.href = url;
    link.download = document.filename;
    link.click();
    URL.revokeObjectURL(url);
    setMessage("");
  }
  async function openPreview(document: DocumentRow) {
    const blob = await fetchBlob(document, "/preview");
    if (blob) { setMessage(""); setPreview({ url: URL.createObjectURL(blob), name: document.filename }); }
  }

  return (
    <section className="drawer-section ticket-attachments" aria-label="Attachments">
      <h3>Attachments {loaded && <span className="muted">({documents.length})</span>}</h3>
      {message && <p className="form-error" role="alert">{message}</p>}
      {loaded && !documents.length && <p className="muted">No attachments.</p>}
      <ul className="attachment-list">
        {documents.map((document) => (
          <li key={document.id} className="attachment-row">
            <div className="attachment-meta">
              <strong>{document.filename}</strong>
              <span>{document.content_type ?? "Unknown type"} · {document.file_missing ? "size unavailable" : formatSize(document.size_bytes)}</span>
              <span>{document.uploaded_by_name ?? "Unknown user"} · {new Date(document.created_at).toLocaleString()}</span>
              {document.file_missing && <span className="status-badge file-missing">File missing</span>}
            </div>
            {!document.file_missing && <div className="attachment-actions">
              {document.previewable && <button type="button" className="secondary-button" onClick={() => void openPreview(document)}>Preview</button>}
              <button type="button" className="secondary-button" onClick={() => void download(document)}>Download</button>
            </div>}
          </li>
        ))}
      </ul>
      {preview && <div className="lightbox" role="dialog" aria-modal="true" aria-label={`Preview of ${preview.name}`} onClick={() => setPreview(null)}>
        <div className="lightbox-body" onClick={(event) => event.stopPropagation()}>
          <button type="button" className="secondary-button" autoFocus onClick={() => setPreview(null)}>Close</button>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={preview.url} alt={preview.name} />
        </div>
      </div>}
    </section>
  );
}
