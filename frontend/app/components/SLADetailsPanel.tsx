"use client";

import { useEffect, useState } from "react";
import SLALiveBadge from "./SLALiveBadge";

export type SLADetailProps = {
  ticketId: number;
  status: string;
  authorization: string;
  staff: boolean;
  ttoDueAt?: string | null;
  ttrDueAt?: string | null;
  solvedAt?: string | null;
  closedAt?: string | null;
  firstRespondedAt?: string | null;
  priorityCode?: string | null;
  matchedTier?: string | null;
  matchedReason?: string | null;
  slaPaused?: boolean;
  breachCode?: string | null;
  breachReason?: string | null;
  onRefresh?: () => void;
};

type HoldItem = {
  id: number;
  ticket_id: number;
  reason: string;
  start_time: string;
  end_time: string | null;
  is_active: boolean;
  duration_minutes: number;
};

type LiveSLAState = {
  effective_start_at?: string;
  is_on_hold?: boolean;
  is_closed?: boolean;
  sla_health?: string;
  health?: string;
  total_hold_minutes?: number;
  response?: {
    target_minutes: number;
    elapsed_minutes: number;
    remaining_minutes: number;
    percent: number;
    responded: boolean;
    met: boolean;
    breached: boolean;
    due_at: string;
  };
  resolution?: {
    target_minutes: number;
    elapsed_minutes: number;
    remaining_minutes: number;
    percent: number;
    resolved: boolean;
    met: boolean;
    breached: boolean;
    due_at: string;
  };
};

