"use client";

import { ReactNode, useCallback, useEffect, useRef, useState } from "react";

export type ToastState = { kind: "success" | "error"; text: string; id: number } | null;

export function useToast() {
  const [toast, setToast] = useState<ToastState>(null);
  const timer = useRef<number | null>(null);
  const dismiss = useCallback(() => setToast(null), []);
  const show = useCallback((kind: "success" | "error", text: string) => {
    if (timer.current) window.clearTimeout(timer.current);
    setToast({ kind, text, id: Date.now() });
    timer.current = window.setTimeout(() => setToast(null), kind === "error" ? 8000 : 4000);
  }, []);
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);
  return { toast, show, dismiss };
}

export function ToastRegion({ toast, onDismiss }: { toast: ToastState; onDismiss: () => void }): ReactNode {
  return (
    <div className="toast-region" role={toast?.kind === "error" ? "alert" : "status"} aria-live={toast?.kind === "error" ? "assertive" : "polite"}>
      {toast && <div key={toast.id} className={`toast toast-${toast.kind}`}>
        <span>{toast.text}</span>
        <button type="button" aria-label="Dismiss message" onClick={onDismiss}>×</button>
      </div>}
    </div>
  );
}
