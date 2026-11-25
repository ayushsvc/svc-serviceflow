"use client";

import { useEffect, useState } from "react";

type CalendarItem = {
  id: number;
  name: string;
  timezone: string;
  is_24_7: boolean;
  workdays: number[];
  start_time: string;
  end_time: string;
  breaks: Array<{ name: string; start: string; end: string }>;
  holidays: Array<{ name: string; date: string }>;
  delayed_start_cutoff?: string | null;
  is_active: boolean;
};

type PolicyItem = {
  id: number;
  name: string;
  description?: string | null;
  calendar_id?: number | null;
  priorities_config: Record<string, { response_mins: number; resolution_mins: number; ola_mins?: number }>;
  warning1_pct: number;
  warning2_pct: number;
  auto_escalate_on_breach: boolean;
  require_breach_code: boolean;
  reset_response_on_user_reply: boolean;
  extend_to_eod: boolean;
  is_default: boolean;
  is_active: boolean;
};

type AnalyticsData = {
  total_tickets: number;
  compliance_pct: number;
  response_compliance_pct: number;
  resolution_compliance_pct: number;
  breached_count: number;
  on_track_count: number;
  paused_count: number;
  avg_mttr_minutes: number;
  breach_reasons: Record<string, number>;
};

type Props = {
  authorization: string;
  isOpen: boolean;
  onClose: () => void;
};

