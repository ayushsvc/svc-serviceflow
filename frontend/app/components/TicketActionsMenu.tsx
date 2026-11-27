"use client";

import { useState } from "react";
import { Popover } from "./Popover";
import SLAModal from "./SLAModal";

type ActionTicket = {
  id: number;
  status: string;
  assignee_id?: number | null;
  priority: number;
  ticket_type_id?: number | null;
  allowed_transitions?: string[];
};

type Props = {
  ticket: ActionTicket;
  authorization: string;
  canAssign: boolean;
  canUpdate: boolean;
  staff: boolean;
  priorityOptions: Array<{ value: number; label: string }>;
  ticketTypes: Array<{ id: number; name: string }>;
  onAssign: () => void;
  onSuccess: (message: string) => void | Promise<void>;
  onError: (message: string) => void;
};

const STATUS_LABELS: Record<string, string> = {
  NEW: "New",
  ASSIGNED: "In Progress (Assigned)",
  PLANNED: "Planned / Scheduled",
  PENDING: "On Hold (Awaiting Customer)",
  SOLVED: "Resolved",
  CLOSED: "Closed",
};

function failure(status: number, detail: unknown, what: string) {
  const text = typeof detail === "string" ? detail : "";
  if (status === 403) return `You do not have permission to ${what}.`;
  if (status === 404) return "This ticket is no longer available.";
  if (status === 409) return text || `The workflow does not allow this change.`;
  if (status === 422) return text || "That value is not valid for this ticket.";
  return `Could not ${what}. Please try again.`;
}

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  }
}

