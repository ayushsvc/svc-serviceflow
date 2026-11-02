"use client";

import { KeyboardEvent, ReactNode, useId, useRef } from "react";

export type DrawerTab = "activity" | "details";

const TABS: Array<{ key: DrawerTab; label: string }> = [
  { key: "activity", label: "Activity" },
  { key: "details", label: "Details" },
];

export function DrawerTabs({ tab, onChange }: { tab: DrawerTab; onChange: (tab: DrawerTab) => void }) {
  const id = useId();
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % TABS.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + TABS.length) % TABS.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = TABS.length - 1;
    else return;
    event.preventDefault();
    onChange(TABS[next].key);
    refs.current[TABS[next].key]?.focus();
  }
  return (
    <div className="drawer-tabs" role="tablist" aria-label="Ticket sections">
      {TABS.map((item, index) => <button
        key={item.key}
        type="button"
        role="tab"
        id={`${id}-tab-${item.key}`}
        aria-selected={tab === item.key}
        aria-controls={`${id}-panel-${item.key}`}
        tabIndex={tab === item.key ? 0 : -1}
        ref={(node) => { refs.current[item.key] = node; }}
        className={`tab ${tab === item.key ? "active" : ""}`}
        onClick={() => onChange(item.key)}
        onKeyDown={(event) => onKeyDown(event, index)}
      >{item.label}</button>)}
    </div>
  );
}

export function TabPanel({ tabId, tab, children }: { tabId: DrawerTab; tab: DrawerTab; children: ReactNode }) {
  return <div role="tabpanel" hidden={tab !== tabId} tabIndex={0} aria-label={tabId === "activity" ? "Activity" : "Details"}>{tab === tabId ? children : null}</div>;
}
