"use client";

import { useEffect, useState } from "react";

export type SLABreachCode = {
  id: number;
  code: string;
  label: string;
  description?: string | null;
  is_active: boolean;
};

type Props = {
  ticketId: number;
  targetStatus: string;
  authorization: string;
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (breachCode: string, breachReason: string) => Promise<void>;
};

export default function SLAModal({ ticketId, targetStatus, authorization, isOpen, onClose, onSubmit }: Props) {
  const [codes, setCodes] = useState<SLABreachCode[]>([]);
  const [selectedCode, setSelectedCode] = useState<string>("");
  const [reason, setReason] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(true);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    setLoading(true);
    setError(null);
    fetch("/api/v1/sla/breach-codes", {
      headers: { Authorization: authorization },
    })
      .then(async (res) => {
        if (res.ok) {
          const data: SLABreachCode[] = await res.json();
          setCodes(data);
          if (data.length > 0) {
            setSelectedCode(data[0].code);
          }
        } else {
          setError("Failed to load SLA breach justification codes.");
        }
      })
      .catch(() => setError("Network error loading breach codes."))
      .finally(() => setLoading(false));
  }, [isOpen, authorization]);

  if (!isOpen) return null;

  async function handleConfirm(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedCode) {
      setError("Please select a valid breach justification code.");
      return;
    }
    if (!reason.trim()) {
      setError("Please provide a detailed explanation rationale for the breach.");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      await onSubmit(selectedCode, reason.trim());
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to record breach justification.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="sla-modal-title"
      className="sla-modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <div className="sla-breach-dialog">
        <div className="sla-breach-header">
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <svg viewBox="0 0 24 24" className="icon" fill="none" stroke="var(--danger)" strokeWidth="1.5" style={{ width: 22, height: 22, flexShrink: 0 }}>
              <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
              <line x1="12" y1="9" x2="12" y2="13" />
              <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
            <div>
              <h2 id="sla-modal-title">Resolution SLA Breached</h2>
              <p>Mandatory justification required to set Ticket #{ticketId} to {targetStatus}</p>
            </div>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={onClose}
            disabled={submitting}
            aria-label="Close"
          >
            <svg viewBox="0 0 24 24" className="icon" fill="none" stroke="currentColor" strokeWidth="1.5" style={{ width: 16, height: 16 }}>
              <path d="m6 6 12 12M18 6 6 18" />
            </svg>
          </button>
        </div>

        <form onSubmit={handleConfirm} style={{ padding: "20px", background: "var(--surface-elevated)" }}>
          {error && (
            <div className="error" style={{ marginBottom: "16px" }}>
              {error}
            </div>
          )}

          <p className="muted" style={{ fontSize: "12px", lineHeight: "1.6", marginTop: 0, marginBottom: "16px" }}>
            In accordance with ITIL &amp; HaloITSM Service Level Management standards, closing or resolving a ticket after its SLA deadline requires an audit-tracked justification code and explanatory rationale.
          </p>

          <div style={{ marginBottom: "16px" }}>
            <label
              htmlFor="breach-code-select"
              style={{ display: "block", fontSize: "11px", fontWeight: 600, marginBottom: "6px", color: "var(--text-label)" }}
            >
              Breach Justification Code <span style={{ color: "var(--danger)" }}>*</span>
            </label>
            {loading ? (
              <p className="muted" style={{ fontSize: "12px" }}>Loading breach codes…</p>
            ) : (
              <select
                id="breach-code-select"
                value={selectedCode}
                onChange={(e) => setSelectedCode(e.target.value)}
                required
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  borderRadius: "6px",
                  border: "1px solid var(--input-border)",
                  backgroundColor: "var(--input-bg)",
                  color: "var(--input-text)",
                  fontSize: "12px",
                }}
              >
                {codes.map((c) => (
                  <option key={c.code} value={c.code}>
                    {c.label} ({c.code})
                  </option>
                ))}
              </select>
            )}
          </div>

          <div style={{ marginBottom: "20px" }}>
            <label
              htmlFor="breach-reason-textarea"
              style={{ display: "block", fontSize: "11px", fontWeight: 600, marginBottom: "6px", color: "var(--text-label)" }}
            >
              Detailed Rationale <span style={{ color: "var(--danger)" }}>*</span>
            </label>
            <textarea
              id="breach-reason-textarea"
              rows={4}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Explain the specific root-cause or blockers that delayed resolution (e.g. awaited vendor firmware patch, parts lead time, user unresponsive)..."
              required
              style={{
                width: "100%",
                padding: "8px 12px",
                borderRadius: "6px",
                border: "1px solid var(--input-border)",
                backgroundColor: "var(--input-bg)",
                color: "var(--input-text)",
                fontSize: "12px",
                resize: "vertical",
              }}
            />
          </div>

          <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
            <button
              type="button"
              className="secondary-button"
              onClick={onClose}
              disabled={submitting}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="primary-button"
              disabled={submitting || loading || !selectedCode || !reason.trim()}
              style={{
                backgroundColor: "var(--danger)",
                borderColor: "var(--danger)",
                opacity: submitting || loading ? 0.7 : 1,
              }}
            >
              {submitting ? "Resolving…" : `Confirm & ${targetStatus}`}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
