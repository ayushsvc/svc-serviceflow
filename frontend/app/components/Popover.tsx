"use client";

import { KeyboardEvent, ReactNode, RefObject, useCallback, useEffect, useId, useRef, useState } from "react";

const OPEN_EVENT = "svc:popover-open";

type DismissableOptions = {
  id: string;
  open: boolean;
  onClose: () => void;
  containerRef: RefObject<HTMLElement>;
  triggerRef: RefObject<HTMLElement>;
  // Opening one item closes the others in the same scope (menus never close nav groups).
  scope?: string;
  closeOnOutside?: boolean;
};

// Shared by every dropdown, popover and collapsible nav group: outside click, Esc (focus returns
// to the trigger) and "opening one closes the others".
export function useDismissable({ id, open, onClose, containerRef, triggerRef, scope = "menu", closeOnOutside = true }: DismissableOptions) {
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    if (!open) return undefined;
    window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: { id, scope } }));
    const onPointer = (event: PointerEvent) => {
      if (closeOnOutside && containerRef.current && !containerRef.current.contains(event.target as Node)) closeRef.current();
    };
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      closeRef.current();
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, id, scope, closeOnOutside, containerRef, triggerRef]);

  useEffect(() => {
    const onOther = (event: Event) => {
      const detail = (event as CustomEvent<{ id: string; scope: string }>).detail;
      if (detail.id !== id && detail.scope === scope) closeRef.current();
    };
    window.addEventListener(OPEN_EVENT, onOther);
    return () => window.removeEventListener(OPEN_EVENT, onOther);
  }, [id, scope]);
}

type PopoverProps = {
  label: ReactNode | ((open: boolean) => ReactNode);
  ariaLabel?: string;
  triggerClassName?: string;
  panelClassName?: string;
  wrapClassName?: string;
  role?: "menu" | "dialog";
  children: (api: { close: () => void }) => ReactNode;
  onOpenChange?: (open: boolean) => void;
  // Changing this while open moves focus back to the first item (for in-menu sub views).
  focusKey?: string;
};

export function Popover({ label, ariaLabel, triggerClassName, panelClassName, wrapClassName, role = "menu", children, onOpenChange, focusKey }: PopoverProps) {
  const id = useId();
  const panelId = `${id}-panel`;
  const [open, setOpenState] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const setOpen = useCallback((next: boolean) => {
    setOpenState(next);
    onOpenChange?.(next);
  }, [onOpenChange]);
  const close = useCallback(() => setOpen(false), [setOpen]);
  useDismissable({ id, open, onClose: close, containerRef: wrapRef, triggerRef });

  useEffect(() => {
    if (!open || role !== "menu") return;
    const frame = window.requestAnimationFrame(() => panelRef.current?.querySelector<HTMLElement>('[role="menuitem"]:not([disabled])')?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [open, role, focusKey]);

  function onPanelKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (role !== "menu") return;
    const items = Array.from(panelRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not([disabled])') ?? []);
    if (!items.length) return;
    const index = items.indexOf(document.activeElement as HTMLElement);
    const move = (next: number) => { event.preventDefault(); items[(next + items.length) % items.length].focus(); };
    if (event.key === "ArrowDown") move(index + 1);
    else if (event.key === "ArrowUp") move(index < 0 ? items.length - 1 : index - 1);
    else if (event.key === "Home") move(0);
    else if (event.key === "End") move(items.length - 1);
  }

  return (
    <div className={wrapClassName} ref={wrapRef} onBlur={(event) => {
      if (open && !wrapRef.current?.contains(event.relatedTarget as Node | null) && event.relatedTarget) close();
    }}>
      <button
        type="button"
        ref={triggerRef}
        className={triggerClassName}
        aria-label={ariaLabel}
        aria-haspopup={role}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen(!open)}
        onKeyDown={(event) => { if (event.key === "ArrowDown" && !open) { event.preventDefault(); setOpen(true); } }}
      >
        {typeof label === "function" ? label(open) : label}
      </button>
      {open && <div id={panelId} ref={panelRef} role={role} aria-label={ariaLabel} className={panelClassName} onKeyDown={onPanelKeyDown}>
        {children({ close: () => { close(); triggerRef.current?.focus(); } })}
      </div>}
    </div>
  );
}