export default function SLAStudioModal({ authorization, isOpen, onClose }: Props) {
  const [activeTab, setActiveTab] = useState<"analytics" | "calendars" | "policies" | "simulator">("analytics");
  const [analytics, setAnalytics] = useState<AnalyticsData | null>(null);
  const [calendars, setCalendars] = useState<CalendarItem[]>([]);
  const [policies, setPolicies] = useState<PolicyItem[]>([]);
  const [loading, setLoading] = useState(true);

  // Simulator state
  const [simMinutes, setSimMinutes] = useState(240);
  const [simCalId, setSimCalId] = useState<number | undefined>(undefined);
  const [simExtendEod, setSimExtendEod] = useState(false);
  const [simResult, setSimResult] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    setLoading(true);
    Promise.all([
      fetch("/api/v1/sla/analytics", { headers: { Authorization: authorization } }).then((r) => r.json()),
      fetch("/api/v1/sla/calendars", { headers: { Authorization: authorization } }).then((r) => r.json()),
      fetch("/api/v1/sla/policies", { headers: { Authorization: authorization } }).then((r) => r.json()),
    ])
      .then(([a, c, p]) => {
        setAnalytics(a);
        setCalendars(Array.isArray(c) ? c : []);
        setPolicies(Array.isArray(p) ? p : []);
        if (Array.isArray(c) && c.length > 0) {
          setSimCalId(c[0].id);
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [isOpen, authorization]);

  async function handleSimulate() {
    try {
      const res = await fetch("/api/v1/sla/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: authorization },
        body: JSON.stringify({
          minutes_to_add: simMinutes,
          calendar_id: simCalId,
          extend_to_eod: simExtendEod,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setSimResult(`${new Date(data.calculated_due_at).toLocaleString()} (${data.calendar_name} / ${data.calendar_timezone})`);
      }
    } catch {
      setSimResult("Simulation error");
    }
  }

  if (!isOpen) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="sla-modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="sla-studio-container">
        {/* Header matching .modal-header typography */}
        <div className="sla-studio-header">
          <div className="sla-studio-header-title">
            <p className="kicker">SERVICE LEVEL MANAGEMENT</p>
            <h2>SLA Configuration Studio &amp; Compliance</h2>
            <p className="subheading">
              HaloITSM Service Level Architecture: Cascading Precedence, Calendars &amp; SLA Analytics
            </p>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={onClose}
            aria-label="Close SLA Studio"
          >
            <svg aria-hidden="true" viewBox="0 0 24 24" className="icon"><path d="m6 6 12 12M18 6 6 18" /></svg>
          </button>
        </div>

        {/* Tab Navigation matching clean .tab typography without emoji prefixes */}
        <nav className="sla-tabs-nav" aria-label="SLA Studio Navigation">
          {[
            { id: "analytics", label: "Compliance Analytics" },
            { id: "calendars", label: "Workday Calendars" },
            { id: "policies", label: "SLA Policies & Targets" },
            { id: "simulator", label: "SLA Calculator" },
          ].map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={`sla-tab-btn ${activeTab === tab.id ? "active" : ""}`}
              onClick={() => setActiveTab(tab.id as typeof activeTab)}
            >
              {tab.label}
            </button>
          ))}
        </nav>

        {/* Content Area */}
        <div className="sla-studio-body">
          {loading ? (
            <p className="muted" style={{ fontSize: "12px" }}>Loading SLA Studio…</p>
          ) : (
            <>
              {/* Tab 1: Analytics */}
              {activeTab === "analytics" && analytics && (
                <div>
                  <div className="sla-metrics-grid">
                    <div className="sla-metric-card">
                      <span className="sla-metric-label">Overall Compliance</span>
                      <strong className={`sla-metric-val ${analytics.compliance_pct >= 90 ? "val-green" : analytics.compliance_pct >= 75 ? "val-amber" : "val-red"}`}>
                        {analytics.compliance_pct}%
                      </strong>
                    </div>
                    <div className="sla-metric-card">
                      <span className="sla-metric-label">Response Met Rate</span>
                      <strong className="sla-metric-val val-green">
                        {analytics.response_compliance_pct}%
                      </strong>
                    </div>
                    <div className="sla-metric-card">
                      <span className="sla-metric-label">Resolution Met Rate</span>
                      <strong className="sla-metric-val val-blue">
                        {analytics.resolution_compliance_pct}%
                      </strong>
                    </div>
                    <div className="sla-metric-card">
                      <span className="sla-metric-label">Average MTTR</span>
                      <strong className="sla-metric-val">
                        {Math.round(analytics.avg_mttr_minutes / 60)}h {Math.round(analytics.avg_mttr_minutes % 60)}m
                      </strong>
                    </div>
                  </div>

                  <div className="sla-section-card">
                    <h3 style={{ margin: "0 0 14px", fontSize: "13px", fontWeight: 700, color: "var(--ink)", letterSpacing: "-.02em" }}>
                      Breach Justification Breakdown
                    </h3>
                    {Object.keys(analytics.breach_reasons || {}).length === 0 ? (
                      <p className="muted" style={{ margin: 0, fontSize: "12px" }}>
                        No breach justifications recorded yet.
                      </p>
                    ) : (
                      <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                        {Object.entries(analytics.breach_reasons).map(([code, count]) => (
                          <div key={code} className="sla-item-row">
                            <span style={{ fontWeight: 600 }}>{code}</span>
                            <span
                              style={{
                                padding: "2px 8px",
                                borderRadius: "10px",
                                backgroundColor: "var(--danger-soft)",
                                color: "var(--danger)",
                                border: "1px solid var(--danger-border)",
                                fontWeight: 700,
                                fontSize: "11px",
                              }}
                            >
                              {count} tickets
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Tab 2: Calendars */}
              {activeTab === "calendars" && (
                <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
                  {calendars.map((cal) => (
                    <div key={cal.id} className="sla-section-card">
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
                        <div style={{ fontWeight: 700, fontSize: "14px", color: "var(--ink)", letterSpacing: "-.02em" }}>{cal.name}</div>
                        <span
                          style={{
                            fontSize: "11px",
                            padding: "3px 10px",
                            borderRadius: "12px",
                            backgroundColor: cal.is_24_7 ? "var(--accent-soft)" : "var(--badge-neutral-bg)",
                            color: cal.is_24_7 ? "var(--accent)" : "var(--text-secondary)",
                            fontWeight: 600,
                            border: "1px solid var(--line)",
                          }}
                        >
                          {cal.is_24_7 ? "24x7 Continuous" : `${cal.start_time} - ${cal.end_time}`}
                        </span>
                      </div>
                      <div style={{ fontSize: "12px", color: "var(--muted)", display: "flex", flexWrap: "wrap", gap: "16px" }}>
                        <div><strong style={{ color: "var(--ink)" }}>Timezone:</strong> {cal.timezone}</div>
                        <div><strong style={{ color: "var(--ink)" }}>Breaks:</strong> {(cal.breaks || []).map((b) => `${b.name} (${b.start}-${b.end})`).join(", ") || "None"}</div>
                        <div><strong style={{ color: "var(--ink)" }}>Delayed Cutoff:</strong> {cal.delayed_start_cutoff || "None"}</div>
                        <div><strong style={{ color: "var(--ink)" }}>Holidays:</strong> {(cal.holidays || []).length} registered</div>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Tab 3: Policies */}
              {activeTab === "policies" && (
                <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
                  {policies.map((pol) => (
                    <div key={pol.id} className="sla-section-card">
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                        <div style={{ fontWeight: 700, fontSize: "14px", color: "var(--ink)", letterSpacing: "-.02em" }}>{pol.name}</div>
                        <div style={{ display: "flex", gap: "6px" }}>
                          {pol.auto_escalate_on_breach && (
                            <span
                              style={{
                                fontSize: "10px",
                                padding: "2px 6px",
                                borderRadius: "4px",
                                backgroundColor: "var(--warning-soft)",
                                color: "var(--warning)",
                                border: "1px solid var(--warning-border)",
                                fontWeight: 600,
                              }}
                            >
                              Auto-Escalate
                            </span>
                          )}
                          {pol.require_breach_code && (
                            <span
                              style={{
                                fontSize: "10px",
                                padding: "2px 6px",
                                borderRadius: "4px",
                                backgroundColor: "var(--danger-soft)",
                                color: "var(--danger)",
                                border: "1px solid var(--danger-border)",
                                fontWeight: 600,
                              }}
                            >
                              Mandatory Breach Code
                            </span>
                          )}
                        </div>
                      </div>
                      {pol.description && <p style={{ fontSize: "12px", color: "var(--muted)", margin: "0 0 12px 0" }}>{pol.description}</p>}
                      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "8px" }}>
                        {["P1", "P2", "P3", "P4"].map((tier) => {
                          const t = (pol.priorities_config || {})[tier] || {};
                          return (
                            <div
                              key={tier}
                              style={{
                                padding: "10px",
                                borderRadius: "6px",
                                backgroundColor: "var(--surface-subtle)",
                                border: "1px solid var(--line)",
                              }}
                            >
                              <div style={{ fontWeight: 700, marginBottom: "4px", color: "var(--ink)", fontSize: "12px" }}>{tier}</div>
                              <div style={{ fontSize: "11px", color: "var(--muted)" }}>
                                Resp: <strong style={{ color: "var(--ink)" }}>{t.response_mins || 60}m</strong>
                              </div>
                              <div style={{ fontSize: "11px", color: "var(--muted)" }}>
                                Fix: <strong style={{ color: "var(--ink)" }}>{t.resolution_mins || 480}m</strong>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Tab 4: Simulator */}
              {activeTab === "simulator" && (
                <div className="sla-section-card" style={{ maxWidth: "520px" }}>
                  <p className="subheading" style={{ margin: "0 0 18px", fontSize: "12px", lineHeight: "1.6" }}>
                    Simulate minute-accurate SLA due timestamps excluding off-hours, breaks, holidays, and delayed-start cutoffs.
                  </p>
                  <div style={{ marginBottom: "16px" }}>
                    <label style={{ display: "block", fontSize: "11px", fontWeight: 600, marginBottom: "8px", color: "var(--text-label)" }}>
                      Target Working Minutes
                    </label>
                    <input
                      type="number"
                      value={simMinutes}
                      onChange={(e) => setSimMinutes(parseInt(e.target.value) || 0)}
                      style={{
                        width: "100%",
                        padding: "10px 12px",
                        borderRadius: "6px",
                        border: "1px solid var(--input-border)",
                        backgroundColor: "var(--input-bg)",
                        color: "var(--input-text)",
                        fontSize: "12px",
                      }}
                    />
                  </div>
                  <div style={{ marginBottom: "16px" }}>
                    <label style={{ display: "block", fontSize: "11px", fontWeight: 600, marginBottom: "8px", color: "var(--text-label)" }}>
                      Calendar
                    </label>
                    <select
                      value={simCalId}
                      onChange={(e) => setSimCalId(parseInt(e.target.value))}
                      style={{
                        width: "100%",
                        padding: "10px 12px",
                        borderRadius: "6px",
                        border: "1px solid var(--input-border)",
                        backgroundColor: "var(--input-bg)",
                        color: "var(--input-text)",
                        fontSize: "12px",
                      }}
                    >
                      {calendars.map((c) => (
                        <option key={c.id} value={c.id}>{c.name} ({c.timezone})</option>
                      ))}
                    </select>
                  </div>
                  <div style={{ marginBottom: "20px" }}>
                    <label className="checkbox" style={{ cursor: "pointer", fontSize: "12px" }}>
                      <input
                        type="checkbox"
                        checked={simExtendEod}
                        onChange={(e) => setSimExtendEod(e.target.checked)}
                      />
                      <span style={{ color: "var(--ink)" }}>Extend deadline to End of Day (EOD)</span>
                    </label>
                  </div>
                  <button
                    type="button"
                    className="primary-button"
                    onClick={handleSimulate}
                  >
                    Calculate SLA Target
                  </button>
                  {simResult && (
                    <div
                      style={{
                        marginTop: "16px",
                        padding: "12px 14px",
                        borderRadius: "6px",
                        backgroundColor: "var(--success-soft)",
                        border: "1px solid var(--success-border)",
                        fontSize: "12px",
                        color: "var(--success)",
                      }}
                    >
                      <strong>Projected Due:</strong> {simResult}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
