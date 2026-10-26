"use client";

import { KeyboardEvent, useEffect, useRef, useState } from "react";

type TokenPair = { access_token: string };
type Team = { id: number; name: string };
type AssignableUser = {
  id: number;
  name: string;
  email: string;
  teams: string[];
  open_ticket_count: number;
};
type AssignedTicket = {
  id: number;
  assignee_id: number | null;
  assignee_name?: string | null;
  team_id?: number | null;
  team_name?: string | null;
  status: string;
};

type AssignPickerProps = {
  ticketId: number;
  token: TokenPair;
  currentUserId: number;
  currentAssigneeId: number | null;
  currentAssigneeName?: string | null;
  currentTeamId?: number | null;
  currentTeamName?: string | null;
  teams: Team[];
  onClose: () => void;
  onAssigned: (ticket: Partial<AssignedTicket> & Pick<AssignedTicket, "id">) => void;
};

function responseMessage(status: number, detail?: string): string {
  if (status === 403) return "You do not have permission to assign tickets.";
  if (status === 404) return "This ticket is no longer available.";
  if (status === 409) return detail === "Ticket is already assigned"
    ? "The ticket was assigned by someone else. Refresh and try again."
    : "The workflow does not allow this assignment change.";
  if (status === 422) return detail === "assignee_cannot_view_ticket"
    ? "That technician cannot see tickets routed to this team."
    : "Choose a valid technician and team.";
  return "Assignment failed. Please try again.";
}

export default function AssignPicker({
  ticketId,
  token,
  currentUserId,
  currentAssigneeId,
  currentAssigneeName,
  currentTeamId,
  currentTeamName,
  teams,
  onClose,
  onAssigned,
}: AssignPickerProps) {
  const [search, setSearch] = useState("");
  const [teamId, setTeamId] = useState(currentTeamId ? String(currentTeamId) : "");
  const [users, setUsers] = useState<AssignableUser[]>([]);
  const [selectedId, setSelectedId] = useState(currentAssigneeId ? String(currentAssigneeId) : "");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null;
    searchRef.current?.focus();
    return () => previousFocus?.focus();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setLoading(true);
      const params = new URLSearchParams({ ticket_id: String(ticketId), limit: "50" });
      if (teamId && teamId !== "none") params.set("team_id", teamId);
      if (search.trim()) params.set("q", search.trim());
      try {
        const response = await fetch(`/api/v1/users/assignable?${params}`, {
          headers: { Authorization: `Bearer ${token.access_token}` },
          signal: controller.signal,
        });
        if (!response.ok) {
          setError(responseMessage(response.status));
          setUsers([]);
          return;
        }
        const page = await response.json() as { items: AssignableUser[] };
        setUsers(page.items);
        setError("");
      } catch (cause) {
        if (!(cause instanceof DOMException && cause.name === "AbortError")) {
          setError("Unable to load assignable technicians.");
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, 180);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [search, teamId, ticketId, token.access_token]);

  function trapFocus(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      const options = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('[role="option"]') ?? []);
      const activeIndex = options.indexOf(document.activeElement as HTMLElement);
      if (activeIndex >= 0 && options.length > 0) {
        event.preventDefault();
        const offset = event.key === "ArrowDown" ? 1 : -1;
        options[(activeIndex + offset + options.length) % options.length]?.focus();
      }
      return;
    }
    if (event.key !== "Tab" || !dialogRef.current) return;
    const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ));
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  }

  async function save(assigneeId: number | null) {
    setSaving(true);
    setError("");
    const body: { assignee_id: number | null; team_id?: number | null } = { assignee_id: assigneeId };
    if (teamId) body.team_id = teamId === "none" ? null : Number(teamId);
    try {
      const response = await fetch(`/api/v1/tickets/${ticketId}/assign`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token.access_token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
      });
      const result = await response.json().catch(() => ({})) as {
        ticket?: AssignedTicket;
        detail?: string;
      };
      if (!response.ok || !result.ticket) {
        setError(responseMessage(response.status, typeof result.detail === "string" ? result.detail : undefined));
        return;
      }
      onAssigned(result.ticket);
      onClose();
    } catch {
      setError("Unable to save the assignment. Check your connection and try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div
        className="create-modal"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="assign-picker-title"
        onKeyDown={trapFocus}
      >
        <div className="modal-heading">
          <div>
            <span className="kicker">TICKET #{ticketId}</span>
            <h2 id="assign-picker-title">Assign ticket</h2>
          </div>
          <button type="button" className="icon-button" aria-label="Close assignment picker" onClick={onClose}>×</button>
        </div>
        <p className="subheading">
          Current assignee: {currentAssigneeName || (currentAssigneeId ? `User #${currentAssigneeId}` : "Unassigned")}
          {" · "}
          Team: {currentTeamName || "Unassigned"}
        </p>
        <label className="assign-picker-field">
          Search technicians
          <input ref={searchRef} type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Name or email" />
        </label>
        <label className="assign-picker-field">
          Team filter / route
          <select aria-label="Filter by team" value={teamId} onChange={(event) => setTeamId(event.target.value)}>
            <option value="">Keep current route</option>
            <option value="none">No team / unroute</option>
            {teams.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}
          </select>
        </label>
        <div className="assignable-users" role="listbox" aria-label="Assignable technicians" aria-busy={loading}>
          {users.map((user) => (
            <button
              type="button"
              role="option"
              aria-selected={selectedId === String(user.id)}
              className={`assignable-user ${selectedId === String(user.id) ? "selected" : ""}`}
              key={user.id}
              onClick={() => setSelectedId(String(user.id))}
            >
              <span><strong>{user.name}</strong><small>{user.email} · {user.teams.join(", ") || "No team"}</small></span>
              <span className="workload-count" aria-label={`${user.open_ticket_count} open tickets`}>{user.open_ticket_count} open</span>
            </button>
          ))}
          {!loading && users.length === 0 && <p className="assign-picker-empty">No assignable technicians found.</p>}
        </div>
        {error && <p className="error" role="alert">{error}</p>}
        <div className="assign-picker-actions">
          <button type="button" className="secondary-button" disabled={saving || currentAssigneeId === null} onClick={() => void save(null)}>Unassign</button>
          <button type="button" className="secondary-button" disabled={saving || !users.some((candidate) => candidate.id === Number(selectedId))} onClick={() => void save(Number(selectedId))}>Assign</button>
          <button type="button" className="primary-button" disabled={saving || !users.some((candidate) => candidate.id === currentUserId)} onClick={() => void save(currentUserId)}>Assign to me</button>
          <button type="button" className="secondary-button" disabled={saving} onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
