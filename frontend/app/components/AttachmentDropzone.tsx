"use client";

import { ChangeEvent, Dispatch, DragEvent, SetStateAction, useEffect, useId, useRef, useState } from "react";
import {
  ACCEPT,
  AttachmentItem,
  MAX_FILES_PER_TICKET,
  TOO_MANY_FILES_MESSAGE,
  formatFileSize,
  makeItem,
  removeRemote,
  revokePreview,
  unsentCount,
  uploadItems,
} from "../lib/attachments";
import { getImageFilesFromClipboard } from "../lib/clipboard";

type AttachmentDropzoneProps = {
  label?: string;
  items: AttachmentItem[];
  setItems: Dispatch<SetStateAction<AttachmentItem[]>>;
  headers: Record<string, string>;
  // When set, Retry uploads immediately and Remove also deletes the stored attachment.
  ticketId?: number | null;
  existingCount?: number;
  disabled?: boolean;
};

export default function AttachmentDropzone({ label = "Attachments", items, setItems, headers, ticketId = null, existingCount = 0, disabled = false }: AttachmentDropzoneProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const itemsRef = useRef(items);
  const [notice, setNotice] = useState("");
  const [dragging, setDragging] = useState(false);
  const hintId = useId();
  itemsRef.current = items;

  function addFiles(files: File[], pasted = false) {
    if (disabled || !files.length) return;
    const room = Math.max(0, MAX_FILES_PER_TICKET - existingCount - itemsRef.current.length);
    const accepted = files.slice(0, room);
    if (accepted.length < files.length) setNotice(TOO_MANY_FILES_MESSAGE);
    else setNotice(pasted ? `${accepted.length} screenshot${accepted.length === 1 ? "" : "s"} attached.` : `${accepted.length} file${accepted.length === 1 ? "" : "s"} added.`);
    if (accepted.length) setItems((current) => [...current, ...accepted.map((file) => makeItem(file, pasted))]);
  }

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return undefined;
    const target: HTMLElement = root.closest("form") ?? root;
    function onPaste(event: ClipboardEvent) {
      const images = getImageFilesFromClipboard(event.clipboardData);
      if (!images.length) return;
      event.preventDefault();
      addFiles(images, true);
    }
    target.addEventListener("paste", onPaste);
    return () => target.removeEventListener("paste", onPaste);
    // addFiles reads current values through refs and props captured per render
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [disabled, existingCount]);

  useEffect(() => {
    if (!unsentCount(items)) return undefined;
    function warn(event: BeforeUnloadEvent) {
      event.preventDefault();
      event.returnValue = "";
    }
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [items]);

  async function removeItem(item: AttachmentItem) {
    if (item.status === "done" && item.remoteId && ticketId) {
      const ok = await removeRemote(ticketId, item.remoteId, headers);
      if (!ok) {
        setNotice(`${item.name} could not be removed.`);
        return;
      }
    }
    revokePreview(item);
    setItems((current) => current.filter((candidate) => candidate.id !== item.id));
    setNotice(`${item.name} removed.`);
  }

  async function retry(item: AttachmentItem) {
    if (!ticketId) {
      setItems((current) => current.map((candidate) => (candidate.id === item.id ? { ...candidate, status: "pending", error: undefined } : candidate)));
      return;
    }
    await uploadItems(ticketId, [item.id], itemsRef.current, setItems, headers);
  }

  return (
    <div className="attachment-dropzone" ref={rootRef}>
      <span className="attachment-label">{label} <span>Optional</span></span>
      <div
        className={`attachment-drop${dragging ? " is-dragging" : ""}`}
        onDragOver={(event: DragEvent<HTMLDivElement>) => { event.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event: DragEvent<HTMLDivElement>) => {
          event.preventDefault();
          setDragging(false);
          addFiles(Array.from(event.dataTransfer.files));
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          multiple
          disabled={disabled}
          aria-label={`${label}: choose files`}
          aria-describedby={hintId}
          onChange={(event: ChangeEvent<HTMLInputElement>) => {
            addFiles(Array.from(event.target.files ?? []));
            event.target.value = "";
          }}
        />
        <strong>Drop files here or choose files</strong>
        <small>PDF, PNG, JPG, TXT, CSV, LOG, DOCX or XLSX. Up to 10 MB per file, 5 files per ticket.</small>
      </div>
      <small id={hintId} className="attachment-hint">Drag files here, click to browse, or paste a screenshot with Ctrl+V</small>
      <div className="attachment-live" role="status" aria-live="polite">{notice}</div>
      {items.length > 0 && (
        <ul className="attachment-list">
          {items.map((item) => (
            <li key={item.id} className={`attachment-item attachment-status-${item.status}`}>
              {item.previewUrl
                // eslint-disable-next-line @next/next/no-img-element
                ? <img src={item.previewUrl} alt={`Preview of ${item.name}`} className="attachment-thumb" />
                : <span className="attachment-thumb attachment-thumb-file" aria-hidden="true">FILE</span>}
              <span className="attachment-meta">
                <strong>{item.name}</strong>
                <small>
                  {formatFileSize(item.size)}
                  {item.status === "uploading" && ` · Uploading ${item.progress}%`}
                  {item.status === "done" && " · Uploaded"}
                  {item.status === "pending" && " · Ready to send"}
                </small>
                {item.status === "uploading" && <progress max={100} value={item.progress} aria-label={`Uploading ${item.name}`} />}
                {item.error && <small className="attachment-error" role="alert">{item.error}</small>}
              </span>
              <span className="attachment-actions">
                {item.status === "error" && !item.error?.includes("10 MB") && <button type="button" className="secondary-button" onClick={() => void retry(item)}>Retry</button>}
                <button type="button" className="secondary-button" onClick={() => void removeItem(item)} aria-label={`Remove ${item.name}`}>Remove</button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