export default function TicketActionsMenu({
  ticket,
  authorization,
  canAssign,
  canUpdate,
  staff,
  priorityOptions,
  ticketTypes,
  onAssign,
  onSuccess,
  onError,
}: Props) {
  const [view, setView] = useState<"root" | "priority" | "type" | "status">("root");
  const [busy, setBusy] = useState(false);
  const [breachModalOpen, setBreachModalOpen] = useState(false);
  const [pendingStatus, setPendingStatus] = useState<string>("SOLVED");
  const canUnassign = canAssign && Boolean(ticket.assignee_id) && ticket.status === "ASSIGNED";
  const canChangePriority = staff && canUpdate && priorityOptions.length > 0;
  const canChangeType = staff && canUpdate && ticketTypes.length > 1;
  const canChangeStatus = staff && canUpdate;
  const transitions = ticket.allowed_transitions && ticket.allowed_transitions.length > 0
    ? ticket.allowed_transitions
    : ["ASSIGNED", "PENDING", "PLANNED", "SOLVED", "CLOSED"].filter((s) => s !== ticket.status);

  async function send(url: string, method: string, body: unknown, what: string, success: string) {
    setBusy(true);
    try {
      const response = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json", Authorization: authorization },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        const errorData = (await response.json().catch(() => ({}))) as { detail?: unknown };
        const detailStr = String(errorData.detail || "");
        if (response.status === 400 && detailStr.toLowerCase().includes("breach")) {
          const bodyObj = body as Record<string, unknown> | null;
          setPendingStatus((bodyObj?.status as string) || "SOLVED");
          setBreachModalOpen(true);
          return;
        }
        onError(failure(response.status, errorData.detail, what));
        return;
      }
      await onSuccess(success);
    } catch {
      onError(`Could not ${what}. Check your connection and try again.`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Popover
      wrapClassName="toolbar-menu-wrap ticket-actions-wrap"
      triggerClassName="icon-button ticket-actions-trigger"
      panelClassName="more-popover ticket-actions-menu"
      ariaLabel="Ticket actions"
      focusKey={view}
      label={<span aria-hidden="true">•••</span>}
      onOpenChange={(open) => {
        if (!open) setView("root");
      }}
    >
      {({ close }) =>
        view === "root" ? (
          <>
            {canChangeStatus && transitions.length > 0 && (
              <button type="button" role="menuitem" aria-haspopup="menu" onClick={() => setView("status")}>
                Change status…
              </button>
            )}
            {canAssign && (
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  close();
                  onAssign();
                }}
              >
                {ticket.assignee_id ? "Reassign" : "Assign"}
              </button>
            )}
            {canUnassign && (
              <button
                type="button"
                role="menuitem"
                disabled={busy}
                onClick={() => {
                  close();
                  void send(
                    `/api/v1/tickets/${ticket.id}/assign`,
                    "POST",
                    { assignee_id: null },
                    "unassign this ticket",
                    `Ticket #${ticket.id} unassigned.`,
                  );
                }}
              >
                Unassign
              </button>
            )}
            {canChangePriority && (
              <button type="button" role="menuitem" aria-haspopup="menu" onClick={() => setView("priority")}>
                Change priority…
              </button>
            )}
            {canChangeType && (
              <button type="button" role="menuitem" aria-haspopup="menu" onClick={() => setView("type")}>
                Change ticket type…
              </button>
            )}
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                close();
                const link = `${window.location.origin}${window.location.pathname}?ticket=${ticket.id}`;
                void copyText(link).then((ok) =>
                  ok
                    ? onSuccess("Ticket link copied.")
                    : onError(`Could not copy the link. Copy it manually: ${link}`),
                );
              }}
            >
              Copy ticket link
            </button>
          </>
        ) : (
          <>
            <button type="button" role="menuitem" onClick={() => setView("root")}>
              ← Back
            </button>
            {view === "status" &&
              transitions.map((st) => (
                <button
                  key={st}
                  type="button"
                  role="menuitem"
                  disabled={busy}
                  onClick={() => {
                    close();
                    void send(
                      `/api/v1/tickets/${ticket.id}`,
                      "PATCH",
                      { status: st },
                      "change status",
                      `Status updated to ${STATUS_LABELS[st] ?? st}.`,
                    );
                  }}
                >
                  {STATUS_LABELS[st] ?? st}
                </button>
              ))}
            {view === "priority" &&
              priorityOptions.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  role="menuitem"
                  disabled={busy}
                  aria-current={option.value === ticket.priority ? "true" : undefined}
                  onClick={() => {
                    close();
                    if (option.value !== ticket.priority)
                      void send(
                        `/api/v1/tickets/${ticket.id}`,
                        "PATCH",
                        { priority: option.value },
                        "change the priority",
                        `Priority set to ${option.label}.`,
                      );
                  }}
                >
                  {option.label} ({option.value})
                  {option.value === ticket.priority ? " (Selected)" : ""}
                </button>
              ))}
            {view === "type" &&
              ticketTypes.map((type) => (
                <button
                  key={type.id}
                  type="button"
                  role="menuitem"
                  disabled={busy}
                  aria-current={type.id === ticket.ticket_type_id ? "true" : undefined}
                  onClick={() => {
                    close();
                    if (type.id !== ticket.ticket_type_id)
                      void send(
                        `/api/v1/tickets/${ticket.id}`,
                        "PATCH",
                        { ticket_type_id: type.id },
                        "change the ticket type",
                        `Ticket type set to ${type.name}.`,
                      );
                  }}
                >
                  {type.name}
                  {type.id === ticket.ticket_type_id ? " (Selected)" : ""}
                </button>
              ))}
          </>
        )
      }
    </Popover>
    <SLAModal
      ticketId={ticket.id}
      targetStatus={pendingStatus}
      authorization={authorization}
      isOpen={breachModalOpen}
      onClose={() => setBreachModalOpen(false)}
      onSubmit={async (breachCode, breachReason) => {
        await send(
          `/api/v1/tickets/${ticket.id}`,
          "PATCH",
          { status: pendingStatus, breach_code: breachCode, breach_reason: breachReason },
          `resolve ticket #${ticket.id}`,
          `Ticket #${ticket.id} updated to ${STATUS_LABELS[pendingStatus] ?? pendingStatus} with breach justification.`,
        );
      }}
    />
  </>
  );
}
