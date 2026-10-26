"use client";

import { KeyboardEvent, useEffect, useId, useMemo, useRef, useState } from "react";

type TemplateItem = {
  id: number;
  name: string;
  description: string;
  ticket_type_id: number;
  team: { id: number; name: string } | null;
  is_recent: boolean;
};

type TemplateDetail = {
  id: number;
  name: string;
  prefilled: Record<string, unknown>;
};

type AppliedTemplate = {
  name: string;
  previousValues: Record<string, unknown>;
};

type TemplatePickerProps = {
  token: string;
  ticketTypeId: string;
  currentValues: Record<string, unknown>;
  hasExistingInput: boolean;
  canManageTemplates: boolean;
  onApply: (prefilled: Record<string, unknown>) => void;
  onRestore: (values: Record<string, unknown>) => void;
  onOpenSettings: () => void;
};

export default function TemplatePicker({
  token,
  ticketTypeId,
  currentValues,
  hasExistingInput,
  canManageTemplates,
  onApply,
  onRestore,
  onOpenSettings,
}: TemplatePickerProps) {
  const [templates, setTemplates] = useState<TemplateItem[]>([]);
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [activeIndex, setActiveIndex] = useState(-1);
  const [applied, setApplied] = useState<AppliedTemplate | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = `template-options-${useId().replace(/:/g, "")}`;
  const restoreRef = useRef(onRestore);
  const previousType = useRef(ticketTypeId);

  useEffect(() => {
    restoreRef.current = onRestore;
  }, [onRestore]);

  useEffect(() => {
    if (previousType.current !== ticketTypeId) {
      if (applied) restoreRef.current(applied.previousValues);
      setApplied(null);
      setSearch("");
      setOpen(false);
      setActiveIndex(-1);
      previousType.current = ticketTypeId;
    }
  }, [ticketTypeId, applied]);

  useEffect(() => {
    if (!ticketTypeId) {
      setTemplates([]);
      return;
    }
    const controller = new AbortController();
    const timeout = window.setTimeout(() => {
      const params = new URLSearchParams({
        ticket_type_id: ticketTypeId,
        limit: "50",
      });
      if (search.trim()) params.set("q", search.trim());
      setLoading(true);
      setError("");
      void fetch(`/api/v1/ticket-templates?${params.toString()}`, {
        headers: { Authorization: `Bearer ${token}` },
        signal: controller.signal,
      })
        .then(async (response) => {
          if (!response.ok) throw new Error("Unable to load templates.");
          return response.json() as Promise<TemplateItem[]>;
        })
        .then(setTemplates)
        .catch((fetchError: unknown) => {
          if (fetchError instanceof DOMException && fetchError.name === "AbortError") return;
          setError(fetchError instanceof Error ? fetchError.message : "Unable to load templates.");
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 180);
    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [ticketTypeId, search, token, reloadKey]);

  const groups = useMemo(() => {
    const recent = templates.filter((template) => template.is_recent);
    const recentIds = new Set(recent.map((template) => template.id));
    const team = templates.filter((template) => template.team && !recentIds.has(template.id));
    const teamIds = new Set(team.map((template) => template.id));
    const all = templates.filter(
      (template) => !recentIds.has(template.id) && !teamIds.has(template.id),
    );
    return [
      { label: "Recently used", items: recent },
      { label: "Team templates", items: team },
      { label: "All templates", items: all },
    ].filter((group) => group.items.length > 0);
  }, [templates]);

  const options = useMemo(
    () => [null, ...groups.flatMap((group) => group.items)],
    [groups],
  );

  async function selectTemplate(template: TemplateItem) {
    if (hasExistingInput && !window.confirm("Replace what you've entered?")) {
      setOpen(false);
      inputRef.current?.focus();
      return;
    }
    setError("");
    try {
      const response = await fetch(`/api/v1/ticket-templates/${template.id}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) throw new Error("This template is no longer available. Refresh and try again.");
      const detail = (await response.json()) as TemplateDetail;
      const previousValues = { ...currentValues };
      onApply(detail.prefilled);
      setApplied({ name: detail.name, previousValues });
      setSearch("");
      setOpen(false);
      setActiveIndex(-1);
    } catch (fetchError) {
      setError(fetchError instanceof Error ? fetchError.message : "Unable to apply this template.");
    }
  }

  function clearApplied() {
    if (!applied) return;
    if (!window.confirm("Clear this template and restore the fields you had before applying it?")) return;
    onRestore(applied.previousValues);
    setApplied(null);
    setSearch("");
    setOpen(false);
  }

  function chooseOption(index: number) {
    if (index === 0) {
      clearApplied();
      return;
    }
    const template = options[index];
    if (template) void selectTemplate(template);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      setOpen(false);
      setActiveIndex(-1);
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((index) => Math.min(index + 1, options.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((index) => Math.max(index - 1, 0));
    } else if (event.key === "Enter" && open && activeIndex >= 0) {
      event.preventDefault();
      chooseOption(activeIndex);
    }
  }

  const activeOption = activeIndex >= 0 ? options[activeIndex] : null;

  return (
    <div className="template-picker">
      <label htmlFor="ticket-template-search">Template (optional)</label>
      <input
        ref={inputRef}
        id="ticket-template-search"
        type="search"
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={listId}
        aria-activedescendant={
          activeIndex === 0
            ? `${listId}-blank`
            : activeOption
              ? `${listId}-${activeOption.id}`
              : undefined
        }
        autoComplete="off"
        value={search}
        placeholder={applied ? "Search or choose another template" : "Search templates"}
        onFocus={() => setOpen(true)}
        onChange={(event) => {
          setSearch(event.target.value);
          setOpen(true);
          setActiveIndex(-1);
        }}
        onKeyDown={handleKeyDown}
      />
      {open && (
        <div className="template-option-list" id={listId} role="listbox" aria-label="Ticket templates">
          <button
            id={`${listId}-blank`}
            type="button"
            role="option"
            aria-selected={activeIndex === 0}
            className={`template-option template-blank-option${activeIndex === 0 ? " selected" : ""}`}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => chooseOption(0)}
          >
            No template (start blank)
          </button>
          {loading ? (
            <div className="template-loading" aria-label="Loading templates">
              <span /><span /><span />
            </div>
          ) : error ? (
            <div className="template-picker-message" role="alert">
              <span>{error}</span>
              <button type="button" className="text-button" onClick={() => setReloadKey((key) => key + 1)}>Retry</button>
            </div>
          ) : templates.length === 0 ? (
            <div className="template-picker-message">
              <span>{search.trim() ? "No templates match your search." : "No templates for this ticket type yet."}</span>
              {!search.trim() && canManageTemplates && (
                <button type="button" className="text-button" onClick={onOpenSettings}>
                  Open Settings &gt; Templates
                </button>
              )}
            </div>
          ) : options.length === 1 ? (
            <div className="template-picker-message">No templates match your search.</div>
          ) : (
            groups.map((group) => (
              <div className="template-option-group" role="group" aria-label={group.label} key={group.label}>
                <div className="template-option-group-label">{group.label}</div>
                {group.items.map((template) => {
                  const optionIndex = options.findIndex((option) => option?.id === template.id);
                  return (
                    <button
                      id={`${listId}-${template.id}`}
                      type="button"
                      role="option"
                      aria-selected={activeIndex === optionIndex}
                      className={`template-option${activeIndex === optionIndex ? " selected" : ""}`}
                      key={template.id}
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => void selectTemplate(template)}
                    >
                      <strong>{template.name}</strong>
                      <small>{template.description || "No description provided."}</small>
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>
      )}
      {applied && (
        <div className="template-applied-chip" role="status">
          <span>Template applied: <strong>{applied.name}</strong></span>
          <button type="button" className="clear-button" onClick={clearApplied}>Clear</button>
        </div>
      )}
      {error && !open && <p className="template-picker-inline-error" role="alert">{error}</p>}
    </div>
  );
}
