"use client";

import { useEffect, useState } from "react";

type LinkItem = {
  id: number;
  ticket_id: number;
  linked_ticket_id: number;
  link_type: "DUPLICATE" | "PARENT" | "CHILD" | "RELATED";
  created_at: string;
  linked_ticket_title?: string | null;
  linked_ticket_status?: string | null;
};

type SolutionItem = {
  id: number;
  ticket_id: number;
  author_id: number;
  solution_type: string;
  body: string;
  status: string;
  created_at: string;
};

type KedbItem = {
  id: number;
  ticket_id: number;
  ticket_title: string;
  solution_type: string;
  body: string;
  status: string;
  created_at: string;
};

type Props = {
  ticketId: number;
  ticketTitle: string;
  authorization: string;
  onRefreshTicket: () => Promise<void>;
  showToast: (type: "success" | "error" | "info", message: string) => void;
};

export function TicketLinksTab({ ticketId, authorization, onRefreshTicket, showToast }: Props) {
  const [links, setLinks] = useState<LinkItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [targetId, setTargetId] = useState("");
  const [linkType, setLinkType] = useState<"CHILD" | "PARENT" | "DUPLICATE" | "RELATED">("CHILD");
  const [submitting, setSubmitting] = useState(false);
  const [cascadeNote, setCascadeNote] = useState("");
  const [cascading, setCascading] = useState(false);

  const headers = { Authorization: authorization, "Content-Type": "application/json" };

  async function loadLinks() {
    setLoading(true);
    try {
      const res = await fetch(`/api/v1/tickets/${ticketId}/links`, { headers });
      if (res.ok) setLinks(await res.json());
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadLinks();
  }, [ticketId]);

  async function handleAddLink(e: React.FormEvent) {
    e.preventDefault();
    const idNum = parseInt(targetId, 10);
    if (!idNum || idNum === ticketId) {
      showToast("error", "Enter a valid different ticket ID.");
      return;
    }
    setSubmitting(true);
    try {
      const res = await fetch(`/api/v1/tickets/${ticketId}/links`, {
        method: "POST",
        headers,
        body: JSON.stringify({ linked_ticket_id: idNum, link_type: linkType }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showToast("error", err.detail || "Could not link ticket.");
        return;
      }
      showToast("success", `Ticket #${idNum} linked as ${linkType}.`);
      setTargetId("");
      await loadLinks();
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDeleteLink(linkId: number) {
    try {
      const res = await fetch(`/api/v1/tickets/${ticketId}/links/${linkId}`, {
        method: "DELETE",
        headers,
      });
      if (res.ok) {
        showToast("success", "Link removed.");
        await loadLinks();
      }
    } catch {
      showToast("error", "Failed to remove link.");
    }
  }

  async function handleCascadeResolve() {
    setCascading(true);
    try {
      const res = await fetch(`/api/v1/tickets/${ticketId}/cascade-resolve`, {
        method: "POST",
        headers,
        body: JSON.stringify({ note: cascadeNote }),
      });
      if (res.ok) {
        const data = await res.json();
        showToast("success", `Cascade resolved ${data.resolved_count} child ticket(s)!`);
        setCascadeNote("");
        await onRefreshTicket();
        await loadLinks();
      } else {
        showToast("error", "Cascade resolution failed.");
      }
    } finally {
      setCascading(false);
    }
  }

  const childLinks = links.filter((l) => l.link_type === "PARENT" || l.link_type === "CHILD");

  return (
    <div className="drawer-section" style={{ padding: "16px 0" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <h3 style={{ margin: 0 }}>Linked Tickets ({links.length})</h3>
        {childLinks.length > 0 && (
          <button
            type="button"
            className="secondary-button"
            style={{ fontSize: "0.8rem", color: "#16a34a", borderColor: "#86efac" }}
            onClick={handleCascadeResolve}
            disabled={cascading}
          >
            {cascading ? "Resolving..." : "Resolve All Child Tickets"}
          </button>
        )}
      </div>

      <form onSubmit={handleAddLink} style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
        <input
          type="number"
          placeholder="Ticket ID"
          value={targetId}
          onChange={(e) => setTargetId(e.target.value)}
          required
          style={{ width: 110, padding: "6px 10px", borderRadius: 6, border: "1px solid #cbd5e1" }}
        />
        <select
          value={linkType}
          onChange={(e) => setLinkType(e.target.value as any)}
          style={{ padding: "6px 10px", borderRadius: 6, border: "1px solid #cbd5e1" }}
        >
          <option value="CHILD">Child of this ticket</option>
          <option value="PARENT">Master / Parent ticket</option>
          <option value="DUPLICATE">Duplicate of</option>
          <option value="RELATED">Related ticket</option>
        </select>
        <button type="submit" className="primary-button" disabled={submitting} style={{ padding: "6px 14px" }}>
          {submitting ? "Linking..." : "+ Link"}
        </button>
      </form>

      {loading && <p className="muted">Loading links…</p>}

      {!loading && links.length === 0 && (
        <p className="muted" style={{ fontSize: "0.9rem" }}>
          No linked tickets. Link recurring incidents to this ticket to manage root causes together.
        </p>
      )}

      {links.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {links.map((link) => (
            <div
              key={link.id}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "8px 12px",
                background: "#f8fafc",
                borderRadius: 6,
                border: "1px solid #e2e8f0",
              }}
            >
              <div>
                <span
                  style={{
                    display: "inline-block",
                    padding: "2px 6px",
                    borderRadius: 4,
                    fontSize: "0.75rem",
                    fontWeight: 600,
                    marginRight: 8,
                    background:
                      link.link_type === "PARENT"
                        ? "#e0e7ff"
                        : link.link_type === "CHILD"
                        ? "#fef3c7"
                        : link.link_type === "DUPLICATE"
                        ? "#fee2e2"
                        : "#f1f5f9",
                    color:
                      link.link_type === "PARENT"
                        ? "#4338ca"
                        : link.link_type === "CHILD"
                        ? "#b45309"
                        : link.link_type === "DUPLICATE"
                        ? "#b91c1c"
                        : "#475569",
                  }}
                >
                  {link.link_type}
                </span>
                <strong>#{link.linked_ticket_id}</strong>: {link.linked_ticket_title || "Ticket"}
                {link.linked_ticket_status && (
                  <small style={{ marginLeft: 8, color: "#64748b" }}>({link.linked_ticket_status})</small>
                )}
              </div>
              <button
                type="button"
                onClick={() => handleDeleteLink(link.id)}
                style={{
                  background: "transparent",
                  border: "none",
                  color: "#ef4444",
                  cursor: "pointer",
                  fontSize: "1rem",
                }}
                title="Remove link"
              >
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2"><path d="m6 6 12 12M18 6 6 18" /></svg>
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function TicketSolutionsTab({ ticketId, authorization, onRefreshTicket, showToast }: Props) {
  const [solutions, setSolutions] = useState<SolutionItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [body, setBody] = useState("");
  const [solType, setSolType] = useState<"WORKAROUND" | "FIX">("WORKAROUND");
  const [saving, setSaving] = useState(false);

  // KEDB Search
  const [kedbQuery, setKedbQuery] = useState("");
  const [kedbResults, setKedbResults] = useState<KedbItem[]>([]);
  const [searchingKedb, setSearchingKedb] = useState(false);

  const headers = { Authorization: authorization, "Content-Type": "application/json" };

  async function loadSolutions() {
    setLoading(true);
    try {
      const res = await fetch(`/api/v1/tickets/${ticketId}/solutions`, { headers });
      if (res.ok) setSolutions(await res.json());
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadSolutions();
  }, [ticketId]);

  async function handleAddSolution(e: React.FormEvent) {
    e.preventDefault();
    if (!body.trim()) return;
    setSaving(true);
    try {
      const res = await fetch(`/api/v1/tickets/${ticketId}/solutions`, {
        method: "POST",
        headers,
        body: JSON.stringify({ body: body.trim(), solution_type: solType }),
      });
      if (res.ok) {
        showToast("success", `${solType === "WORKAROUND" ? "Workaround" : "Permanent Fix"} recorded to KEDB!`);
        setBody("");
        await loadSolutions();
      } else {
        showToast("error", "Could not save solution.");
      }
    } finally {
      setSaving(false);
    }
  }

  async function handleApprove(solutionId: number) {
    try {
      const res = await fetch(`/api/v1/tickets/${ticketId}/solutions/${solutionId}/approve`, {
        method: "POST",
        headers,
      });
      if (res.ok) {
        showToast("success", "Solution approved and ticket closed");
        await onRefreshTicket();
        await loadSolutions();
      }
    } catch {
      showToast("error", "Failed to approve solution.");
    }
  }

  async function searchKedb() {
    if (!kedbQuery.trim()) return;
    setSearchingKedb(true);
    try {
      const res = await fetch(`/api/v1/kedb?q=${encodeURIComponent(kedbQuery.trim())}`, { headers });
      if (res.ok) setKedbResults(await res.json());
    } finally {
      setSearchingKedb(false);
    }
  }

  return (
    <div className="drawer-section" style={{ padding: "16px 0" }}>
      <h3 style={{ marginTop: 0 }}>Propose Workaround or Fix</h3>
      <form onSubmit={handleAddSolution} style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 20 }}>
        <div style={{ display: "flex", gap: 10 }}>
          <label style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
            <input
              type="radio"
              name="solType"
              checked={solType === "WORKAROUND"}
              onChange={() => setSolType("WORKAROUND")}
            />
            <span>Workaround (Known Error)</span>
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
            <input
              type="radio"
              name="solType"
              checked={solType === "FIX"}
              onChange={() => setSolType("FIX")}
            />
            <span>Permanent Fix</span>
          </label>
        </div>
        <textarea
          rows={3}
          placeholder="Describe workaround steps or root cause resolution details..."
          value={body}
          onChange={(e) => setBody(e.target.value)}
          required
          style={{ width: "100%", padding: 8, borderRadius: 6, border: "1px solid #cbd5e1", resize: "vertical" }}
        />
        <button type="submit" className="primary-button" disabled={saving} style={{ alignSelf: "flex-start" }}>
          {saving ? "Publishing..." : "Publish to KEDB"}
        </button>
      </form>

      <h3>Ticket Solutions ({solutions.length})</h3>
      {loading && <p className="muted">Loading solutions…</p>}
      {!loading && solutions.length === 0 && (
        <p className="muted" style={{ fontSize: "0.9rem" }}>No workarounds or solutions logged yet for this ticket.</p>
      )}
      {solutions.map((sol) => (
        <div
          key={sol.id}
          style={{
            padding: 12,
            background: sol.solution_type === "WORKAROUND" ? "#fffbeb" : "#f0fdf4",
            border: `1px solid ${sol.solution_type === "WORKAROUND" ? "#fef3c7" : "#bbf7d0"}`,
            borderRadius: 6,
            marginBottom: 10,
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
            <span style={{ fontWeight: 600, fontSize: "0.85rem", color: sol.solution_type === "WORKAROUND" ? "#b45309" : "#15803d" }}>
              {sol.solution_type === "WORKAROUND" ? "Known Workaround" : "Permanent Fix"} · {sol.status}
            </span>
            {sol.status !== "APPROVED" && (
              <button
                type="button"
                className="secondary-button"
                style={{ fontSize: "0.75rem", padding: "2px 8px" }}
                onClick={() => handleApprove(sol.id)}
              >
                Approve & Close
              </button>
            )}
          </div>
          <p style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: "0.9rem" }}>{sol.body}</p>
        </div>
      ))}

      <hr style={{ margin: "24px 0", border: "none", borderTop: "1px solid #e2e8f0" }} />

      <h3>Search KEDB (Known Error Database)</h3>
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        <input
          type="text"
          placeholder="Search past error workarounds..."
          value={kedbQuery}
          onChange={(e) => setKedbQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void searchKedb();
            }
          }}
          style={{ flex: 1, padding: "6px 10px", borderRadius: 6, border: "1px solid #cbd5e1" }}
        />
        <button type="button" className="secondary-button" onClick={searchKedb} disabled={searchingKedb}>
          {searchingKedb ? "Searching..." : "Search"}
        </button>
      </div>

      {kedbResults.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {kedbResults.map((item) => (
            <div
              key={item.id}
              style={{
                padding: 10,
                background: "#f8fafc",
                border: "1px solid #e2e8f0",
                borderRadius: 6,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                <strong>#{item.ticket_id}: {item.ticket_title}</strong>
                <span style={{ fontSize: "0.75rem", color: "#64748b" }}>{item.solution_type}</span>
              </div>
              <p style={{ margin: 0, fontSize: "0.85rem", color: "#334155" }}>{item.body}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
