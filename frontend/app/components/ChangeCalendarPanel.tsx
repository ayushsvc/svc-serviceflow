"use client";

import { useEffect, useMemo, useState } from "react";

export type CalendarTicket = {
  id: number;
  title: string;
  description: string;
  status: string;
  priority: number;
  opened_at: string;
  ticket_type_id?: number | null;
  assignee_name?: string | null;
  ttr_due_at?: string | null;
  custom_fields?: Record<string, any>;
};

type Props = {
  tickets: CalendarTicket[];
  onSelectTicket: (ticket: CalendarTicket) => void | Promise<void>;
  authFetch?: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
  onRefreshTickets?: () => Promise<void>;
};

function getChangeDate(ticket: CalendarTicket): Date {
  const cf = ticket.custom_fields;
  if (cf?.start_date) {
    const d = new Date(cf.start_date);
    if (!Number.isNaN(d.getTime())) return d;
  }
  if (ticket.ttr_due_at) {
    const d = new Date(ticket.ttr_due_at);
    if (!Number.isNaN(d.getTime())) return d;
  }
  return new Date(ticket.opened_at);
}

export default function ChangeCalendarPanel({ tickets, onSelectTicket, authFetch, onRefreshTickets }: Props) {
  const [currentDate, setCurrentDate] = useState(() => new Date(2026, 10, 25)); // Nov 2026 current month
  const [fetchedChanges, setFetchedChanges] = useState<CalendarTicket[]>([]);
  const [loading, setLoading] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string>("ALL");

  // Schedule modal state
  const [showScheduleModal, setShowScheduleModal] = useState(false);
  const [scheduleDate, setScheduleDate] = useState("2026-11-26");
  const [scheduleTitle, setScheduleTitle] = useState("");
  const [scheduleDescription, setScheduleDescription] = useState("");
  const [schedulePriority, setSchedulePriority] = useState(3);
  const [scheduleStartTime, setScheduleStartTime] = useState("02:00");
  const [scheduleEndTime, setScheduleEndTime] = useState("06:00");
  const [scheduleSubmitting, setScheduleSubmitting] = useState(false);
  const [scheduleError, setScheduleError] = useState<string | null>(null);

  const year = currentDate.getFullYear();
  const month = currentDate.getMonth();

  const monthNames = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];

  // Fetch change tickets directly from backend
  async function loadChanges() {
    if (!authFetch) return;
    setLoading(true);
    try {
      const res = await authFetch("/api/v1/tickets?ticket_type_id=4&page_size=100");
      if (res.ok) {
        const data = await res.json();
        setFetchedChanges(Array.isArray(data.items) ? data.items : []);
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadChanges();
  }, [authFetch]);

  // Combine fetched change tickets with passed tickets
  const changeTickets = useMemo(() => {
    const map = new Map<number, CalendarTicket>();
    // First add passed tickets matching change
    tickets.forEach((t) => {
      if (t.ticket_type_id === 4 || t.title.toLowerCase().includes("change") || t.title.toLowerCase().includes("rfc")) {
        map.set(t.id, t);
      }
    });
    // Add explicitly fetched changes
    fetchedChanges.forEach((t) => {
      map.set(t.id, t);
    });
    return Array.from(map.values());
  }, [tickets, fetchedChanges]);

  // Filtered change tickets
  const filteredChanges = useMemo(() => {
    if (statusFilter === "ALL") return changeTickets;
    return changeTickets.filter((t) => t.status === statusFilter);
  }, [changeTickets, statusFilter]);

  // Calendar math
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const firstDayIndex = new Date(year, month, 1).getDay();

  function prevMonth() {
    setCurrentDate(new Date(year, month - 1, 1));
  }

  function nextMonth() {
    setCurrentDate(new Date(year, month + 1, 1));
  }

  function goToToday() {
    setCurrentDate(new Date());
  }

  const today = new Date();
  const isCurrentMonth = today.getFullYear() === year && today.getMonth() === month;

  function handleOpenSchedule(dayNum?: number) {
    if (dayNum) {
      const mStr = String(month + 1).padStart(2, "0");
      const dStr = String(dayNum).padStart(2, "0");
      setScheduleDate(`${year}-${mStr}-${dStr}`);
    } else {
      const todayY = today.getFullYear();
      const todayM = String(today.getMonth() + 1).padStart(2, "0");
      const todayD = String(today.getDate()).padStart(2, "0");
      setScheduleDate(`${todayY}-${todayM}-${todayD}`);
    }
    setScheduleError(null);
    setShowScheduleModal(true);
  }

  async function handleScheduleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!scheduleTitle.trim()) {
      setScheduleError("Please enter a change title / RFC summary.");
      return;
    }
    if (!authFetch) {
      setScheduleError("Authentication not available.");
      return;
    }

    setScheduleSubmitting(true);
    setScheduleError(null);

    try {
      const startIso = `${scheduleDate}T${scheduleStartTime}:00`;
      const endIso = `${scheduleDate}T${scheduleEndTime}:00`;

      const res = await authFetch("/api/v1/tickets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: scheduleTitle.trim(),
          description: scheduleDescription.trim() || `Scheduled change window from ${scheduleStartTime} to ${scheduleEndTime}.`,
          type: "CHANGE",
          ticket_type_id: 4,
          urgency: schedulePriority,
          impact: schedulePriority,
          custom_fields: {
            start_date: startIso,
            due_date: endIso,
          },
        }),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || "Failed to schedule change request.");
      }

      setShowScheduleModal(false);
      setScheduleTitle("");
      setScheduleDescription("");
      await loadChanges();
      if (onRefreshTickets) void onRefreshTickets();
    } catch (err: unknown) {
      setScheduleError(err instanceof Error ? err.message : "Error scheduling change.");
    } finally {
      setScheduleSubmitting(false);
    }
  }

  function getPillStyle(t: CalendarTicket) {
    if (t.priority === 1) {
      return {
        bg: "var(--danger-soft)",
        color: "var(--danger)",
        border: "1px solid var(--danger-border)",
      };
    }
    if (t.status === "SOLVED" || t.status === "CLOSED") {
      return {
        bg: "var(--success-soft)",
        color: "var(--success)",
        border: "1px solid var(--success-border)",
      };
    }
    if (t.status === "PENDING") {
      return {
        bg: "var(--warning-soft)",
        color: "var(--warning)",
        border: "1px solid var(--warning-border)",
      };
    }
    return {
      bg: "var(--accent-soft)",
      color: "var(--accent)",
      border: "1px solid var(--accent)",
    };
  }

  return (
    <section className="section-panel" style={{ paddingBottom: 40 }}>
      {/* Page Heading */}
      <div className="page-heading">
        <div>
          <p className="kicker">CHANGE ENABLEMENT</p>
          <h1>Change Calendar</h1>
          <p className="subheading">
            Schedule, coordinate, and review planned infrastructure and system modifications across your enterprise.
          </p>
        </div>
        <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
          <button type="button" className="secondary-button" onClick={prevMonth} title="Previous month">
            ← Previous
          </button>
          <strong style={{ fontSize: "14px", minWidth: 150, textAlign: "center", color: "var(--ink)" }}>
            {monthNames[month]} {year}
          </strong>
          <button type="button" className="secondary-button" onClick={nextMonth} title="Next month">
            Next →
          </button>
          <button type="button" className="secondary-button" onClick={goToToday} title="Go to current date">
            Today
          </button>
          <button
            type="button"
            className="primary-button"
            onClick={() => handleOpenSchedule()}
            style={{ marginLeft: "8px" }}
          >
            <span>+</span> Schedule Change
          </button>
        </div>
      </div>

      {/* Metric Cards Row */}
      <div className="metric-row">
        <div className="metric">
          <span className="metric-label">Total Changes</span>
          <strong>{changeTickets.length}</strong>
          <small>Tracked changes</small>
        </div>
        <div className="metric">
          <span className="metric-label">In Progress / Planned</span>
          <strong style={{ color: "var(--accent)" }}>
            {changeTickets.filter((t) => t.status === "ASSIGNED" || t.status === "PLANNED").length}
          </strong>
          <small>Executing or scheduled</small>
        </div>
        <div className="metric">
          <span className="metric-label">Awaiting CAB Approval</span>
          <strong style={{ color: "var(--warning)" }}>
            {changeTickets.filter((t) => t.status === "PENDING" || t.status === "NEW").length}
          </strong>
          <small>Pending review</small>
        </div>
        <div className="metric">
          <span className="metric-label">Completed</span>
          <strong style={{ color: "var(--success)" }}>
            {changeTickets.filter((t) => t.status === "SOLVED" || t.status === "CLOSED").length}
          </strong>
          <small>Successfully deployed</small>
        </div>
      </div>

      {/* Calendar Grid Container */}
      <div
        className="table-card"
        style={{
          padding: 18,
          backgroundColor: "var(--surface)",
          borderRadius: 8,
          border: "1px solid var(--line)",
          boxShadow: "var(--shadow-sm)",
          marginBottom: 24,
        }}
      >
        {/* Days of week header */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(7, 1fr)",
            gap: 8,
            textAlign: "center",
            fontWeight: 600,
            color: "var(--muted)",
            fontSize: "11px",
            textTransform: "uppercase",
            letterSpacing: ".05em",
            paddingBottom: 10,
            borderBottom: "1px solid var(--line)",
          }}
        >
          <span>Sun</span>
          <span>Mon</span>
          <span>Tue</span>
          <span>Wed</span>
          <span>Thu</span>
          <span>Fri</span>
          <span>Sat</span>
        </div>

        {/* Calendar days cells */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 6, marginTop: 10 }}>
          {/* Empty cells before month start */}
          {Array.from({ length: firstDayIndex }).map((_, i) => (
            <div
              key={`empty-${i}`}
              style={{
                minHeight: 100,
                backgroundColor: "var(--canvas)",
                borderRadius: 6,
                border: "1px solid var(--line)",
                opacity: 0.35,
              }}
            />
          ))}

          {/* Days of month */}
          {Array.from({ length: daysInMonth }).map((_, i) => {
            const dayNum = i + 1;
            const isToday = isCurrentMonth && today.getDate() === dayNum;

            // Find changes on this date (using custom_fields.start_date, ttr_due_at, or opened_at)
            const dayChanges = filteredChanges.filter((t) => {
              const d = getChangeDate(t);
              return d.getFullYear() === year && d.getMonth() === month && d.getDate() === dayNum;
            });

            return (
              <div
                key={`day-${dayNum}`}
                onClick={(e) => {
                  // Only open schedule if clicked the day cell directly, not a change ticket button
                  if (e.target === e.currentTarget) {
                    handleOpenSchedule(dayNum);
                  }
                }}
                style={{
                  minHeight: 100,
                  padding: "6px 8px",
                  borderRadius: 6,
                  border: isToday ? "2px solid var(--accent)" : "1px solid var(--line)",
                  backgroundColor: isToday ? "var(--accent-soft)" : "var(--surface)",
                  display: "flex",
                  flexDirection: "column",
                  cursor: "pointer",
                  transition: "background-color 0.15s ease, border-color 0.15s ease",
                  position: "relative",
                }}
                title={`Click to schedule a Change on ${monthNames[month]} ${dayNum}, ${year}`}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6, pointerEvents: "none" }}>
                  <span
                    style={{
                      fontWeight: isToday ? 700 : 600,
                      fontSize: "12px",
                      color: isToday ? "var(--accent)" : "var(--ink)",
                    }}
                  >
                    {dayNum}
                  </span>
                  <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                    {isToday && (
                      <span
                        style={{
                          fontSize: "9px",
                          fontWeight: 700,
                          backgroundColor: "var(--accent)",
                          color: "#ffffff",
                          padding: "1px 5px",
                          borderRadius: 4,
                          textTransform: "uppercase",
                        }}
                      >
                        Today
                      </span>
                    )}
                    <span style={{ fontSize: "11px", color: "var(--muted)", opacity: 0.6 }}>+</span>
                  </div>
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, overflowY: "auto" }}>
                  {dayChanges.map((t) => {
                    const pill = getPillStyle(t);
                    return (
                      <button
                        key={t.id}
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          void onSelectTicket(t);
                        }}
                        style={{
                          textAlign: "left",
                          padding: "3px 6px",
                          borderRadius: 4,
                          fontSize: "10px",
                          backgroundColor: pill.bg,
                          color: pill.color,
                          border: pill.border,
                          cursor: "pointer",
                          whiteSpace: "nowrap",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          fontWeight: 600,
                          display: "block",
                          width: "100%",
                        }}
                        title={`#${t.id}: ${t.title} (${t.status})`}
                      >
                        #{t.id} {t.title}
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Active & Scheduled Changes Table */}
      <div className="table-card" style={{ padding: 20, backgroundColor: "var(--surface)", border: "1px solid var(--line)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
          <h2 style={{ fontSize: "16px", fontWeight: 700, margin: 0, color: "var(--ink)" }}>
            Active &amp; Scheduled Changes ({changeTickets.length})
          </h2>
          <div style={{ display: "flex", gap: "6px" }}>
            {[
              { id: "ALL", label: "All" },
              { id: "PLANNED", label: "Planned" },
              { id: "PENDING", label: "Awaiting CAB" },
              { id: "ASSIGNED", label: "In Progress" },
              { id: "SOLVED", label: "Completed" },
            ].map((f) => (
              <button
                key={f.id}
                type="button"
                className={`secondary-button ${statusFilter === f.id ? "button-active" : ""}`}
                style={{ padding: "4px 10px", fontSize: "11px" }}
                onClick={() => setStatusFilter(f.id)}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        {filteredChanges.length === 0 ? (
          <p className="muted" style={{ fontSize: "12px" }}>
            No Change tickets match the selected filter. Click &ldquo;+ Schedule Change&rdquo; or any day on the calendar to schedule an RFC.
          </p>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12px" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--line)", textAlign: "left", color: "var(--muted)" }}>
                <th style={{ padding: "10px 12px" }}>RFC ID</th>
                <th style={{ padding: "10px 12px" }}>Change Title</th>
                <th style={{ padding: "10px 12px" }}>Status</th>
                <th style={{ padding: "10px 12px" }}>Priority</th>
                <th style={{ padding: "10px 12px" }}>Scheduled Date</th>
                <th style={{ padding: "10px 12px" }}>Assignee</th>
                <th style={{ padding: "10px 12px", textAlign: "right" }}>Action</th>
              </tr>
            </thead>
            <tbody>
              {filteredChanges.map((t) => {
                const changeDate = getChangeDate(t);
                return (
                  <tr key={t.id} style={{ borderBottom: "1px solid var(--table-border)" }}>
                    <td style={{ padding: "10px 12px", fontWeight: 600, color: "var(--ink)" }}>#{t.id}</td>
                    <td style={{ padding: "10px 12px", color: "var(--ink)", maxWidth: 320 }}>
                      <strong style={{ display: "block" }}>{t.title}</strong>
                      <span className="muted" style={{ fontSize: "11px" }}>{t.description?.slice(0, 80)}…</span>
                    </td>
                    <td style={{ padding: "10px 12px" }}>
                      <span className={`status status-${t.status.toLowerCase()}`}>
                        <i />
                        {t.status}
                      </span>
                    </td>
                    <td style={{ padding: "10px 12px" }}>
                      <span className={`priority priority-${t.priority}`}>Priority {t.priority}</span>
                    </td>
                    <td style={{ padding: "10px 12px", color: "var(--ink)" }}>
                      {changeDate.toLocaleDateString()} {changeDate.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                    </td>
                    <td style={{ padding: "10px 12px", color: "var(--muted)" }}>{t.assignee_name || "Unassigned"}</td>
                    <td style={{ padding: "10px 12px", textAlign: "right" }}>
                      <button
                        type="button"
                        className="secondary-button"
                        style={{ padding: "4px 10px", fontSize: "11px" }}
                        onClick={() => void onSelectTicket(t)}
                      >
                        View RFC
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Schedule Change Request (RFC) Modal */}
      {showScheduleModal && (
        <div
          role="dialog"
          aria-modal="true"
          className="sla-modal-backdrop"
          onClick={(e) => {
            if (e.target === e.currentTarget && !scheduleSubmitting) setShowScheduleModal(false);
          }}
        >
          <div className="create-modal" style={{ maxWidth: 600 }}>
            <div className="modal-header">
              <div>
                <p className="kicker">CHANGE ENABLEMENT</p>
                <h2>Schedule Change Request (RFC)</h2>
                <p className="subheading">
                  Plan, schedule, and review change deployment windows across IT infrastructure.
                </p>
              </div>
              <button
                type="button"
                className="icon-button"
                onClick={() => setShowScheduleModal(false)}
                disabled={scheduleSubmitting}
                aria-label="Close"
              >
                <svg aria-hidden="true" viewBox="0 0 24 24" className="icon"><path d="m6 6 12 12M18 6 6 18" /></svg>
              </button>
            </div>

            <form onSubmit={handleScheduleSubmit}>
              {scheduleError && (
                <div className="error" style={{ marginBottom: 16 }}>
                  {scheduleError}
                </div>
              )}

              <div style={{ marginBottom: 14 }}>
                <label style={{ display: "block", fontSize: "11px", fontWeight: 600, marginBottom: 6, color: "var(--text-label)" }}>
                  Change Title / Summary <span style={{ color: "var(--danger)" }}>*</span>
                </label>
                <input
                  type="text"
                  placeholder="e.g. Core Switch Firmware Upgrade v4.2"
                  value={scheduleTitle}
                  onChange={(e) => setScheduleTitle(e.target.value)}
                  required
                  style={{
                    width: "100%",
                    padding: "10px 12px",
                    borderRadius: 6,
                    border: "1px solid var(--input-border)",
                    backgroundColor: "var(--input-bg)",
                    color: "var(--input-text)",
                    fontSize: "12px",
                  }}
                />
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12, marginBottom: 14 }}>
                <div>
                  <label style={{ display: "block", fontSize: "11px", fontWeight: 600, marginBottom: 6, color: "var(--text-label)" }}>
                    Scheduled Date
                  </label>
                  <input
                    type="date"
                    value={scheduleDate}
                    onChange={(e) => setScheduleDate(e.target.value)}
                    required
                    style={{
                      width: "100%",
                      padding: "8px 10px",
                      borderRadius: 6,
                      border: "1px solid var(--input-border)",
                      backgroundColor: "var(--input-bg)",
                      color: "var(--input-text)",
                      fontSize: "12px",
                    }}
                  />
                </div>
                <div>
                  <label style={{ display: "block", fontSize: "11px", fontWeight: 600, marginBottom: 6, color: "var(--text-label)" }}>
                    Window Start Time
                  </label>
                  <input
                    type="time"
                    value={scheduleStartTime}
                    onChange={(e) => setScheduleStartTime(e.target.value)}
                    required
                    style={{
                      width: "100%",
                      padding: "8px 10px",
                      borderRadius: 6,
                      border: "1px solid var(--input-border)",
                      backgroundColor: "var(--input-bg)",
                      color: "var(--input-text)",
                      fontSize: "12px",
                    }}
                  />
                </div>
                <div>
                  <label style={{ display: "block", fontSize: "11px", fontWeight: 600, marginBottom: 6, color: "var(--text-label)" }}>
                    Window End Time
                  </label>
                  <input
                    type="time"
                    value={scheduleEndTime}
                    onChange={(e) => setScheduleEndTime(e.target.value)}
                    required
                    style={{
                      width: "100%",
                      padding: "8px 10px",
                      borderRadius: 6,
                      border: "1px solid var(--input-border)",
                      backgroundColor: "var(--input-bg)",
                      color: "var(--input-text)",
                      fontSize: "12px",
                    }}
                  />
                </div>
              </div>

              <div style={{ marginBottom: 14 }}>
                <label style={{ display: "block", fontSize: "11px", fontWeight: 600, marginBottom: 6, color: "var(--text-label)" }}>
                  Impact &amp; Urgency Level
                </label>
                <select
                  value={schedulePriority}
                  onChange={(e) => setSchedulePriority(Number(e.target.value))}
                  style={{
                    width: "100%",
                    padding: "10px 12px",
                    borderRadius: 6,
                    border: "1px solid var(--input-border)",
                    backgroundColor: "var(--input-bg)",
                    color: "var(--input-text)",
                    fontSize: "12px",
                  }}
                >
                  <option value={1}>P1 - Critical / Emergency (High Risk)</option>
                  <option value={2}>P2 - Major Change (Medium-High Risk)</option>
                  <option value={3}>P3 - Normal Change (Standard Risk)</option>
                  <option value={4}>P4 - Minor / Routine Change</option>
                </select>
              </div>

              <div style={{ marginBottom: 20 }}>
                <label style={{ display: "block", fontSize: "11px", fontWeight: 600, marginBottom: 6, color: "var(--text-label)" }}>
                  Change Scope &amp; Rollback Plan
                </label>
                <textarea
                  rows={4}
                  placeholder="Detail the scope of change, execution steps, and backout / rollback procedures if validation fails..."
                  value={scheduleDescription}
                  onChange={(e) => setScheduleDescription(e.target.value)}
                  style={{
                    width: "100%",
                    padding: "10px 12px",
                    borderRadius: 6,
                    border: "1px solid var(--input-border)",
                    backgroundColor: "var(--input-bg)",
                    color: "var(--input-text)",
                    fontSize: "12px",
                    resize: "vertical",
                  }}
                />
              </div>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => setShowScheduleModal(false)}
                  disabled={scheduleSubmitting}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="primary-button"
                  disabled={scheduleSubmitting || !scheduleTitle.trim()}
                >
                  {scheduleSubmitting ? "Scheduling…" : "Confirm & Schedule RFC"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </section>
  );
}
