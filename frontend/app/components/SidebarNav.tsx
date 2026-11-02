"use client";

import { ReactNode, useEffect, useId, useRef, useState } from "react";
import { useDismissable } from "./Popover";

export type NavChild = { key: string; label: string; count?: number; active: boolean; onSelect: () => void };
export type NavEntry = { icon: string; label: string; badge?: number; children?: NavChild[] };

type Props = {
  items: NavEntry[];
  activeNav: string;
  renderIcon: (name: string) => ReactNode;
  onNavigate: (label: string) => void;
};

function NavGroup({ entry, activeNav, expanded, onToggle, renderIcon, onNavigate }: {
  entry: NavEntry;
  activeNav: string;
  expanded: boolean;
  onToggle: (open: boolean) => void;
  renderIcon: (name: string) => ReactNode;
  onNavigate: (label: string) => void;
}) {
  const id = useId();
  const wrapRef = useRef<HTMLLIElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  // Esc collapses the section; clicking elsewhere must not collapse the navigation.
  useDismissable({ id, scope: "nav", open: expanded, onClose: () => onToggle(false), containerRef: wrapRef, triggerRef, closeOnOutside: false });
  const listId = `${id}-items`;
  return (
    <li ref={wrapRef} className="nav-group">
      <button
        type="button"
        ref={triggerRef}
        className={`nav-item ${activeNav === entry.label ? "active" : ""}`}
        aria-expanded={expanded}
        aria-controls={expanded ? listId : undefined}
        onClick={() => {
          if (!expanded) onNavigate(entry.label);
          onToggle(!expanded);
        }}
      >
        {renderIcon(entry.icon)}<span>{entry.label}</span>
        {entry.badge !== undefined && <b>{entry.badge}</b>}
        <i className={`nav-chevron ${expanded ? "nav-chevron-open" : ""}`} aria-hidden="true">⌄</i>
      </button>
      {expanded && <ul id={listId} className="nav-subitems">
        {entry.children!.map((child) => <li key={child.key}>
          <button
            type="button"
            className={`nav-subitem ${activeNav === entry.label && child.active ? "active" : ""}`}
            aria-current={activeNav === entry.label && child.active ? "true" : undefined}
            onClick={() => { onNavigate(entry.label); child.onSelect(); }}
          >
            <span>{child.label}</span>{child.count !== undefined && <b>{child.count}</b>}
          </button>
        </li>)}
      </ul>}
    </li>
  );
}

export default function SidebarNav({ items, activeNav, renderIcon, onNavigate }: Props) {
  const [expandedGroup, setExpandedGroup] = useState<string | null>(null);
  useEffect(() => {
    // A group whose children disappeared (for example after the rights change) cannot stay open.
    const entry = items.find((item) => item.label === expandedGroup);
    if (expandedGroup && !entry?.children?.length) setExpandedGroup(null);
  }, [items, expandedGroup]);
  return (
    <nav className="nav-list" aria-label="Main navigation">
      <ul className="nav-list-items">
        {items.map((entry) => entry.children?.length
          ? <NavGroup
            key={entry.label}
            entry={entry}
            activeNav={activeNav}
            expanded={expandedGroup === entry.label}
            onToggle={(open) => setExpandedGroup((current) => (open ? entry.label : current === entry.label ? null : current))}
            renderIcon={renderIcon}
            onNavigate={onNavigate}
          />
          : <li key={entry.label}>
            <button type="button" className={`nav-item ${activeNav === entry.label ? "active" : ""}`} aria-current={activeNav === entry.label ? "page" : undefined} onClick={() => onNavigate(entry.label)}>
              {renderIcon(entry.icon)}<span>{entry.label}</span>{entry.badge !== undefined && <b>{entry.badge}</b>}
            </button>
          </li>)}
      </ul>
    </nav>
  );
}
