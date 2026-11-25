"use client";

import { FormEvent, useEffect, useState } from "react";
import AttachmentDropzone from "./AttachmentDropzone";
import { AttachmentItem, MAX_FILE_BYTES, uploadItems } from "../lib/attachments";

type ReplyComposerProps = {
  ticketId: number;
  headers: Record<string, string>;
  onSent: () => Promise<void> | void;
};

const CANNED_RESPONSES = [
  {
    label: "⚡ Quick Action / Canned macro...",
    text: "",
  },
  {
    label: "📋 Request Diagnostics",
    text: "Hello,\n\nTo assist you further with this issue, could you please provide:\n1. Exact error message or screenshot\n2. Device name / OS version\n3. Steps to reproduce the incident\n\nThank you,\nSupport Team",
  },
  {
    label: "💡 Propose Known Workaround",
    text: "Hello,\n\nA verified workaround is available in our Knowledge Base:\n- Please verify your network connection and flush DNS cache.\n- Restart the application and attempt sign-in.\n\nPlease reply to let us know if this resolves your issue.",
  },
  {
    label: "✅ Ready for Closure",
    text: "Hello,\n\nThe solution for your request has been implemented and verified. Please confirm everything is working smoothly on your end. This ticket will automatically close once confirmed.",
  },
  {
    label: "🔑 Password / Access Reset",
    text: "Hello,\n\nYour temporary access credentials have been reset and dispatched securely. Please log in and set a new password upon your first session.\n\nBest regards,\nSVC Support Team",
  },
];

export default function ReplyComposer({ ticketId, headers, onSent }: ReplyComposerProps) {
  const [followup, setFollowup] = useState("");
  const [items, setItems] = useState<AttachmentItem[]>([]);
  const [existingCount, setExistingCount] = useState(0);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  // Kept after a partial failure so a retry attaches to the same reply instead of creating another.
  const [followupId, setFollowupId] = useState<number | null>(null);

  function applyCanned(text: string) {
    if (!text) return;
    setFollowup((prev) => (prev.trim() ? `${prev}\n\n${text}` : text));
  }

  useEffect(() => {
    setFollowup("");
    setItems([]);
    setError("");
    setFollowupId(null);
    let cancelled = false;
    void fetch(`/api/v1/tickets/${ticketId}/documents`, { headers })
      .then((response) => (response.ok ? response.json() : []))
      .then((documents: unknown) => { if (!cancelled && Array.isArray(documents)) setExistingCount(documents.length); });
    return () => { cancelled = true; };
    // headers is rebuilt on every parent render; the ticket id is the real trigger
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticketId]);

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const unsent = items.filter((item) => item.status !== "done" && item.size <= MAX_FILE_BYTES);
    const text = followup.trim();
    if (followupId === null && !text && !unsent.length) return;
    setSending(true);
    setError("");
    let replyId = followupId;
    if (replyId === null) {
      const response = await fetch(`/api/v1/tickets/${ticketId}/followups`, {
        method: "POST", headers: { "Content-Type": "application/json", ...headers },
        body: JSON.stringify({ body: followup, is_private: false, expects_attachments: !text && unsent.length > 0 }),
      });
      if (!response.ok) {
        setSending(false);
        setError("The reply could not be sent. Your text and files are kept.");
        return;
      }
      replyId = ((await response.json()) as { id: number }).id;
      setFollowup("");
      if (unsent.length) setFollowupId(replyId);
    }
    const failed = unsent.length
      ? await uploadItems(ticketId, unsent.map((item) => item.id), items, setItems, headers, { followup_id: String(replyId) })
      : 0;
    setSending(false);
    if (failed) setError(`${failed} attachment${failed === 1 ? "" : "s"} could not be uploaded. Retry or remove them below.`);
    else {
      setItems([]);
      setFollowupId(null);
    }
    await onSent();
  }

  return (
    <form className="reply-box" onSubmit={send}>
      <div className="reply-type" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span>Public reply</span>
        <select
          className="canned-select"
          style={{ fontSize: "11px", padding: "4px 8px", borderRadius: "5px", border: "1px solid var(--input-border)", background: "var(--input-bg)", cursor: "pointer", color: "var(--input-text)" }}
          value=""
          onChange={(e) => {
            applyCanned(e.target.value);
            e.target.value = "";
          }}
          aria-label="Insert canned response macro"
        >
          {CANNED_RESPONSES.map((macro, idx) => (
            <option key={idx} value={macro.text} disabled={idx === 0}>
              {macro.label}
            </option>
          ))}
        </select>
      </div>
      <textarea
        value={followup}
        onChange={(event) => setFollowup(event.target.value)}
        onKeyDown={(event) => {
          if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
            event.preventDefault();
            event.currentTarget.form?.requestSubmit();
          }
        }}
        placeholder="Write a reply to the requester… (Ctrl+Enter to send)"
      />
      <AttachmentDropzone
        items={items}
        setItems={setItems}
        headers={headers}
        ticketId={ticketId}
        existingCount={existingCount}
        disabled={sending}
        uploadFields={followupId !== null ? { followup_id: String(followupId) } : undefined}
      />
      {error && <p className="error" role="alert">{error}</p>}
      <div className="reply-actions">
        <span>Visible to requester · <b>Ctrl+Enter</b> to send</span>
        <button className="primary-button" type="submit" disabled={sending}>{sending ? "Sending…" : "Send reply"}</button>
      </div>
    </form>
  );
}
