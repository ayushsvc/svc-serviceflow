"use client";

import { useCallback, useEffect, useState } from "react";
import { Popover } from "./Popover";

type NotificationItem = { id: string; ticket_id: number; ticket_title: string; kind: "reply" | "internal"; actor_name: string; summary: string; created_at: string };
type Props = { authorization: string; userId: number | undefined; onOpenTicket: (ticketId: number) => void };

const POLL_MS = 60_000;

function ago(value: string) {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 1440) return `${Math.round(minutes / 60)} h ago`;
  return `${Math.round(minutes / 1440)} d ago`;
}

function plain(html: string) {
  return html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
}

export default function NotificationBell({ authorization, userId, onOpenTicket }: Props) {
  const storageKey = userId ? `svc-notifications-seen:${userId}` : null;
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [seenAt, setSeenAt] = useState(0);

  useEffect(() => {
    setSeenAt(storageKey ? Number(window.localStorage.getItem(storageKey) ?? 0) : 0);
  }, [storageKey]);

  const load = useCallback(async () => {
    try {
      const response = await fetch("/api/v1/notifications", { headers: { Authorization: authorization } });
      if (!response.ok) { setState("error"); return; }
      setItems((await response.json()) as NotificationItem[]);
      setState("ready");
    } catch {
      setState("error");
    }
  }, [authorization]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void load(); }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  const unread = items.filter((item) => new Date(item.created_at).getTime() > seenAt).length;
  function markAllRead() {
    const now = Date.now();
    setSeenAt(now);
    if (storageKey) window.localStorage.setItem(storageKey, String(now));
  }

  return (
    <Popover
      wrapClassName="notification-wrap"
      triggerClassName="icon-button notification-trigger"
      panelClassName="notification-panel"
      ariaLabel={unread ? `Notifications, ${unread} unread` : "Notifications"}
      role="dialog"
      onOpenChange={(open) => { if (open) void load(); }}
      label={<><svg aria-hidden="true" viewBox="0 0 24 24" className="icon"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9ZM10 21h4" /></svg>{unread > 0 && <b className="notification-badge" aria-hidden="true">{unread > 9 ? "9+" : unread}</b>}</>}
    >
      {({ close }) => <>
        <div className="notification-head">
          <strong>Notifications</strong>
          <button type="button" className="text-button" disabled={unread === 0} onClick={markAllRead}>Mark all as read</button>
        </div>
        <div className="notification-list" aria-live="polite">
          {state === "loading" && <p className="muted">Loading…</p>}
          {state === "error" && <p className="form-error" role="alert">Notifications could not be loaded. <button type="button" className="text-button" onClick={() => void load()}>Try again</button></p>}
          {state === "ready" && items.length === 0 && <p className="muted">You are all caught up. Replies on your tickets will appear here.</p>}
          {items.map((item) => {
            const isNew = new Date(item.created_at).getTime() > seenAt;
            return <button
              key={item.id}
              type="button"
              className={`notification-item ${isNew ? "notification-new" : ""}`}
              onClick={() => { close(); onOpenTicket(item.ticket_id); }}
            >
              <span className="notification-title">{item.actor_name} {item.kind === "internal" ? "added an internal update on" : "replied on"} #{item.ticket_id}</span>
              <span className="notification-body">{plain(item.summary)}</span>
              <span className="notification-meta">{item.ticket_title} · {ago(item.created_at)}</span>
            </button>;
          })}
        </div>
      </>}
    </Popover>
  );
}