export default function SLADetailsPanel({
  ticketId,
  status,
  authorization,
  staff,
  ttoDueAt,
  ttrDueAt,
  solvedAt,
  closedAt,
  firstRespondedAt,
  priorityCode,
  matchedTier,
  matchedReason,
  slaPaused,
  breachCode,
  breachReason,
  onRefresh,
}: SLADetailProps) {
  const [liveState, setLiveState] = useState<LiveSLAState | null>(null);
  const [holds, setHolds] = useState<HoldItem[]>([]);
  const [showHolds, setShowHolds] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!staff || !ticketId) return;

    // Fetch live state and hold intervals in parallel
    void fetch(`/api/v1/sla/tickets/${ticketId}/state`, {
      headers: { Authorization: authorization },
    })
      .then(async (res) => {
        if (res.ok) setLiveState(await res.json());
      })
      .catch(() => {});

    void fetch(`/api/v1/sla/tickets/${ticketId}/holds`, {
      headers: { Authorization: authorization },
    })
      .then(async (res) => {
        if (res.ok) setHolds(await res.json());
      })
      .catch(() => {});
  }, [ticketId, status, staff, authorization]);

  async function handleQuickPause(reason: string) {
    setBusy(true);
    try {
      const res = await fetch(`/api/v1/sla/tickets/${ticketId}/pause?reason=${encodeURIComponent(reason)}`, {
        method: "POST",
        headers: { Authorization: authorization },
      });
      if (res.ok && onRefresh) {
        onRefresh();
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleQuickResume() {
    setBusy(true);
    try {
      const res = await fetch(`/api/v1/sla/tickets/${ticketId}/resume`, {
        method: "POST",
        headers: { Authorization: authorization },
      });
      if (res.ok && onRefresh) {
        onRefresh();
      }
    } finally {
      setBusy(false);
    }
  }

  const isPaused = slaPaused || status === "PENDING" || liveState?.is_on_hold;
  const isDone = Boolean(solvedAt || closedAt);

  const respPct = Math.min(100, Math.max(0, liveState?.response?.percent ?? 0));
  const resPct = Math.min(100, Math.max(0, liveState?.resolution?.percent ?? 0));

  function getBarColor(pct: number, breached?: boolean) {
    if (breached) return "var(--danger)";
    if (pct >= 75) return "#ea580c";
    if (pct >= 50) return "var(--warning)";
    return "var(--success)";
  }

  const tierLabels: Record<string, string> = {
    GLOBAL_OVERRIDE: "Tier 1: Global Override",
    ASSET: "Tier 2: Asset / CI Override",
    CONTRACT: "Tier 3: Customer Contract",
    RULE: "Tier 4: Automation Rule Tag",
    SITE_DEFAULT: "Tier 5: Site / Ticket Type Default",
  };

  return (
    <section className="drawer-section sla-details-panel" style={{ marginTop: "16px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
        <h3 style={{ margin: 0, display: "flex", alignItems: "center", gap: "8px", color: "var(--ink)", fontSize: "12px", fontWeight: 700 }}>
          <span>Service Level Agreement (HaloITSM SLA)</span>
          {isPaused && (
            <span style={{ fontSize: "10px", backgroundColor: "var(--warning-soft)", color: "var(--warning)", border: "1px solid var(--warning-border)", padding: "2px 8px", borderRadius: "10px", fontWeight: 600 }}>
              ⏸ Paused on Hold
            </span>
          )}
        </h3>
        {priorityCode && (
          <span
            style={{
              fontSize: "11px",
              fontWeight: 700,
              padding: "2px 8px",
              borderRadius: "6px",
              backgroundColor: priorityCode === "P1" ? "var(--danger-soft)" : priorityCode === "P2" ? "var(--warning-soft)" : "var(--badge-neutral-bg)",
              color: priorityCode === "P1" ? "var(--danger)" : priorityCode === "P2" ? "var(--warning)" : "var(--ink)",
              border: "1px solid var(--line)",
            }}
          >
            {priorityCode} ITIL Target
          </span>
        )}
      </div>

      {matchedTier && (
        <div style={{ fontSize: "12px", color: "var(--muted)", marginBottom: "14px", display: "flex", alignItems: "center", gap: "6px" }}>
          <span style={{ fontWeight: 600, color: "var(--ink)" }}>Cascade:</span>
          <span style={{ padding: "1px 6px", borderRadius: "4px", backgroundColor: "var(--surface-subtle)", border: "1px solid var(--line)", fontSize: "11px", color: "var(--ink)" }}>
            {tierLabels[matchedTier] || matchedTier}
          </span>
          {matchedReason && <span style={{ fontStyle: "italic", fontSize: "11px" }}>({matchedReason})</span>}
        </div>
      )}

      {/* Response SLA (Respond By) */}
      <div style={{ padding: "12px", borderRadius: "8px", border: "1px solid var(--line)", backgroundColor: "var(--surface)", marginBottom: "10px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
          <div style={{ fontWeight: 600, fontSize: "12px", color: "var(--ink)" }}>Response SLA (First Reply)</div>
          <SLALiveBadge
            dueAt={ttoDueAt || liveState?.response?.due_at}
            solvedAt={firstRespondedAt || (liveState?.response?.responded ? liveState?.response?.due_at : undefined)}
            isPaused={isPaused}
            health={liveState?.response?.breached ? "breached" : liveState?.response?.met ? "met" : undefined}
          />
        </div>
        <div style={{ height: "6px", width: "100%", backgroundColor: "var(--line)", borderRadius: "3px", overflow: "hidden", marginBottom: "6px" }}>
          <div
            style={{
              height: "100%",
              width: `${respPct}%`,
              backgroundColor: getBarColor(respPct, liveState?.response?.breached),
              transition: "width 0.4s ease",
            }}
          />
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: "11px", color: "var(--muted)" }}>
          <span>
            {firstRespondedAt
              ? `Responded: ${new Date(firstRespondedAt).toLocaleTimeString()}`
              : liveState?.response
              ? `${Math.round(liveState.response.remaining_minutes)}m remaining (${Math.round(liveState.response.elapsed_minutes)}m / ${liveState.response.target_minutes}m target)`
              : ttoDueAt
              ? `Due: ${new Date(ttoDueAt).toLocaleTimeString()}`
              : "No target set"}
          </span>
          <span>{respPct}% elapsed</span>
        </div>
      </div>

      {/* Resolution SLA (Fix By) */}
      <div style={{ padding: "12px", borderRadius: "8px", border: "1px solid var(--line)", backgroundColor: "var(--surface)", marginBottom: "12px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
          <div style={{ fontWeight: 600, fontSize: "12px", color: "var(--ink)" }}>Resolution SLA (Fix By)</div>
          <SLALiveBadge
            dueAt={ttrDueAt || liveState?.resolution?.due_at}
            solvedAt={solvedAt || closedAt}
            isPaused={isPaused}
            health={liveState?.resolution?.breached ? "breached" : liveState?.resolution?.met ? "met" : undefined}
          />
        </div>
        <div style={{ height: "6px", width: "100%", backgroundColor: "var(--line)", borderRadius: "3px", overflow: "hidden", marginBottom: "6px" }}>
          <div
            style={{
              height: "100%",
              width: `${resPct}%`,
              backgroundColor: getBarColor(resPct, liveState?.resolution?.breached),
              transition: "width 0.4s ease",
            }}
          />
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: "11px", color: "var(--muted)" }}>
          <span>
            {isDone
              ? `Resolved: ${when(solvedAt || closedAt)}`
              : liveState?.resolution
              ? `${Math.round(liveState.resolution.remaining_minutes)}m remaining (${Math.round(liveState.resolution.elapsed_minutes)}m / ${liveState.resolution.target_minutes}m target)`
              : ttrDueAt
              ? `Due: ${when(ttrDueAt)}`
              : "No target set"}
          </span>
          <span>{resPct}% elapsed</span>
        </div>
      </div>

      {/* Breach Justification Details if logged */}
      {breachCode && (
        <div style={{ padding: "10px 14px", backgroundColor: "var(--danger-soft)", border: "1px solid var(--danger-border)", borderRadius: "6px", marginBottom: "12px", fontSize: "12px" }}>
          <div style={{ fontWeight: 700, color: "var(--danger)", marginBottom: "2px" }}>
            Breach Justification: {breachCode}
          </div>
          {breachReason && (
            <div style={{ color: "var(--muted)", fontStyle: "italic" }}>
              &ldquo;{breachReason}&rdquo;
            </div>
          )}
        </div>
      )}

      {/* Hold / Pause Management & History */}
      <div style={{ marginTop: "10px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <button
            type="button"
            onClick={() => setShowHolds(!showHolds)}
            style={{
              background: "none",
              border: "none",
              padding: 0,
              fontSize: "12px",
              color: "var(--accent)",
              cursor: "pointer",
              fontWeight: 500,
              display: "flex",
              alignItems: "center",
              gap: "4px",
            }}
          >
            <span>{showHolds ? "▼ Hide Hold History" : `▶ View Hold History (${holds.length})`}</span>
          </button>

          {!isDone && staff && (
            <div style={{ display: "flex", gap: "6px" }}>
              {isPaused ? (
                <button
                  type="button"
                  onClick={handleQuickResume}
                  disabled={busy}
                  style={{
                    fontSize: "11px",
                    padding: "4px 10px",
                    borderRadius: "4px",
                    border: "1px solid var(--success-border)",
                    backgroundColor: "var(--success-soft)",
                    color: "var(--success)",
                    cursor: "pointer",
                    fontWeight: 600,
                  }}
                >
                  ▶ Resume SLA
                </button>
              ) : (
                <select
                  disabled={busy}
                  onChange={(e) => {
                    if (e.target.value) {
                      void handleQuickPause(e.target.value);
                      e.target.value = "";
                    }
                  }}
                  defaultValue=""
                  style={{
                    fontSize: "11px",
                    padding: "4px 8px",
                    borderRadius: "4px",
                    border: "1px solid var(--input-border)",
                    backgroundColor: "var(--input-bg)",
                    color: "var(--input-text)",
                    cursor: "pointer",
                  }}
                >
                  <option value="" disabled>⏸ Pause on Hold…</option>
                  <option value="awaiting_user">Awaiting Customer Reply</option>
                  <option value="awaiting_vendor">Awaiting 3rd-Party Vendor</option>
                  <option value="awaiting_approval">Awaiting Management Approval</option>
                  <option value="on_hold">General Hold</option>
                </select>
              )}
            </div>
          )}
        </div>

        {showHolds && (
          <div style={{ marginTop: "10px", padding: "10px 14px", backgroundColor: "var(--surface)", borderRadius: "6px", border: "1px solid var(--line)" }}>
            {holds.length === 0 ? (
              <p style={{ margin: 0, fontSize: "11px", color: "var(--muted)" }}>
                No SLA hold intervals recorded for this ticket.
              </p>
            ) : (
              <ul style={{ margin: 0, paddingLeft: "16px", fontSize: "11px", display: "flex", flexDirection: "column", gap: "6px", color: "var(--ink)" }}>
                {holds.map((h) => (
                  <li key={h.id}>
                    <strong>{h.reason}</strong>: {new Date(h.start_time).toLocaleString()} &rarr;{" "}
                    {h.end_time ? new Date(h.end_time).toLocaleString() : <span style={{ color: "var(--warning)", fontWeight: 600 }}>Active</span>}
                    <span style={{ marginLeft: "6px", color: "var(--muted)" }}>({h.duration_minutes}m held)</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

function when(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}
