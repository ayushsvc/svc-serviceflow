"use client";

import { FormEvent, useEffect, useState } from "react";
import AttachmentDropzone from "./AttachmentDropzone";
import { AttachmentItem, uploadItems } from "../lib/attachments";

type ReplyComposerProps = {
  ticketId: number;
  headers: Record<string, string>;
  onSent: () => Promise<void> | void;
};

export default function ReplyComposer({ ticketId, headers, onSent }: ReplyComposerProps) {
  const [followup, setFollowup] = useState("");
  const [items, setItems] = useState<AttachmentItem[]>([]);
  const [existingCount, setExistingCount] = useState(0);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);

  useEffect(() => {
    setFollowup("");
    setItems([]);
    setError("");
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
    const unsent = items.filter((item) => item.status !== "done" && item.size <= 10 * 1024 * 1024);
    if (!followup.trim() && !unsent.length) return;
    setSending(true);
    setError("");
    if (followup.trim()) {
      const response = await fetch(`/api/v1/tickets/${ticketId}/followups`, {
        method: "POST", headers: { "Content-Type": "application/json", ...headers },
        body: JSON.stringify({ body: followup, is_private: false }),
      });
      if (!response.ok) {
        setSending(false);
        setError("The reply could not be sent. Your text and files are kept.");
        return;
      }
      setFollowup("");
    }
    const failed = await uploadItems(ticketId, unsent.map((item) => item.id), items, setItems, headers);
    setSending(false);
    if (failed) setError(`${failed} attachment${failed === 1 ? "" : "s"} could not be uploaded. Retry or remove them below.`);
    else setItems([]);
    await onSent();
  }

  return (
    <form className="reply-box" onSubmit={send}>
      <div className="reply-type"><span>Public reply</span></div>
      <textarea value={followup} onChange={(event) => setFollowup(event.target.value)} placeholder="Write a reply to the requester…" />
      <AttachmentDropzone items={items} setItems={setItems} headers={headers} ticketId={ticketId} existingCount={existingCount} disabled={sending} />
      {error && <p className="error" role="alert">{error}</p>}
      <div className="reply-actions"><span>Visible to requester</span><button className="primary-button" type="submit" disabled={sending}>{sending ? "Sending…" : "Send reply"}</button></div>
    </form>
  );
}
