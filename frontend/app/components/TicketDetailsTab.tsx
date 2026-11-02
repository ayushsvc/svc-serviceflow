"use client";

import { ReactNode, useEffect, useState } from "react";
import { SafeHtml } from "./SafeHtml";

type FormField = { key: string; label: string; type: string; page: string };
type FormSchema = { pages: Record<string, FormField[]> };
export type DetailTicket = {
  id: number;
  title: string;
  description: string;
  status: string;
  status_label?: string | null;
  priority: number;
  priority_label?: string | null;
  urgency?: number;
  impact?: number;
  source?: string;
  opened_at: string;
  solved_at?: string | null;
  closed_at?: string | null;
  tto_due_at?: string | null;
  ttr_due_at?: string | null;
  category_id?: number | null;
  ticket_type_id?: number | null;
  requester_name?: string | null;
  assignee_id?: number | null;
  assignee_name?: string | null;
  team_name?: string | null;
  flagged?: boolean | null;
  custom_fields?: Record<string, unknown>;
};
type Props = {
  ticket: DetailTicket;
  authorization: string;
  staff: boolean;
  categories: Array<{ id: number; name: string }>;
  ticketTypes: Array<{ id: number; name: string }>;
  entityName: string;
};

const SKIP_KEYS = new Set(["attachments"]);
const EMPTY = "—";

function when(value?: string | null) {
  if (!value) return EMPTY;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? EMPTY : date.toLocaleString();
}

function slaState(due?: string | null, doneAt?: string | null) {
  if (!due) return "Not set";
  const dueTime = new Date(due).getTime();
  if (doneAt) return new Date(doneAt).getTime() <= dueTime ? "Met" : "Breached";
  return Date.now() > dueTime ? "Breached" : "Running";
}

function pageTitle(page: string) {
  return page.charAt(0).toUpperCase() + page.slice(1).replace(/[_-]/g, " ");
}

