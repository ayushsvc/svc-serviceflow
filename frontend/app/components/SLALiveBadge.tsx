"use client";

import { useEffect, useState } from "react";

type Props = {
  dueAt?: string | null;
  solvedAt?: string | null;
  isPaused?: boolean;
  health?: "on_track" | "warning_1" | "warning_2" | "paused" | "breached" | "met" | string;
  showIcon?: boolean;
};

export function formatTimeRemaining(dueAtStr?: string | null, solvedAtStr?: string | null, isPaused?: boolean): {
  text: string;
  status: "on_track" | "warning_1" | "warning_2" | "paused" | "breached" | "met" | "none";
} {
  if (isPaused) {
    return { text: "On Hold", status: "paused" };
  }
  if (!dueAtStr) {
    return { text: "No SLA", status: "none" };
  }

  const due = new Date(dueAtStr).getTime();
  if (Number.isNaN(due)) {
    return { text: "Invalid date", status: "none" };
  }

  const targetTime = solvedAtStr ? new Date(solvedAtStr).getTime() : Date.now();
  const diffMs = due - targetTime;

  if (solvedAtStr) {
    return diffMs >= 0
      ? { text: "Met", status: "met" }
      : { text: "Breached", status: "breached" };
  }

  if (diffMs <= 0) {
    const overdueMin = Math.abs(Math.round(diffMs / 60000));
    if (overdueMin < 60) {
      return { text: `${overdueMin}m overdue`, status: "breached" };
    }
    const overdueHrs = Math.floor(overdueMin / 60);
    const remMin = overdueMin % 60;
    return { text: `${overdueHrs}h ${remMin}m overdue`, status: "breached" };
  }

  const leftMin = Math.round(diffMs / 60000);
  if (leftMin < 60) {
    return {
      text: `${leftMin}m left`,
      status: leftMin < 15 ? "warning_2" : "warning_1",
    };
  }
  const leftHrs = Math.floor(leftMin / 60);
  const remMin = leftMin % 60;
  return { text: `${leftHrs}h ${remMin}m left`, status: "on_track" };
}

export default function SLALiveBadge({ dueAt, solvedAt, isPaused, health: initialHealth, showIcon = true }: Props) {
  const [, setTick] = useState(0);

  useEffect(() => {
    if (solvedAt || isPaused || !dueAt) return;
    const interval = setInterval(() => setTick((t) => t + 1), 30000); // refresh every 30s
    return () => clearInterval(interval);
  }, [dueAt, solvedAt, isPaused]);

  const calc = formatTimeRemaining(dueAt, solvedAt, isPaused);
  const effectiveStatus = initialHealth && initialHealth !== "on_track" ? initialHealth : calc.status;

  const iconMap: Record<string, string> = {
    on_track: "⏱",
    warning_1: "⚡",
    warning_2: "⚠️",
    breached: "✕",
    paused: "⏸",
    met: "✓",
    none: "—",
  };

  const icon = iconMap[effectiveStatus] || "⏱";

  return (
    <span
      className={`sla-live-badge sla-${effectiveStatus}`}
      title={`SLA Target: ${dueAt ? new Date(dueAt).toLocaleString() : "Not set"}`}
    >
      {showIcon && <span>{icon}</span>}
      <span>{calc.text}</span>
    </span>
  );
}
