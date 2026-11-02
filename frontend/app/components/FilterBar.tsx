"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useDismissable } from "./Popover";
import {
  FilterOption, FilterOptions, TicketFilters, activeFilterCount, dateRangeInvalid,
} from "../lib/ticketFilters";

type ListKey = "status" | "priority" | "assignee" | "requester" | "type" | "category";

type Props = {
  filters: TicketFilters;
  options: FilterOptions | null;
  optionsError: string;
  staff: boolean;
  search: string;
  total: number;
  loading: boolean;
  onChange: (next: TicketFilters) => void;
  onClearSearch: () => void;
  onClearAll: () => void;
};

function MultiSelect({ label, options, selected, onChange }: {
  label: string;
  options: FilterOption[];
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const wrapRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const panelId = useId();

  useDismissable({ id: panelId, open, onClose: () => setOpen(false), containerRef: wrapRef, triggerRef: buttonRef });
  useEffect(() => {
    if (open) searchRef.current?.focus();
  }, [open]);

  const visible = options.filter((option) => option.label.toLowerCase().includes(text.trim().toLowerCase()));
  const toggle = (id: string) => onChange(selected.includes(id) ? selected.filter((item) => item !== id) : [...selected, id]);

  return <div className="filter-field" ref={wrapRef} onKeyDown={(event) => {
    if (event.key === "Escape" && open) { event.stopPropagation(); setOpen(false); buttonRef.current?.focus(); }
  }}>
    <button
      type="button"
      ref={buttonRef}
      className={`filter-trigger ${selected.length ? "filter-trigger-active" : ""}`}
      aria-haspopup="listbox"
      aria-expanded={open}
      aria-controls={open ? panelId : undefined}
      onClick={() => { setOpen((value) => !value); setText(""); }}
    >
      <span>{label}</span>{selected.length > 0 && <b>{selected.length}</b>}<i aria-hidden="true">⌄</i>
    </button>
    {open && <div className="filter-panel" id={panelId}>
      {options.length > 6 && <input
        ref={searchRef}
        type="search"
        className="filter-panel-search"
        aria-label={`Search ${label}`}
        placeholder={`Search ${label.toLowerCase()}`}
        value={text}
        onChange={(event) => setText(event.target.value)}
      />}
      <div className="filter-options" role="group" aria-label={label}>
        {visible.map((option) => <label key={option.id} className="filter-option">
          <input type="checkbox" checked={selected.includes(option.id)} onChange={() => toggle(option.id)} />
          <span>{option.label}</span>{option.hint && <small>{option.hint}</small>}
        </label>)}
        {!visible.length && <p className="filter-empty">{options.length ? "No matches" : "No options available"}</p>}
      </div>
      {selected.length > 0 && <button type="button" className="clear-button" onClick={() => onChange([])}>Clear {label}</button>}
    </div>}
  </div>;
}

export default function FilterBar({ filters, options, optionsError, staff, search, total, loading, onChange, onClearSearch, onClearAll }: Props) {
  const count = activeFilterCount(filters) + (search.trim() ? 1 : 0);
  const rangeInvalid = dateRangeInvalid(filters);
  const empty: FilterOptions = { statuses: [], priorities: [], assignees: [], requesters: [], types: [], categories: [] };
  const opts = options ?? empty;
  const set = (key: ListKey) => (next: string[]) => onChange({ ...filters, [key]: next });
  const labelFor = (list: FilterOption[], id: string) => list.find((option) => option.id === id)?.label ?? (list === opts.statuses || list === opts.priorities ? id : `#${id}`);

  const chips: Array<{ key: string; text: string; remove: () => void }> = [];
  if (search.trim()) chips.push({ key: "search", text: `Search: ${search.trim()}`, remove: onClearSearch });
  const addChips = (key: ListKey, name: string, list: FilterOption[]) => filters[key].forEach((id) => chips.push({
    key: `${key}:${id}`,
    text: `${name}: ${labelFor(list, id)}`,
    remove: () => onChange({ ...filters, [key]: filters[key].filter((item) => item !== id) }),
  }));
  addChips("status", "Status", opts.statuses);
  addChips("priority", "Priority", opts.priorities);
  if (staff) {
    addChips("assignee", "Assignee", opts.assignees);
    addChips("requester", "Requester", opts.requesters);
    addChips("type", "Type", opts.types);
    addChips("category", "Category", opts.categories);
  }
  const basisName = filters.basis === "updated" ? "Updated" : "Created";
  if (filters.from) chips.push({ key: "from", text: `${basisName} from ${filters.from}`, remove: () => onChange({ ...filters, from: "" }) });
  if (filters.to) chips.push({ key: "to", text: `${basisName} to ${filters.to}`, remove: () => onChange({ ...filters, to: "" }) });

  return <section className="filter-bar" aria-label="Ticket filters">
    <div className="filter-row">
      <MultiSelect label="Status" options={opts.statuses} selected={filters.status} onChange={set("status")} />
      <MultiSelect label="Priority" options={opts.priorities} selected={filters.priority} onChange={set("priority")} />
      {staff && <>
        <MultiSelect label="Assignee" options={opts.assignees} selected={filters.assignee} onChange={set("assignee")} />
        <MultiSelect label="Requester" options={opts.requesters} selected={filters.requester} onChange={set("requester")} />
        <MultiSelect label="Ticket type" options={opts.types} selected={filters.type} onChange={set("type")} />
        <MultiSelect label="Category" options={opts.categories} selected={filters.category} onChange={set("category")} />
      </>}
    </div>
    <fieldset className="filter-dates">
      <legend>Date range</legend>
      {staff && <div className="filter-basis" role="radiogroup" aria-label="Date basis">
        {(["created", "updated"] as const).map((basis) => <label key={basis} className={filters.basis === basis ? "filter-basis-active" : ""}>
          <input type="radio" name="filter-date-basis" checked={filters.basis === basis} onChange={() => onChange({ ...filters, basis })} />
          {basis === "created" ? "Created" : "Last updated"}
        </label>)}
      </div>}
      <label>From<input type="date" value={filters.from} max={filters.to || undefined} onChange={(event) => onChange({ ...filters, from: event.target.value })} aria-invalid={rangeInvalid} /></label>
      <label>To<input type="date" value={filters.to} min={filters.from || undefined} onChange={(event) => onChange({ ...filters, to: event.target.value })} aria-invalid={rangeInvalid} /></label>
    </fieldset>
    {rangeInvalid && <p className="error" role="alert">The start date must be on or before the end date. The date range is not applied.</p>}
    {optionsError && <p className="error" role="alert">{optionsError}</p>}
    <div className="filter-summary">
      <p className="filter-count" role="status" aria-live="polite" aria-atomic="true">
        {loading ? "Loading tickets…" : `${total} ${total === 1 ? "ticket" : "tickets"} found${count ? " with the current filters" : ""}`}
      </p>
      {chips.length > 0 && <ul className="filter-chips" aria-label="Active filters">
        {chips.map((chip) => <li key={chip.key}>
          <span>{chip.text}</span>
          <button type="button" aria-label={`Remove filter ${chip.text}`} onClick={chip.remove}>×</button>
        </li>)}
      </ul>}
      {count > 0 && <button type="button" className="clear-button" onClick={onClearAll}>Clear all</button>}
    </div>
  </section>;
}