export default function TicketDetailsTab({ ticket, authorization, staff, categories, ticketTypes, entityName }: Props) {
  const [schema, setSchema] = useState<FormSchema | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error" | "unavailable">("loading");
  const [attempt, setAttempt] = useState(0);
  const typeId = ticket.ticket_type_id ?? null;

  useEffect(() => {
    if (typeId === null) { setSchema(null); setState("unavailable"); return; }
    let cancelled = false;
    setState("loading");
    void fetch(`/api/v1/ticket-types/${typeId}/form`, { headers: { Authorization: authorization } })
      .then(async (response) => {
        if (cancelled) return;
        if (response.ok) { setSchema((await response.json()) as FormSchema); setState("ready"); }
        else if (response.status === 404 || response.status === 403) { setSchema(null); setState("unavailable"); }
        else setState("error");
      })
      .catch(() => { if (!cancelled) setState("error"); });
    return () => { cancelled = true; };
  }, [typeId, authorization, attempt]);

  const custom = ticket.custom_fields ?? {};
  const typeName = ticketTypes.find((item) => item.id === typeId)?.name ?? EMPTY;
  const priorityText = `${ticket.priority_label ?? `Priority ${ticket.priority}`}${staff ? ` (${ticket.priority})` : ""}`;

  function resolve(field: FormField): ReactNode {
    switch (field.key) {
      case "summary": return ticket.title;
      case "details": return <SafeHtml className="ticket-description" html={ticket.description} />;
      case "user": return ticket.requester_name ?? EMPTY;
      case "organisation": return entityName || EMPTY;
      case "category": return categories.find((item) => item.id === ticket.category_id)?.name ?? (ticket.category_id ? `Category ${ticket.category_id}` : "Uncategorized");
      case "urgency": return ticket.urgency ?? EMPTY;
      case "impact": return ticket.impact ?? EMPTY;
      case "priority": return priorityText;
      case "team": return ticket.team_name || "Unassigned";
      case "agent": return ticket.assignee_name || (ticket.assignee_id ? `User #${ticket.assignee_id}` : "Unassigned");
      case "source": return ticket.source ?? EMPTY;
      case "flag": return ticket.flagged ? "Flagged" : "Not flagged";
      case "sla": return staff ? slaState(ticket.ttr_due_at, ticket.solved_at ?? ticket.closed_at) : EMPTY;
      default: {
        const value = custom[field.key];
        if (value === undefined || value === null || value === "") return EMPTY;
        if (field.type === "datetime" && typeof value === "string") return when(value);
        if (Array.isArray(value)) return value.length ? value.join(", ") : EMPTY;
        if (typeof value === "boolean") return value ? "Yes" : "No";
        return String(value);
      }
    }
  }

  const schemaKeys = new Set(Object.values(schema?.pages ?? {}).flat().map((field) => field.key));
  const extraCustom = staff ? Object.entries(custom).filter(([key, value]) => !schemaKeys.has(key) && !SKIP_KEYS.has(key) && value !== null && value !== "" && typeof value !== "object") : [];

  return (
    <div className="details-tab" aria-busy={state === "loading"}>
      <section className="drawer-section">
        <h3>Ticket</h3>
        <dl className="details-grid">
          <div><dt>Ticket ID</dt><dd>#{ticket.id}</dd></div>
          <div><dt>Type</dt><dd>{typeName}</dd></div>
          <div><dt>Status</dt><dd>{ticket.status_label ?? ticket.status}</dd></div>
          <div><dt>Priority</dt><dd>{priorityText}</dd></div>
          <div><dt>Created</dt><dd>{when(ticket.opened_at)}</dd></div>
          {ticket.solved_at && <div><dt>Resolved</dt><dd>{when(ticket.solved_at)}</dd></div>}
          {ticket.closed_at && <div><dt>Closed</dt><dd>{when(ticket.closed_at)}</dd></div>}
        </dl>
      </section>
      {staff && <section className="drawer-section">
        <h3>Service levels</h3>
        <dl className="details-grid">
          <div><dt>Response target</dt><dd>{when(ticket.tto_due_at)} <span className={`sla-state sla-state-${slaState(ticket.tto_due_at, ticket.solved_at ?? ticket.closed_at).toLowerCase()}`}>{slaState(ticket.tto_due_at, ticket.solved_at ?? ticket.closed_at)}</span></dd></div>
          <div><dt>Fix target</dt><dd>{when(ticket.ttr_due_at)} <span className={`sla-state sla-state-${slaState(ticket.ttr_due_at, ticket.solved_at ?? ticket.closed_at).toLowerCase()}`}>{slaState(ticket.ttr_due_at, ticket.solved_at ?? ticket.closed_at)}</span></dd></div>
        </dl>
      </section>}
      {state === "loading" && <p className="muted" role="status">Loading ticket fields…</p>}
      {state === "error" && <div role="alert" className="form-error"><p>The ticket fields could not be loaded.</p><button type="button" className="secondary-button" onClick={() => setAttempt((value) => value + 1)}>Try again</button></div>}
      {state === "unavailable" && <p className="muted">No additional fields are available for this ticket type.</p>}
      {state === "ready" && schema && Object.entries(schema.pages).map(([page, fields]) => {
        const visible = fields.filter((field) => !SKIP_KEYS.has(field.key));
        if (!visible.length) return null;
        return <section className="drawer-section" key={page}>
          <h3>{pageTitle(page)}</h3>
          <dl className="details-grid">
            {visible.map((field) => <div key={field.key} className={field.type === "richtext" || field.key === "details" ? "details-wide" : undefined}><dt>{field.label}</dt><dd>{resolve(field)}</dd></div>)}
          </dl>
        </section>;
      })}
      {state === "ready" && schema && Object.values(schema.pages).flat().length === 0 && <p className="muted">This ticket type has no fields to show.</p>}
      {extraCustom.length > 0 && <section className="drawer-section">
        <h3>Custom fields</h3>
        <dl className="details-grid">{extraCustom.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>)}</dl>
      </section>}
    </div>
  );
}
