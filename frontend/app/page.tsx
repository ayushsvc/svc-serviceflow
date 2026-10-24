"use client";

import { ChangeEvent, FormEvent, useEffect, useMemo, useState } from "react";

type TokenPair = { access_token: string; refresh_token: string };
type TicketListColumn = "id" | "title" | "status" | "priority" | "opened_at" | "requester_name" | "requester_active" | "requester_vip" | "flagged" | "assignee_id" | "ttr_due_at";
type SavedListProfile = { id: number; name: string; kind: "filter" | "column"; config: Record<string, unknown>; is_default: boolean };
const ticketListColumns: Array<{ key: TicketListColumn; label: string }> = [
  { key: "id", label: "ID" },
  { key: "title", label: "Ticket" },
  { key: "status", label: "Status" },
  { key: "priority", label: "Priority" },
  { key: "opened_at", label: "Opened" },
  { key: "requester_name", label: "Requester" },
  { key: "requester_active", label: "Requester status" },
  { key: "requester_vip", label: "VIP" },
  { key: "flagged", label: "Flagged" },
  { key: "assignee_id", label: "Assignee" },
  { key: "ttr_due_at", label: "SLA due" },
];
const defaultTicketListColumns: TicketListColumn[] = ["title", "status", "priority", "assignee_id", "ttr_due_at"];
type Ticket = {
  id: number;
  title: string;
  description: string;
  status: string;
  priority: number;
  urgency?: number;
  impact?: number;
  opened_at: string;
  ttr_due_at?: string | null;
  assignee_id?: number | null;
  category_id?: number | null;
  requester_name?: string | null;
  requester_active?: boolean | null;
  requester_vip?: boolean | null;
  flagged?: boolean;
};
type TimelineItem = { kind: string; body: string; created_at: string; state?: string; is_private?: boolean };
type CurrentUser = { id: number; name: string; email: string; profile: { name: string; interface: string; rights?: Record<string, boolean> } };
type Requester = { id: number; name: string; email: string };
type Category = { id: number; name: string };
type Group = { id: number; name: string };
type TicketType = { id: number; name: string; itil_class: string; fields: Array<{ field_definition_id: number; key?: string; required: boolean; page: string }> };
type FormField = { key: string; label: string; type: string; options: Record<string, unknown>; validation: Record<string, unknown>; required: boolean; editable: boolean; default_value: unknown; page: string; show_on_portal: boolean; show_condition: Record<string, unknown> };
type FormSchema = { ticket_type_id: number; name: string; pages: Record<string, FormField[]> };
type TicketTemplate = { id: number; name: string; ticket_type_id: number; prefilled: Record<string, unknown> };
type AssignmentPreview = { team_name: string | null; agent_name: string | null; priority: number; sla: { sla_name: string | null; response_target: string | null; fix_target: string | null } };
type TicketWorkflow = { id?: number; entity_id?: number; record_type?: string; states: string[]; transitions: Record<string, string[]>; approval_requirements: Record<string, number>; is_active: boolean };
type WorkflowApproval = { id: number; record_type: string; record_id: number; transition_to: string; step: number; status: string; note: string | null; created_at: string };

const statusLabels: Record<string, string> = {
  NEW: "New",
  ASSIGNED: "Assigned",
  PLANNED: "Planned",
  PENDING: "Pending",
  SOLVED: "Solved",
  CLOSED: "Closed",
};

function Icon({ name }: { name: string }) {
  const paths: Record<string, string> = {
    grid: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z",
    ticket: "M20 12a2 2 0 0 0 0-4V5H4v3a2 2 0 0 0 0 4 2 2 0 0 0 0 4v3h16v-3a2 2 0 0 0 0-4Z",
    users: "M16 20v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1M9.5 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7ZM17 11a3 3 0 0 0 0-6M19 15a4 4 0 0 1 3 4v1",
    book: "M4 4.5A2.5 2.5 0 0 1 6.5 2H20v17H6.5A2.5 2.5 0 0 0 4 21.5v-17ZM4 18h16",
    chart: "M4 19V5M4 19h17M8 16v-3M12 16V8M16 16v-6M20 16v-9",
    settings: "M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7ZM19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-1.7 1.7-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5v.2h-2.4v-.2a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1L8 17l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H6.7v-2.4h.2a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.9L8 8.6l1.7-1.7.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.5v-.2h2.4v.2a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1 1.7 1.7-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.5 1h.2v2.4h-.2a1.7 1.7 0 0 0-1.5 1Z",
    search: "m20 20-4.4-4.4M17 11a6 6 0 1 1-12 0 6 6 0 0 1 12 0Z",
    bell: "M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9ZM10 21h4",
    close: "m6 6 12 12M18 6 6 18",
    arrow: "M5 12h14m-6-6 6 6-6 6",
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24" className="icon"><path d={paths[name]} /></svg>;
}
export default function Home() {
  const [email, setEmail] = useState("admin@svcinfosys.in");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [token, setToken] = useState<TokenPair | null>(null);

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("");
    const response = await fetch("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!response.ok) {
      setMessage("Invalid email or password.");
      return;
    }
    setToken((await response.json()) as TokenPair);
  }

  if (token) return <TicketWorkspace token={token} onSignOut={() => { Object.keys(window.localStorage).filter((key) => key.startsWith("svc-ticket-draft:")).forEach((key) => window.localStorage.removeItem(key)); setToken(null); }} />;

  return (
    <main className="login-page">
      <section className="login-panel">
        <div className="login-brand"><img src="/svc-logo.png" alt="SVC Infosystems" /><span>SVC ServiceFlow</span></div>
        <div className="login-copy"><p className="kicker">SERVICE OPERATIONS</p><h1>Request to Resolution</h1><p>One focused workspace for employees, technicians, and IT leadership.</p></div>
        <div className="login-footer"><span>Request to Resolution</span><span>v1.0</span></div>
      </section>
      <section className="login-form-wrap">
        <form className="login-form" onSubmit={login}>
          <div className="mobile-brand"><img src="/svc-logo.png" alt="SVC Infosystems" /><strong>SVC ServiceFlow</strong></div>
          <p className="kicker">WELCOME BACK</p><h2>Sign in to your workspace</h2><p className="form-help">Use your work account to continue.</p>
          <label>Work email<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} required autoComplete="email" /></label>
          <label>Password<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} required autoComplete="current-password" /></label>
          <div className="form-options"><label className="checkbox"><input type="checkbox" /> Remember me</label><button type="button" className="text-button">Forgot password?</button></div>
          <button className="primary-button login-button" type="submit">Sign in <Icon name="arrow" /></button>
          {message && <p className="error">{message}</p>}
        </form>
      </section>
    </main>
  );
}

function DynamicFields({ schema, values, onChange, portal }: { schema: FormSchema; values: Record<string, unknown>; onChange: (key: string, value: unknown) => void; portal: boolean }) {
  const allFields = Object.values(schema.pages).flat();
  const visible = (field: FormField) => {
    const condition = field.show_condition;
    if (!condition || Object.keys(condition).length === 0) return true;
    const actual = values[String(condition.field)];
    if (condition.op === "eq") return actual === condition.value;
    if (condition.op === "in" && Array.isArray(condition.value)) return condition.value.includes(actual);
    return true;
  };
  const skipped = new Set(["summary", "details", "user", "category", "urgency", "impact", "priority", "team", "agent"]);
  return <div className="form-grid">{allFields.filter((field) => (!portal || field.show_on_portal) && field.editable && !skipped.has(field.key) && visible(field)).map((field) => {
    const value = values[field.key] ?? "";
    const choices = Array.isArray(field.options.choices) ? field.options.choices as string[] : [];
    const common = { required: field.required, value: Array.isArray(value) ? value.join(", ") : String(value), onChange: (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => onChange(field.key, field.type === "number" ? Number(event.target.value) : event.target.value) };
    if (field.type === "textarea" || field.type === "richtext") return <label key={field.key}>{field.label}<textarea {...common} /></label>;
    if (field.type === "select") return <label key={field.key}>{field.label}<select {...common}><option value="">Select {field.label.toLowerCase()}</option>{choices.map((choice) => <option key={choice} value={choice}>{choice}</option>)}</select></label>;
    if (field.type === "checkbox") return <label className="checkbox" key={field.key}><input type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(field.key, event.target.checked)} /> {field.label}</label>;
    return <label key={field.key}>{field.label}<input type={field.type === "number" ? "number" : field.type === "date" ? "date" : field.type === "datetime" ? "datetime-local" : "text"} {...common} /></label>;
  })}</div>;
}

function TicketWorkspace({ token, onSignOut }: { token: TokenPair; onSignOut: () => void }) {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [selected, setSelected] = useState<Ticket | null>(null);
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [followup, setFollowup] = useState("");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("All tickets");
  const [priorityFilter, setPriorityFilter] = useState("All priorities");
  const [filterOpen, setFilterOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [activeNav, setActiveNav] = useState("Tickets");
  const [showDrawer, setShowDrawer] = useState(true);
  const [error, setError] = useState("");
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [requesters, setRequesters] = useState<Requester[]>([]);
  const [requesterSearch, setRequesterSearch] = useState("");
  const [selectedRequesterId, setSelectedRequesterId] = useState<number | null>(null);
  const [agents, setAgents] = useState<Requester[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [ticketType, setTicketType] = useState("INCIDENT");
  const [ticketTypes, setTicketTypes] = useState<TicketType[]>([]);
  const [formSchema, setFormSchema] = useState<FormSchema | null>(null);
  const [templates, setTemplates] = useState<TicketTemplate[]>([]);
  const [assignmentPreview, setAssignmentPreview] = useState<AssignmentPreview | null>(null);
  const [submitAnother, setSubmitAnother] = useState(false);
  const [draftSaved, setDraftSaved] = useState(false);
  const [impact, setImpact] = useState("3");
  const [urgency, setUrgency] = useState("3");
  const [categoryId, setCategoryId] = useState("");
  const [assigneeId, setAssigneeId] = useState("");
  const [customFields, setCustomFields] = useState<Record<string, unknown>>({});
  const [showCreate, setShowCreate] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [newFiles, setNewFiles] = useState<File[]>([]);
  const [uploadMessage, setUploadMessage] = useState("");
  const [createError, setCreateError] = useState("");
  const [workspaceMenuOpen, setWorkspaceMenuOpen] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [listPage, setListPage] = useState(1);
  const [listPageSize, setListPageSize] = useState(25);
  const [listTotal, setListTotal] = useState(0);
  const [listSort, setListSort] = useState("opened_at");
  const [listDirection, setListDirection] = useState<"asc" | "desc">("desc");
  const [listProfiles, setListProfiles] = useState<SavedListProfile[]>([]);
  const [activeListProfileId, setActiveListProfileId] = useState("");
  const [profileSaveKind, setProfileSaveKind] = useState<"filter" | "column">("filter");
  const [profileName, setProfileName] = useState("");
  const [listColumns, setListColumns] = useState<TicketListColumn[]>(defaultTicketListColumns);
  const [teamCounts, setTeamCounts] = useState<Array<{ team_id: number | null; count: number }>>([]);
  const [queueFilter, setQueueFilter] = useState<"all" | "mine" | "teams" | "unassigned">("all");
  const [queueCounts, setQueueCounts] = useState({ all: 0, mine: 0, teams: 0, unassigned: 0 });
  const queueAccess = Boolean(currentUser?.profile.rights?.["ticket:view_queue"] && !currentUser?.profile.rights?.["ticket:view_all"] && !currentUser?.profile.rights?.["*"]);
  const draftKey = currentUser ? `svc-ticket-draft:${currentUser.id}` : null;

  const apiHeaders = { Authorization: `Bearer ${token.access_token}` };
  async function loadTickets() {
    const params = new URLSearchParams({
      page: String(listPage),
      page_size: String(listPageSize),
      sort: listSort,
      direction: listDirection,
    });
    if (queueAccess && queueFilter !== "all") params.set("queue", queueFilter);
    if (query.trim()) params.set("search", query.trim());
    if (statusFilter !== "All tickets") params.set("status", statusFilter);
    if (priorityFilter !== "All priorities") params.set("priority", priorityFilter);
    const response = await fetch(`/api/v1/tickets?${params.toString()}`, { headers: apiHeaders });
    if (!response.ok) { setError("Unable to load tickets."); return; }
    const result = (await response.json()) as { items: Ticket[]; total: number };
    setTickets(result.items);
    setListTotal(result.total);
    if (queueAccess) {
      const queueResponse = await fetch("/api/v1/tickets/queue-counts", { headers: apiHeaders });
      if (queueResponse.ok) setQueueCounts(await queueResponse.json() as typeof queueCounts);
    }
    const countsResponse = await fetch("/api/v1/tickets/team-counts", { headers: apiHeaders });
    if (countsResponse.ok) setTeamCounts((await countsResponse.json()) as Array<{ team_id: number | null; count: number }>);
  }
  function applyListProfile(profile: SavedListProfile | undefined) {
    setActiveListProfileId(profile ? String(profile.id) : "");
    if (!profile) return;
    if (profile.kind === "column") {
      const columns = profile.config.columns;
      if (Array.isArray(columns)) setListColumns(columns as TicketListColumn[]);
      return;
    }
    const config = profile.config;
    setQuery(typeof config.search === "string" ? config.search : "");
    setStatusFilter(typeof config.status === "string" ? config.status : "All tickets");
    setPriorityFilter(typeof config.priority === "number" ? String(config.priority) : "All priorities");
    setListSort(typeof config.sort === "string" ? config.sort : "opened_at");
    setListDirection(config.direction === "asc" ? "asc" : "desc");
    setListPageSize(typeof config.page_size === "number" ? config.page_size : 25);
    setListPage(1);
  }

  async function saveListProfile() {
    if (!profileName.trim()) { setError("Enter a name for the saved profile."); return; }
    const config = profileSaveKind === "column"
      ? { columns: listColumns }
      : {
        ...(query.trim() ? { search: query.trim() } : {}),
        ...(statusFilter !== "All tickets" ? { status: statusFilter } : {}),
        ...(priorityFilter !== "All priorities" ? { priority: Number(priorityFilter) } : {}),
        sort: listSort,
        direction: listDirection,
        page_size: listPageSize,
      };
    const response = await fetch("/api/v1/tickets/list-profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...apiHeaders },
      body: JSON.stringify({ name: profileName.trim(), kind: profileSaveKind, config }),
    });
    if (!response.ok) { setError(response.status === 409 ? "A profile with that name already exists." : "Unable to save this profile."); return; }
    const saved = (await response.json()) as SavedListProfile;
    setListProfiles((profiles) => [...profiles.filter((profile) => profile.id !== saved.id), saved]);
    setProfileName("");
    applyListProfile(saved);
    setError("");
  }

  async function setDefaultListProfile() {
    const profile = listProfiles.find((item) => String(item.id) === activeListProfileId);
    if (!profile) return;
    const response = await fetch(`/api/v1/tickets/list-profiles/${profile.id}/set-default`, {
      method: "POST",
      headers: apiHeaders,
    });
    if (!response.ok) { setError("Unable to set this profile as the default."); return; }
    const updated = (await response.json()) as SavedListProfile;
    setListProfiles((profiles) => profiles.map((item) => item.kind === updated.kind ? { ...item, is_default: item.id === updated.id } : item));
    setError("");
  }

  async function selectTicket(ticket: Ticket) {
    setSelected(ticket);
    setShowDrawer(true);
    const response = await fetch(`/api/v1/tickets/${ticket.id}/timeline`, { headers: apiHeaders });
    if (response.ok) setTimeline((await response.json()) as TimelineItem[]);
  }
  async function claimTicket(ticket: Ticket) {
    const previousTickets = tickets;
    const previousSelected = selected;
    setTickets((items) => items.map((item) => item.id === ticket.id ? { ...item, assignee_id: currentUser?.id ?? null, status: item.status === "NEW" ? "ASSIGNED" : item.status } : item));
    if (selected?.id === ticket.id) setSelected({ ...selected, assignee_id: currentUser?.id ?? null, status: selected.status === "NEW" ? "ASSIGNED" : selected.status });
    const response = await fetch(`/api/v1/tickets/${ticket.id}/claim`, { method: "POST", headers: apiHeaders });
    if (!response.ok) {
      setTickets(previousTickets);
      setSelected(previousSelected);
      setError(response.status === 409 ? "Another technician claimed this ticket first. The queue has been refreshed." : "Unable to claim this ticket.");
      await loadTickets();
      return;
    }
    const claimed = await response.json() as Ticket;
    setTickets((items) => items.map((item) => item.id === claimed.id ? claimed : item));
    if (selected?.id === claimed.id) setSelected(claimed);
    setError("");
    await loadTickets();
  }
  async function addFollowup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected || !followup.trim()) return;
    const response = await fetch(`/api/v1/tickets/${selected.id}/followups`, {
      method: "POST", headers: { "Content-Type": "application/json", ...apiHeaders },
      body: JSON.stringify({ body: followup, is_private: false }),
    });
    if (response.ok) { setFollowup(""); await selectTicket(selected); }
  }
  useEffect(() => { void loadTickets(); }, [query, statusFilter, priorityFilter, listPage, listPageSize, listSort, listDirection, queueFilter, queueAccess]);
  useEffect(() => {
    void fetch("/api/v1/auth/me", { headers: apiHeaders }).then((response) => response.ok ? response.json() : null).then((user: CurrentUser | null) => setCurrentUser(user));
  }, []);
  useEffect(() => {
    if (!currentUser) return;
    void fetch("/api/v1/tickets/list-profiles", { headers: apiHeaders })
      .then((response) => {
        if (!response.ok) { setError("Unable to load saved ticket profiles."); return null; }
        return response.json() as Promise<SavedListProfile[]>;
      })
      .then((loaded) => {
        if (!loaded) return;
        setListProfiles(loaded);
        const defaultFilter = loaded.find((profile) => profile.kind === "filter" && profile.is_default);
        const defaultColumns = loaded.find((profile) => profile.kind === "column" && profile.is_default);
        if (defaultFilter) applyListProfile(defaultFilter);
        if (defaultColumns && Array.isArray(defaultColumns.config.columns)) setListColumns(defaultColumns.config.columns as TicketListColumn[]);
      }, () => setError("Unable to load saved ticket profiles."));
    if (currentUser.profile.interface === "helpdesk") {
      void fetch("/api/v1/requesters", { headers: apiHeaders })
        .then((response) => response.ok ? response.json() : [])
        .then((users: Requester[]) => setRequesters(users));
    }
    void Promise.all([
      fetch("/api/v1/categories", { headers: apiHeaders }).then((response) => response.ok ? response.json() : []),
      fetch("/api/v1/groups", { headers: apiHeaders }).then((response) => response.ok ? response.json() : []),
    ]).then(([categoryList, groupList]: [Category[], Group[]]) => {
      setCategories(categoryList); setGroups(groupList);
    });
    if (currentUser.profile.interface === "helpdesk") {
      void fetch("/api/v1/agents", { headers: apiHeaders })
        .then((response) => response.ok ? response.json() : [])
        .then((agentList: Requester[]) => setAgents(agentList));
    }
    void fetch("/api/v1/ticket-types", { headers: apiHeaders })
      .then((response) => {
        if (!response.ok) {
          setCreateError("Unable to load ticket types. Please try again or contact support.");
          return [];
        }
        return response.json() as Promise<TicketType[]>;
      })
      .then((types: TicketType[]) => {
        setTicketTypes(types);
        const incident = types.find((item) => item.itil_class === "INCIDENT") ?? types[0];
        if (incident) setTicketType(String(incident.id));
      });
    void fetch("/api/v1/templates", { headers: apiHeaders })
      .then((response) => response.ok ? response.json() : [])
      .then((loaded: TicketTemplate[]) => setTemplates(loaded));
  }, [currentUser]);
  useEffect(() => {
    if (!ticketType) return;
    void fetch(`/api/v1/ticket-types/${ticketType}/form`, { headers: apiHeaders })
      .then((response) => response.ok ? response.json() : null)
      .then((schema: FormSchema | null) => {
        setFormSchema(schema);
        if (schema) {
          const defaults = Object.fromEntries(Object.values(schema.pages).flat().filter((field) => field.default_value !== null).map((field) => [field.key, field.default_value]));
          setCustomFields((current) => ({ ...defaults, ...current }));
        }
      });
  }, [ticketType]);
  useEffect(() => {
    if (!ticketType || !showCreate) return;
    const params = new URLSearchParams({ ticket_type_id: ticketType, urgency, impact });
    void fetch(`/api/v1/tickets/assignment-preview?${params.toString()}`, { headers: apiHeaders })
      .then((response) => response.ok ? response.json() : null)
      .then((preview: AssignmentPreview | null) => setAssignmentPreview(preview));
  }, [ticketType, urgency, impact, showCreate]);
  useEffect(() => {
    if (showCreate) {
      const saved = draftKey ? window.localStorage.getItem(draftKey) : null;
      if (saved) {
        try {
          const draft = JSON.parse(saved) as { savedAt?: number; title?: string; description?: string; type?: string; fields?: Record<string, unknown> };
          if (!draft.savedAt || Date.now() - draft.savedAt > 7 * 24 * 60 * 60 * 1000) {
            window.localStorage.removeItem(draftKey!);
          } else {
            setNewTitle(draft.title ?? ""); setNewDescription(draft.description ?? ""); setTicketType(draft.type ?? ticketType); setCustomFields(draft.fields ?? {});
            setDraftSaved(true);
          }
        } catch { window.localStorage.removeItem(draftKey!); }
      }
    }
  }, [showCreate, draftKey]);
  function saveDraft() {
    if (!draftKey) return;
    window.localStorage.setItem(draftKey, JSON.stringify({ savedAt: Date.now(), title: newTitle, description: newDescription, type: ticketType, fields: customFields }));
    setDraftSaved(true);
    setCreateError("Draft saved on this device.");
  }
  function applyTemplate(templateId: string) {
    const template = templates.find((item) => String(item.id) === templateId);
    if (template) setCustomFields((current) => ({ ...current, ...template.prefilled }));
  }

  async function createTicket(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCreateError("");
    setUploadMessage("");
    const response = await fetch("/api/v1/tickets", {
      method: "POST", headers: { "Content-Type": "application/json", ...apiHeaders },
      body: JSON.stringify({ title: newTitle, description: newDescription, type: ticketTypes.find((item) => String(item.id) === ticketType)?.itil_class ?? "INCIDENT", ticket_type_id: Number(ticketType), urgency: Number(urgency), impact: Number(impact), category_id: categoryId ? Number(categoryId) : null, ...(selectedRequesterId ? { requester_id: selectedRequesterId } : {}), ...(assigneeId ? { assignee_id: Number(assigneeId) } : {}), custom_fields: { ...customFields, summary: newTitle, details: newDescription, user: selectedRequesterId ?? currentUser?.id, category: categoryId ? Number(categoryId) : undefined, urgency: Number(urgency), impact: Number(impact), agent: assigneeId ? Number(assigneeId) : undefined } }),
    });
    if (!response.ok) {
      setCreateError("Enter a title and a description before submitting.");
      return;
    }
    if (draftKey) window.localStorage.removeItem(draftKey);
    const ticket = (await response.json()) as Ticket;
    let failedUploads = 0;
    for (const file of newFiles) {
      const formData = new FormData();
      formData.append("document", file);
      const uploadResponse = await fetch(`/api/v1/tickets/${ticket.id}/documents`, {
        method: "POST", headers: apiHeaders, body: formData,
      });
      if (!uploadResponse.ok) failedUploads += 1;
    }
    setNewTitle(""); setNewDescription(""); setNewFiles([]); setRequesterSearch(""); setSelectedRequesterId(null); setImpact("3"); setUrgency("3"); setCategoryId(""); setAssigneeId(""); setCustomFields({});
    setUploadMessage(failedUploads ? `${failedUploads} attachment${failedUploads === 1 ? "" : "s"} could not be uploaded.` : "");
    if (submitAnother) {
      setNewTitle(""); setNewDescription(""); setCustomFields({}); setNewFiles([]); setSubmitAnother(false);
    } else setShowCreate(false);
    await loadTickets();
    await selectTicket(ticket);
  }

  const filteredTickets = useMemo(() => tickets.filter((ticket) => {
    const matchesQuery = `${ticket.id} ${ticket.title} ${ticket.description}`.toLowerCase().includes(query.toLowerCase());
    const matchesStatus = statusFilter === "All tickets" || ticket.status === statusFilter;
    const matchesPriority = priorityFilter === "All priorities" || String(ticket.priority) === priorityFilter;
    return matchesQuery && matchesStatus && matchesPriority;
  }), [tickets, query, statusFilter, priorityFilter]);
  const activeFilterCount = (query.trim() ? 1 : 0) + (statusFilter !== "All tickets" ? 1 : 0) + (priorityFilter !== "All priorities" ? 1 : 0);
  function renderTicketColumn(ticket: Ticket, column: TicketListColumn) {
    if (column === "title") return <div className="ticket-cell"><strong>#{ticket.id} {ticket.requester_vip ? "★ " : ""}{ticket.flagged ? "⚑ " : ""}{ticket.requester_active === false ? "⚠ " : ""}{ticket.title}</strong><span>{ticket.description.slice(0, 62)}{ticket.description.length > 62 ? "…" : ""}</span></div>;
    if (column === "id") return `#${ticket.id}`;
    if (column === "status") return <span className={`status status-${ticket.status.toLowerCase()}`}><i />{statusLabels[ticket.status] ?? ticket.status}</span>;
    if (column === "priority") return <span className={`priority priority-${ticket.priority}`}>{ticket.priority <= 2 ? "Low" : ticket.priority === 3 ? "Medium" : "High"} <b>{ticket.priority}</b></span>;
    if (column === "opened_at") return new Date(ticket.opened_at).toLocaleDateString();
    if (column === "requester_name") return ticket.requester_name ?? "—";
    if (column === "requester_active") return ticket.requester_active == null ? "—" : ticket.requester_active ? "Active" : "Inactive";
    if (column === "requester_vip") return ticket.requester_vip ? "★ VIP" : "—";
    if (column === "flagged") return ticket.flagged ? "Flagged" : "—";
    if (column === "assignee_id") return <span className="assignee"><span className="avatar avatar-orange">AT</span>{ticket.assignee_id ? "Assigned" : "Unassigned"}</span>;
    if (column === "ttr_due_at") return <span className={`sla ${ticket.ttr_due_at && new Date(ticket.ttr_due_at) < new Date() ? "sla-breach" : ""}`}>{ticket.ttr_due_at ? formatDue(ticket.ttr_due_at) : "Not set"}</span>;
    return null;
  }
  function clearFilters() {
    setQuery("");
    setStatusFilter("All tickets");
    setPriorityFilter("All priorities");
    setFilterOpen(false);
    setMoreOpen(false);
  }
  const openCount = tickets.filter((ticket) => !["SOLVED", "CLOSED"].includes(ticket.status)).length;
  const resolvedCount = tickets.filter((ticket) => ["SOLVED", "CLOSED"].includes(ticket.status)).length;
  const highPriorityCount = tickets.filter((ticket) => ticket.priority >= 4).length;

  return <div className="app-shell">
    {mobileNavOpen && <button className="mobile-nav-overlay" aria-label="Close navigation" onClick={() => setMobileNavOpen(false)} />}
    <aside className={`sidebar ${mobileNavOpen ? "sidebar-mobile-open" : ""}`}>
      <div className="sidebar-logo"><img src="/svc-logo.png" alt="SVC Infosystems" /><span>SVC ServiceFlow</span></div>
      <div className="workspace-switcher-wrap">
        <button className="workspace-switcher" onClick={() => setWorkspaceMenuOpen((open) => !open)} aria-expanded={workspaceMenuOpen} aria-haspopup="menu">
          <span className="avatar avatar-indigo">SI</span><span className="workspace-name">Service desk <small>{currentUser?.profile.name ?? "Operations"}</small></span><span className={`chevron ${workspaceMenuOpen ? "chevron-open" : ""}`}>⌄</span>
        </button>
        {workspaceMenuOpen && <div className="workspace-menu" role="menu">
          <div className="workspace-menu-heading"><span className="avatar avatar-indigo">SI</span><span><strong>Service desk</strong><small>{currentUser?.email ?? "Operations workspace"}</small></span><span className="menu-check">✓</span></div>
          <div className="workspace-menu-divider" />
          <button role="menuitem" onClick={() => { setActiveNav("Overview"); setWorkspaceMenuOpen(false); }}><Icon name="grid" /><span>Workspace overview</span></button>
          {currentUser?.profile.name === "Administrator" && <button role="menuitem" onClick={() => { setActiveNav("Settings"); setWorkspaceMenuOpen(false); }}><Icon name="settings" /><span>Administration</span></button>}
          <button role="menuitem" onClick={() => { setWorkspaceMenuOpen(false); onSignOut(); }}><span className="menu-signout">↗</span><span>Sign out</span></button>
        </div>}
      </div>
      <nav className="nav-list" aria-label="Main navigation">
        {[["grid", "Overview"], ["ticket", "Tickets"], ["users", "People"], ["book", "Knowledge base"], ["chart", "Reports"], ...(currentUser?.profile.interface === "helpdesk" ? [["ticket", "Approvals"], ...(currentUser.profile.name === "Administrator" ? [["settings", "Workflows"]] : [])] : [])].map(([icon, label]) => <button className={`nav-item ${activeNav === label ? "active" : ""}`} key={label} onClick={() => setActiveNav(label)}><Icon name={icon} /><span>{label}</span>{label === "Tickets" && <b>{openCount}</b>}</button>)}
      </nav>
      <div className="sidebar-bottom"><button className={`nav-item ${activeNav === "Settings" ? "active" : ""}`} onClick={() => setActiveNav("Settings")}><Icon name="settings" /><span>Settings</span></button><div className="user-card"><span className="avatar avatar-slate">{currentUser?.name.slice(0, 2).toUpperCase() ?? "AD"}</span><span><strong>{currentUser?.name ?? "Admin"}</strong><small>{currentUser?.profile.name ?? "Administrator"}</small></span><button aria-label="Sign out" onClick={onSignOut}>↗</button></div></div>
    </aside>
    <main className="main-canvas">
      <header className="topbar"><div className="topbar-leading"><button className="mobile-menu-button icon-button" aria-label="Open navigation" onClick={() => setMobileNavOpen(true)}><span /><span /><span /></button><div className="breadcrumb">Workspace <span>/</span> <strong>{activeNav}</strong></div></div><div className="topbar-actions"><button aria-label="Notifications" className="icon-button"><Icon name="bell" /><i /></button><button className="avatar avatar-indigo top-avatar">AD</button></div></header>
      <div className="page-content">
        {activeNav === "Settings" ? <SettingsPanel currentUser={currentUser} adminToken={token} /> : activeNav === "Workflows" ? <WorkflowPanel token={token} /> : activeNav === "Approvals" ? <ApprovalsPanel token={token} currentUser={currentUser} /> : activeNav === "Overview" ? <OverviewPanel tickets={tickets} openCount={openCount} resolvedCount={resolvedCount} highPriorityCount={highPriorityCount} /> : activeNav === "People" ? <PeoplePanel currentUser={currentUser} requesters={requesters} agents={agents} /> : activeNav === "Knowledge base" ? <KnowledgePanel categories={categories} /> : activeNav === "Reports" ? <ReportsPanel tickets={tickets} /> : <>
        <div className="page-heading"><div><p className="kicker">SERVICE OPERATIONS</p><h1>{activeNav === "Tickets" ? "Tickets" : activeNav}</h1><p className="subheading">{currentUser?.profile.interface === "self_service" ? "Report an incident or request a service, then track your tickets here." : queueAccess ? "Tickets assigned to you, your teams, or awaiting a technician." : "Manage, prioritize, and resolve requests across your organization."}</p></div><button className="primary-button" onClick={() => setShowCreate(true)}><span>+</span> Create ticket</button></div>
        <div className="metric-row"><div className="metric"><span className="metric-label">Open tickets</span><strong>{openCount}</strong><small className="metric-up">+12% <span>vs last week</span></small></div><div className="metric"><span className="metric-label">Awaiting response</span><strong>{tickets.filter((ticket) => ticket.status === "PENDING").length}</strong><small>Needs attention</small></div><div className="metric"><span className="metric-label">SLA compliance</span><strong>94.2%</strong><small className="metric-up">+2.4% <span>vs last month</span></small></div><div className="metric"><span className="metric-label">Avg. resolution</span><strong>6h 24m</strong><small>Across all tickets</small></div></div>
        <section className="table-card">
          {queueAccess && <div className="list-controls" aria-label="Ticket queues">
            {([
              ["all", "All visible", queueCounts.all],
              ["mine", "My tickets", queueCounts.mine],
              ["teams", "My teams", queueCounts.teams],
              ["unassigned", "Unassigned", queueCounts.unassigned],
            ] as const).map(([queue, label, count]) => <button key={queue} type="button" className={`secondary-button ${queueFilter === queue ? "button-active" : ""}`} aria-pressed={queueFilter === queue} onClick={() => { setQueueFilter(queue); setListPage(1); }}>{label} <b>{count}</b></button>)}
          </div>}
          <div className="team-counts" aria-label="Team ticket counts">{teamCounts.map((team) => <span className="team-count" key={team.team_id ?? "unassigned"}>Team {team.team_id ?? "Unassigned"} <b>{team.count}</b></span>)}</div>
          <div className="table-toolbar"><div className="search-box"><Icon name="search" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search tickets by ID, title, or keyword" /></div><div className="toolbar-actions"><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} aria-label="Filter tickets"><option>All tickets</option><option>NEW</option><option>ASSIGNED</option><option>PENDING</option><option>SOLVED</option><option>CLOSED</option></select><div className="toolbar-menu-wrap"><button className={`filter-button ${filterOpen ? "button-active" : ""}`} onClick={() => { setFilterOpen((open) => !open); setMoreOpen(false); }}>Filter <span>{activeFilterCount}</span></button>{filterOpen && <div className="filter-popover"><label>Priority<select value={priorityFilter} onChange={(event) => setPriorityFilter(event.target.value)}><option>All priorities</option><option value="1">Low (1)</option><option value="2">Low (2)</option><option value="3">Medium (3)</option><option value="4">High (4)</option><option value="5">High (5)</option></select></label><button className="clear-button" onClick={clearFilters}>Clear filters</button></div>}</div><div className="toolbar-menu-wrap"><button className="more-button" aria-label="More ticket actions" aria-expanded={moreOpen} onClick={() => { setMoreOpen((open) => !open); setFilterOpen(false); }}>•••</button>{moreOpen && <div className="more-popover" role="menu"><button role="menuitem" onClick={() => { setMoreOpen(false); void loadTickets(); }}>Refresh tickets</button><button role="menuitem" onClick={clearFilters}>Clear all filters</button></div>}</div></div></div>
          <div className="list-controls"><label>Sort<select value={listSort} onChange={(event) => { setListSort(event.target.value); setListPage(1); }}><option value="opened_at">Opened</option><option value="priority">Priority</option><option value="status">Status</option><option value="title">Title</option></select></label><button className="sort-toggle" onClick={() => setListDirection((current) => current === "asc" ? "desc" : "asc")}>{listDirection === "asc" ? "Ascending" : "Descending"}</button><label>Page size<select value={listPageSize} onChange={(event) => { setListPageSize(Number(event.target.value)); setListPage(1); }}><option value="10">10</option><option value="25">25</option><option value="50">50</option><option value="100">100</option></select></label></div>
          <div className="profile-control-panel" aria-label="Saved ticket profiles">
            <label>Saved profile<select value={activeListProfileId} onChange={(event) => applyListProfile(listProfiles.find((profile) => String(profile.id) === event.target.value))}><option value="">Current view</option>{listProfiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.kind === "filter" ? "Filter" : "Columns"} · {profile.name}{profile.is_default ? " (default)" : ""}</option>)}</select></label>
            <button className="secondary-button" disabled={!activeListProfileId || listProfiles.find((profile) => String(profile.id) === activeListProfileId)?.is_default} onClick={() => void setDefaultListProfile()}>Set default</button>
            <label>Save as<select value={profileSaveKind} onChange={(event) => setProfileSaveKind(event.target.value as "filter" | "column")}><option value="filter">Filter profile</option><option value="column">Column profile</option></select></label>
            <label className="profile-name-field">Profile name<input value={profileName} maxLength={120} onChange={(event) => setProfileName(event.target.value)} placeholder="Name this view" /></label>
            <button className="secondary-button" onClick={() => void saveListProfile()}>Save as</button>
          </div>
          <div className="profile-column-chooser"><strong>Choose columns</strong>{ticketListColumns.map(({ key, label }) => <label key={key}><input type="checkbox" checked={listColumns.includes(key)} onChange={(event) => setListColumns((columns) => event.target.checked ? [...columns, key] : columns.length > 1 ? columns.filter((column) => column !== key) : columns)} />{label}</label>)}</div>
          {error && <p className="error" role="alert">{error}</p>}
          <div className="table-meta"><span><strong>{listTotal}</strong> tickets</span><button className="clear-button" onClick={clearFilters}>Clear filters</button></div>
          <div className="ticket-table-wrap"><table><thead><tr><th className="check-col"><input type="checkbox" aria-label="Select all tickets" /></th>{listColumns.map((column) => <th key={column}>{ticketListColumns.find((item) => item.key === column)?.label ?? column}</th>)}<th /></tr></thead><tbody>{filteredTickets.map((ticket) => <tr key={ticket.id} className={selected?.id === ticket.id ? "row-selected" : ""} onClick={() => void selectTicket(ticket)}><td className="check-col" onClick={(event) => event.stopPropagation()}><input type="checkbox" aria-label={`Select ticket ${ticket.id}`} /></td>{listColumns.map((column) => <td key={column}>{renderTicketColumn(ticket, column)}</td>)}<td>{queueAccess && !ticket.assignee_id && <button className="secondary-button" onClick={(event) => { event.stopPropagation(); void claimTicket(ticket); }}>Claim</button>}<button className="row-menu" onClick={(event) => event.stopPropagation()}>•••</button></td></tr>)}</tbody></table>{!filteredTickets.length && <div className="empty-table"><strong>{queueAccess ? "No tickets in this queue" : "No tickets found"}</strong><span>{queueAccess ? "Try another queue or refresh the list." : "Try changing your search or filters."}</span></div>}</div>
          <div className="pagination-controls"><button disabled={listPage <= 1} onClick={() => setListPage((page) => page - 1)}>Previous</button><span>Page {listPage}</span><button disabled={listPage * listPageSize >= listTotal} onClick={() => setListPage((page) => page + 1)}>Next</button></div>
        </section>
        </>}
      </div>
    </main>
    {showDrawer && selected && <aside className="detail-drawer"><div className="drawer-header"><span className="kicker">TICKET #{selected.id}</span><button className="icon-button" onClick={() => setShowDrawer(false)} aria-label="Close details"><Icon name="close" /></button></div><div className="drawer-title"><h2>{selected.title}</h2><span className={`status status-${selected.status.toLowerCase()}`}><i />{statusLabels[selected.status] ?? selected.status}</span>{queueAccess && !selected.assignee_id && <button className="secondary-button" onClick={() => void claimTicket(selected)}>Claim ticket</button>}</div><div className="drawer-tabs"><button className="tab active">Activity</button><button className="tab">Details</button></div><div className="drawer-content"><section className="drawer-section"><h3>Conversation</h3><div className="timeline">{timeline.map((item, index) => <article key={`${item.kind}-${index}`} className={item.is_private ? "private-note" : ""}><span className="timeline-dot" /><div className="timeline-head"><strong>{item.is_private ? "Internal note" : item.kind}</strong><time>{new Date(item.created_at).toLocaleString()}</time></div><p>{item.body}</p></article>)}{!timeline.length && <p className="muted">No activity yet.</p>}</div></section><form className="reply-box" onSubmit={addFollowup}><div className="reply-type"><span>Public reply</span><button type="button">⌄</button></div><textarea value={followup} onChange={(event) => setFollowup(event.target.value)} placeholder="Write a reply to the requester…" /><div className="reply-actions"><span>Visible to requester</span><button className="primary-button" type="submit">Send reply <Icon name="arrow" /></button></div></form><section className="drawer-section metadata"><h3>Ticket details</h3><dl><div><dt>Requester</dt><dd>Employee</dd></div><div><dt>Category</dt><dd>{selected.category_id ? `Category ${selected.category_id}` : "Uncategorized"}</dd></div><div><dt>Opened</dt><dd>{new Date(selected.opened_at).toLocaleDateString()}</dd></div><div><dt>Assignee</dt><dd>{selected.assignee_id ? "Assigned technician" : "Unassigned"}</dd></div></dl></section></div></aside>}
    {showCreate && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowCreate(false); }}>    <form className="create-modal" onSubmit={createTicket} onKeyDown={(event) => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); event.currentTarget.requestSubmit(); } }}><div className="modal-header"><div><p className="kicker">NEW REQUEST</p><h2>Create a ticket</h2></div><button type="button" className="icon-button" onClick={() => setShowCreate(false)} aria-label="Close"><Icon name="close" /></button></div>    <label>{currentUser?.profile.interface === "self_service" ? "What do you need?" : "Ticket type"}<select value={ticketType} onChange={(event) => setTicketType(event.target.value)} required><option value="">Select a request type</option>{ticketTypes.map((type) => <option key={type.id} value={type.id}>{type.itil_class === "INCIDENT" ? "Report an issue · Incident" : type.itil_class === "REQUEST" ? "Request a service · Service Request" : type.name}</option>)}</select>{currentUser?.profile.interface === "self_service" && <small className="form-help">Choose Incident for an interruption or fault. Choose Service Request for access, hardware, software, or another service.</small>}</label><label>Template (optional)<select defaultValue="" onChange={(event) => applyTemplate(event.target.value)}><option value="">Start from blank</option>{templates.filter((template) => String(template.ticket_type_id) === ticketType).map((template) => <option key={template.id} value={template.id}>{template.name}</option>)}</select></label>{currentUser?.profile.interface === "helpdesk" &&     <label>Requester<input list="requester-options" value={requesterSearch} onChange={(event) => { const value = event.target.value; setRequesterSearch(value); const match = requesters.find((requester) => `${requester.name} — ${requester.email}` === value); setSelectedRequesterId(match?.id ?? null); }} placeholder="Search by name or email" /><datalist id="requester-options">{requesters.map((requester) => <option key={requester.id} value={`${requester.name} — ${requester.email}`} />)}</datalist>{selectedRequesterId && (() => { const requester = requesters.find((item) => item.id === selectedRequesterId); return requester ? <span className="requester-card"><strong>{requester.name}</strong><small>{requester.email} · Organisation: current entity · Active requester</small><b aria-label="VIP requester">★</b></span> : null; })()}</label>}<label>Summary<input value={newTitle} onChange={(event) => setNewTitle(event.target.value)} minLength={5} required placeholder="Summarize the issue" /></label><label>Description<textarea value={newDescription} onChange={(event) => setNewDescription(event.target.value)} minLength={10} required placeholder="Tell the support team what happened and what you need." /></label>{formSchema ?     <DynamicFields schema={formSchema} values={customFields} onChange={(key, value) => setCustomFields((current) => ({ ...current, [key]: value }))} portal={currentUser?.profile.interface === "self_service"} /> : <p className="form-help">Loading the selected ticket form…</p>}    <label className="file-picker">Attachments <span>Optional</span><span className="drop-zone" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); setNewFiles((current) => [...current, ...Array.from(event.dataTransfer.files)]); }}><input type="file" accept=".pdf,.png,.jpg,.jpeg,.txt,application/pdf,image/png,image/jpeg,text/plain" multiple onChange={(event) => setNewFiles(Array.from(event.target.files ?? []))} /><strong>Drop files here or choose files</strong><small>PDF, PNG, JPG, or TXT. Up to 10 MB per file.</small></span></label>{newFiles.length > 0 && <div className="file-list">{newFiles.map((file) => <span key={`${file.name}-${file.size}`}>{file.name}<b>{formatFileSize(file.size)}</b></span>)}</div>}{createError && <p className="error">{createError}</p>}{uploadMessage && <p className="error">{uploadMessage}</p>}    <aside className="new-ticket-sidebar" aria-label="Ticket preview"><p className="kicker">LIVE ROUTING</p><div className={`priority-preview priority-${assignmentPreview?.priority ?? 3}`}>Priority {assignmentPreview?.priority ?? "—"}</div><dl>{assignmentPreview?.team_name && <div><dt>Suggested team</dt><dd>{assignmentPreview.team_name}</dd></div>}{assignmentPreview?.agent_name && <div><dt>Suggested agent</dt><dd>{assignmentPreview.agent_name}</dd></div>}{assignmentPreview?.sla.sla_name && <div><dt>SLA</dt><dd>{assignmentPreview.sla.sla_name}</dd></div>}<div><dt>Response target</dt><dd>{assignmentPreview?.sla.response_target ? new Date(assignmentPreview.sla.response_target).toLocaleString() : "Pending selection"}</dd></div><div><dt>Fix target</dt><dd>{assignmentPreview?.sla.fix_target ? new Date(assignmentPreview.sla.fix_target).toLocaleString() : "Pending selection"}</dd></div></dl><p className="kicker">KNOWLEDGE SUGGESTIONS</p><small>{newTitle.trim() ? "Search suggestions will appear as knowledge articles are connected to this summary." : "Start typing a Summary to see suggested articles."}</small></aside><div className="modal-actions"><label className="checkbox"><input type="checkbox" checked={submitAnother} onChange={(event) => setSubmitAnother(event.target.checked)} /> Create another</label><button type="button" className="secondary-button" onClick={() => { if (!newTitle && !newDescription || window.confirm("Discard this ticket draft?")) setShowCreate(false); }}>Cancel</button><button type="button" className="secondary-button" onClick={saveDraft}>Save draft</button><button type="submit" className="primary-button" disabled={!formSchema}>Submit ticket <Icon name="arrow" /></button></div></form></div>}
  </div>;
}

function OverviewPanel({ tickets, openCount, resolvedCount, highPriorityCount }: { tickets: Ticket[]; openCount: number; resolvedCount: number; highPriorityCount: number }) {
  const statusCounts = tickets.reduce<Record<string, number>>((counts, ticket) => ({ ...counts, [ticket.status]: (counts[ticket.status] ?? 0) + 1 }), {});
  return <section className="section-panel"><div className="page-heading"><div><p className="kicker">SERVICE OPERATIONS</p><h1>Workspace overview</h1><p className="subheading">Monitor service health and the work requiring attention.</p></div></div><div className="metric-row"><div className="metric"><span className="metric-label">Open tickets</span><strong>{openCount}</strong><small>Needs attention</small></div><div className="metric"><span className="metric-label">Resolved tickets</span><strong>{resolvedCount}</strong><small>Current workspace</small></div><div className="metric"><span className="metric-label">High priority</span><strong>{highPriorityCount}</strong><small>Priority 4 or 5</small></div><div className="metric"><span className="metric-label">Total requests</span><strong>{tickets.length}</strong><small>All visible tickets</small></div></div><div className="panel-grid"><section className="table-card panel-card"><div className="panel-card-heading"><h2>Ticket status</h2><span>Live queue</span></div>{Object.entries(statusCounts).map(([status, count]) => <div className="bar-row" key={status}><span>{statusLabels[status] ?? status}</span><b><i style={{ width: `${tickets.length ? Math.max(8, (count / tickets.length) * 100) : 0}%` }} /></b><strong>{count}</strong></div>)}</section><section className="table-card panel-card"><div className="panel-card-heading"><h2>Recent activity</h2><span>Latest requests</span></div>{tickets.slice(0, 5).map((ticket) => <div className="activity-row" key={ticket.id}><span className={`status status-${ticket.status.toLowerCase()}`}><i />#{ticket.id}</span><strong>{ticket.title}</strong><small>{statusLabels[ticket.status] ?? ticket.status}</small></div>)}</section></div></section>;
}

function PeoplePanel({ currentUser, requesters, agents }: { currentUser: CurrentUser | null; requesters: Requester[]; agents: Requester[] }) {
  const people = [...agents, ...requesters.filter((requester) => !agents.some((agent) => agent.id === requester.id))];
  return <section className="section-panel"><div className="page-heading"><div><p className="kicker">PEOPLE AND TEAMS</p><h1>People</h1><p className="subheading">Manage requesters and service desk participants in your workspace.</p></div></div><div className="table-card people-card">{currentUser?.profile.interface !== "helpdesk" ? <div className="empty-table"><strong>{currentUser?.name}</strong><span>Requester profile and ticket access are managed by your service desk.</span></div> : people.length ? people.map((person) => <div className="person-row" key={person.id}><span className="avatar avatar-indigo">{person.name.slice(0, 2).toUpperCase()}</span><div><strong>{person.name}</strong><small>{person.email}</small></div><span className="person-role">{agents.some((agent) => agent.id === person.id) ? "Technician" : "Requester"}</span></div>) : <div className="empty-table"><strong>No people found</strong><span>Active users in this entity will appear here.</span></div>}</div></section>;
}

function KnowledgePanel({ categories }: { categories: Category[] }) {
  return <section className="section-panel"><div className="page-heading"><div><p className="kicker">SELF SERVICE</p><h1>Knowledge base</h1><p className="subheading">Give employees guided answers before they raise a request.</p></div><button className="secondary-button">Search articles</button></div><div className="knowledge-grid"><section className="table-card panel-card"><div className="panel-card-heading"><h2>Browse categories</h2><span>{categories.length} categories</span></div>{categories.length ? categories.map((category) => <div className="knowledge-row" key={category.id}><span className="avatar avatar-indigo"><Icon name="book" /></span><div><strong>{category.name}</strong><small>Service articles and known solutions</small></div><span>→</span></div>) : <div className="empty-table"><strong>No categories configured</strong><span>Administrators can add knowledge categories from Settings.</span></div>}</section><section className="knowledge-callout"><p className="kicker">QUICK HELP</p><h2>Find an answer faster</h2><p>Search for a known solution before creating a new ticket.</p><button className="primary-button">Browse solutions</button></section></div></section>;
}

function ReportsPanel({ tickets }: { tickets: Ticket[] }) {
  const priorities = [1, 2, 3, 4, 5].map((priority) => ({ priority, count: tickets.filter((ticket) => ticket.priority === priority).length }));
  return <section className="section-panel"><div className="page-heading"><div><p className="kicker">SERVICE ANALYTICS</p><h1>Reports</h1><p className="subheading">Understand volume, priority, and workload across visible tickets.</p></div><button className="secondary-button">Export report</button></div><div className="metric-row"><div className="metric"><span className="metric-label">Ticket volume</span><strong>{tickets.length}</strong><small>Visible tickets</small></div><div className="metric"><span className="metric-label">SLA due</span><strong>{tickets.filter((ticket) => ticket.ttr_due_at).length}</strong><small>Tickets with deadlines</small></div><div className="metric"><span className="metric-label">Unassigned</span><strong>{tickets.filter((ticket) => !ticket.assignee_id).length}</strong><small>Awaiting ownership</small></div><div className="metric"><span className="metric-label">Completion</span><strong>{tickets.length ? `${Math.round((tickets.filter((ticket) => ["SOLVED", "CLOSED"].includes(ticket.status)).length / tickets.length) * 100)}%` : "0%"}</strong><small>Solved or closed</small></div></div><section className="table-card report-card"><div className="panel-card-heading"><h2>Tickets by priority</h2><span>Current queue</span></div>{priorities.map(({ priority, count }) => <div className="bar-row" key={priority}><span>Priority {priority}</span><b><i style={{ width: `${tickets.length ? Math.max(count ? 8 : 0, (count / tickets.length) * 100) : 0}%` }} /></b><strong>{count}</strong></div>)}</section></section>;
}

function SettingsPanel({ currentUser, adminToken }: { currentUser: CurrentUser | null; adminToken: TokenPair }) {
  const [emailUpdates, setEmailUpdates] = useState(true);
  const [compactTables, setCompactTables] = useState(false);
  const [saved, setSaved] = useState(false);

  function saveSettings() {
    setSaved(true);
    window.setTimeout(() => setSaved(false), 2200);
  }

  type AdminField = { id: number; key: string; label: string; type: string; options: Record<string, unknown>; validation: Record<string, unknown>; is_system: boolean };
  type AdminTicketType = { id: number; name: string; itil_class: string; default_priority: number; default_sla_id?: number | null; color?: string | null; fields: Array<{ field_definition_id: number; order: number; page: string; required: boolean; visible_to_roles: string[]; editable_by_roles: string[]; default_value: unknown; show_on_portal: boolean; show_condition: Record<string, unknown> }> };
  type AdminTemplate = { id: number; name: string; ticket_type_id: number; team_id?: number | null; prefilled: Record<string, unknown> };
  type AdminCategory = { id: number; name: string; parent_id?: number | null; entity_id: number };

  function AdminPanel({ token }: { token: TokenPair }) {
    const headers = { Authorization: `Bearer ${token.access_token}` };
    const [section, setSection] = useState("Ticket types");
    const [fields, setFields] = useState<AdminField[]>([]);
    const [types, setTypes] = useState<AdminTicketType[]>([]);
    const [templates, setTemplates] = useState<AdminTemplate[]>([]);
    const [adminCategories, setAdminCategories] = useState<AdminCategory[]>([]);
    const [newCategory, setNewCategory] = useState({ name: "", parent_id: "" });
    const [selectedType, setSelectedType] = useState<AdminTicketType | null>(null);
    const [draft, setDraft] = useState<AdminTicketType | null>(null);
    const [matrix, setMatrix] = useState<number[][]>([]);
    const [notice, setNotice] = useState("");
    const [dirty, setDirty] = useState(false);
    const [draggedIndex, setDraggedIndex] = useState<number | null>(null);
    const [newField, setNewField] = useState({ key: "", label: "", type: "text" });
    const [newTemplate, setNewTemplate] = useState({ name: "", ticket_type_id: "", prefilled: "{}" });

    async function loadAdminData() {
      const [fieldResponse, typeResponse, templateResponse, matrixResponse, categoryResponse] = await Promise.all([
        fetch("/api/v1/field-definitions", { headers }),
        fetch("/api/v1/ticket-types", { headers }),
        fetch("/api/v1/templates", { headers }),
        fetch("/api/v1/priority-matrix", { headers }),
        fetch("/api/v1/categories", { headers }),
      ]);
      if (fieldResponse.ok) setFields(await fieldResponse.json());
      if (typeResponse.ok) {
        const loaded = await typeResponse.json();
        setTypes(loaded);
        if (!selectedType && loaded[0]) { setSelectedType(loaded[0]); setDraft(loaded[0]); }
      }
      if (templateResponse.ok) setTemplates(await templateResponse.json());
      if (matrixResponse.ok) setMatrix((await matrixResponse.json()).matrix);
      if (categoryResponse.ok) setAdminCategories(await categoryResponse.json());
    }
    useEffect(() => { void loadAdminData(); }, []);

    function editType(type: AdminTicketType) {
      if (dirty && !window.confirm("Discard unsaved ticket type changes?")) return;
      setDirty(false);
      setSelectedType(type);
      setDraft(JSON.parse(JSON.stringify(type)) as AdminTicketType);
    }
    function updateDraft(changes: Partial<AdminTicketType>) {
      setDirty(true);
      setDraft((current) => current ? { ...current, ...changes } : current);
    }
    useEffect(() => {
      const guard = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } };
      window.addEventListener("beforeunload", guard);
      return () => window.removeEventListener("beforeunload", guard);
    }, [dirty]);
    function moveField(index: number, direction: number) {
      if (!draft) return;
      const next = [...draft.fields];
      const target = index + direction;
      if (target < 0 || target >= next.length) return;
      [next[index], next[target]] = [next[target], next[index]];
      updateDraft({ fields: next.map((field, order) => ({ ...field, order })) });
    }
    function dropField(targetIndex: number) {
      if (draggedIndex === null || !draft || draggedIndex === targetIndex) return;
      const next = [...draft.fields];
      const [field] = next.splice(draggedIndex, 1);
      next.splice(targetIndex, 0, field);
      updateDraft({ fields: next.map((item, order) => ({ ...item, order })) });
      setDraggedIndex(null);
    }
    async function saveType() {
      if (!draft || !draft.name.trim() || draft.fields.some((field) => !field.field_definition_id)) return;
      const payload = { name: draft.name, itil_class: draft.itil_class, default_priority: draft.default_priority, default_sla_id: draft.default_sla_id ?? null, icon: "ticket", color: draft.color ?? "#4f46e5", is_active: true, fields: draft.fields };
      const response = await fetch(draft.id ? `/api/v1/ticket-types/${draft.id}` : "/api/v1/ticket-types", { method: draft.id ? "PATCH" : "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (!response.ok) { setNotice("Could not save ticket type. Check the field configuration."); return; }
      setDirty(false);
      setNotice("Ticket type saved.");
      await loadAdminData();
    }
    async function createField(event: FormEvent<HTMLFormElement>) {
      event.preventDefault();
      const response = await fetch("/api/v1/field-definitions", { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify({ ...newField, options: {}, validation: {}, is_system: false }) });
      if (response.ok) { setNewField({ key: "", label: "", type: "text" }); setNotice("Field added to the catalogue."); await loadAdminData(); }
      else setNotice("Field key must be unique and use lowercase letters, numbers, and underscores.");
    }
    async function saveMatrix() {
      const response = await fetch("/api/v1/priority-matrix", { method: "PUT", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify({ matrix }) });
      setNotice(response.ok ? "Priority matrix saved." : "Priority matrix must be a valid 5 × 5 matrix.");
    }
    async function createTemplate(event: FormEvent<HTMLFormElement>) {
      event.preventDefault();
      try {
        const response = await fetch("/api/v1/templates", { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify({ name: newTemplate.name, ticket_type_id: Number(newTemplate.ticket_type_id), prefilled: JSON.parse(newTemplate.prefilled) }) });
        if (!response.ok) throw new Error();
        setNewTemplate({ name: "", ticket_type_id: "", prefilled: "{}" }); setNotice("Template saved."); await loadAdminData();
      } catch { setNotice("Template JSON is invalid or the ticket type is unavailable."); }
    }
    async function createCategory(event: FormEvent<HTMLFormElement>) {
      event.preventDefault();
      const response = await fetch("/api/v1/categories", { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify({ name: newCategory.name, parent_id: newCategory.parent_id ? Number(newCategory.parent_id) : null }) });
      if (response.ok) { setNewCategory({ name: "", parent_id: "" }); setNotice("Category added."); await loadAdminData(); }
      else setNotice("Category could not be added. Check the parent and entity.");
    }

    return <section className="admin-panel">
      <div className="page-heading"><div><p className="kicker">ADMINISTRATION</p><h1>Service configuration</h1><p className="subheading">Design the forms and routing rules used by SVC ServiceFlow.</p></div></div>
      <div className="admin-tabs">{["Ticket types", "Field catalogue", "Templates", "Priority matrix", "Categories"].map((item) => <button key={item} className={section === item ? "active" : ""} onClick={() => setSection(item)}>{item}</button>)}</div>
      {notice && <p className="settings-saved" role="status">{notice}</p>}
      {section === "Ticket types" && <div className="admin-layout"><section className="table-card admin-list"><div className="panel-card-heading"><h2>Ticket types</h2><button className="secondary-button" onClick={() => { const fresh = { id: 0, name: "New ticket type", itil_class: "REQUEST", default_priority: 3, fields: [] }; setSelectedType(fresh); setDraft(fresh); }}>New type</button></div>{types.map((type) => <button className={`admin-list-row ${selectedType?.id === type.id ? "selected" : ""}`} key={type.id} onClick={() => editType(type)}><span><strong>{type.name}</strong><small>{type.itil_class} · {type.fields.length} fields</small></span><b>›</b></button>)}</section>{draft && <section className="table-card admin-editor"><div className="panel-card-heading"><div><h2>Ticket type editor</h2><span>Drag order with the arrows, then preview the portal form.</span></div><button className="primary-button" onClick={() => void saveType()}>Save type</button></div><div className="form-grid"><label>Name<input value={draft.name} onChange={(event) => updateDraft({ name: event.target.value })} required /></label><label>ITIL class<select value={draft.itil_class} onChange={(event) => updateDraft({ itil_class: event.target.value })}><option value="INCIDENT">Incident</option><option value="REQUEST">Service Request</option><option value="PROBLEM">Problem</option><option value="CHANGE">Change</option></select></label><label>Default priority<input type="number" min="1" max="5" value={draft.default_priority} onChange={(event) => updateDraft({ default_priority: Number(event.target.value) })} /></label></div><h3 className="admin-subtitle">Field layout</h3>      <div className="field-layout">{draft.fields.map((field, index) => { const definition = fields.find((item) => item.id === field.field_definition_id); const replace = (changes: Partial<typeof field>) => updateDraft({ fields: draft.fields.map((item, itemIndex) => itemIndex === index ? { ...item, ...changes } : item) }); return <div className="field-layout-row" key={`${field.field_definition_id}-${index}`} draggable onDragStart={() => setDraggedIndex(index)} onDragOver={(event) => event.preventDefault()} onDrop={() => dropField(index)}><span className="drag-handle">⋮⋮</span><strong>{definition?.label ?? `Field ${field.field_definition_id}`}</strong><select value={field.page} onChange={(event) => replace({ page: event.target.value })}><option value="details">Details</option><option value="classification">Classification</option><option value="schedule">Schedule</option></select><label className="inline-check"><input type="checkbox" checked={field.required} onChange={(event) => replace({ required: event.target.checked })} /> Required</label><label className="inline-check"><input type="checkbox" checked={field.show_on_portal} onChange={(event) => replace({ show_on_portal: event.target.checked })} /> Visible</label><label className="inline-check"><input type="checkbox" checked={field.editable_by_roles.includes("Requester")} onChange={(event) => replace({ editable_by_roles: event.target.checked ? [...new Set([...field.editable_by_roles, "Requester"])] : field.editable_by_roles.filter((role) => role !== "Requester") })} /> Requester editable</label><input className="field-default-input" placeholder="Default value" value={field.default_value == null ? "" : String(field.default_value)} onChange={(event) => replace({ default_value: event.target.value || null })} /><input className="field-condition-input" placeholder="Condition JSON" value={Object.keys(field.show_condition ?? {}).length ? JSON.stringify(field.show_condition) : ""} onChange={(event) => { try { replace({ show_condition: event.target.value ? JSON.parse(event.target.value) : {} }); } catch { /* wait for valid JSON */ } }} /><button type="button" className="icon-button" onClick={() => moveField(index, -1)}>↑</button><button type="button" className="icon-button" onClick={() => moveField(index, 1)}>↓</button></div>; })}</div><div className="field-picker-row">{fields.filter((field) => !draft.fields.some((item) => item.field_definition_id === field.id)).map((field) => <button key={field.id} className="secondary-button" onClick={() => updateDraft({ fields: [...draft.fields, { field_definition_id: field.id, order: draft.fields.length, page: "details", required: false, visible_to_roles: ["Administrator", "Technician", "Requester"], editable_by_roles: ["Administrator", "Technician"], default_value: null, show_on_portal: false, show_condition: {} }] })}>+ {field.label}</button>)}</div><h3 className="admin-subtitle">Live portal preview</h3><div className="form-preview">{draft.fields.map((field) => { const definition = fields.find((item) => item.id === field.field_definition_id); return <label key={field.field_definition_id}>{definition?.label ?? "Field"}{field.required && <span> *</span>}<input placeholder={String(field.default_value ?? `Enter ${definition?.label?.toLowerCase() ?? "value"}`)} disabled /></label>; })}</div></section>}</div>}
      {section === "Field catalogue" && <div className="admin-two-column"><section className="table-card"><div className="panel-card-heading"><h2>Field catalogue</h2><span>{fields.length} definitions</span></div>{fields.map((field) => <div className="catalogue-row" key={field.id}><strong>{field.label}</strong><span>{field.key}</span><small>{field.type}{field.is_system ? " · System" : ""}</small></div>)}</section><form className="table-card admin-form-card" onSubmit={createField}><h2>Add custom field</h2><p>Create a field once and reuse it across ticket types.</p><label>Key<input value={newField.key} onChange={(event) => setNewField({ ...newField, key: event.target.value })} placeholder="business_unit" required /></label><label>Label<input value={newField.label} onChange={(event) => setNewField({ ...newField, label: event.target.value })} placeholder="Business unit" required /></label><label>Type<select value={newField.type} onChange={(event) => setNewField({ ...newField, type: event.target.value })}>{["text", "textarea", "number", "date", "datetime", "select", "multiselect", "checkbox", "url"].map((type) => <option key={type}>{type}</option>)}</select></label><button className="primary-button" type="submit">Add field</button></form></div>}
      {section === "Templates" && <div className="admin-two-column"><section className="table-card"><div className="panel-card-heading"><h2>Templates</h2><span>{templates.length} saved</span></div>{templates.map((template) => <div className="catalogue-row" key={template.id}><strong>{template.name}</strong><span>{types.find((type) => type.id === template.ticket_type_id)?.name ?? "Ticket type"}</span><small>{Object.keys(template.prefilled).length} prefilled fields</small></div>)}</section><form className="table-card admin-form-card" onSubmit={createTemplate}><h2>New template</h2><label>Name<input value={newTemplate.name} onChange={(event) => setNewTemplate({ ...newTemplate, name: event.target.value })} required /></label><label>Ticket type<select value={newTemplate.ticket_type_id} onChange={(event) => setNewTemplate({ ...newTemplate, ticket_type_id: event.target.value })} required><option value="">Select type</option>{types.map((type) => <option key={type.id} value={type.id}>{type.name}</option>)}</select></label><label>Prefilled JSON<textarea value={newTemplate.prefilled} onChange={(event) => setNewTemplate({ ...newTemplate, prefilled: event.target.value })} rows={7} /></label><button className="primary-button" type="submit">Save template</button></form></div>}
      {section === "Priority matrix" && <section className="table-card matrix-card"><div className="panel-card-heading"><div><h2>Priority matrix</h2><span>Rows are urgency, columns are impact.</span></div><button className="primary-button" onClick={() => void saveMatrix()}>Save matrix</button></div><div className="matrix-grid"><div /><span>1</span><span>2</span><span>3</span><span>4</span><span>5</span>{matrix.map((row, rowIndex) => <><strong key={`label-${rowIndex}`}>{rowIndex + 1}</strong>{row.map((value, columnIndex) => <input key={`${rowIndex}-${columnIndex}`} type="number" min="1" max="5" value={value} onChange={(event) => setMatrix(matrix.map((currentRow, currentRowIndex) => currentRowIndex === rowIndex ? currentRow.map((current, currentColumn) => currentColumn === columnIndex ? Number(event.target.value) : current) : currentRow))} />)}</>)}</div></section>}
      {section === "Categories" && <div className="admin-two-column"><section className="table-card"><div className="panel-card-heading"><h2>Category tree</h2><span>{adminCategories.length} categories · up to four levels</span></div>{adminCategories.length ? adminCategories.map((category) => { let level = 0; let parent = category.parent_id; while (parent && level < 4) { level += 1; parent = adminCategories.find((item) => item.id === parent)?.parent_id; } return <div className="catalogue-row category-tree-row" key={category.id} style={{ paddingLeft: `${level * 20}px` }}><strong>{level ? "↳ " : ""}{category.name}</strong><span>{level === 0 ? "Root" : `Level ${level + 1}`}</span><small>Entity scoped</small></div>; }) : <div className="empty-table"><strong>No categories configured</strong><span>Add the first category to start the service catalogue tree.</span></div>}</section><form className="table-card admin-form-card" onSubmit={createCategory}><h2>Add category</h2><p>Categories are restricted to the signed-in entity and may be nested four levels deep.</p><label>Name<input value={newCategory.name} onChange={(event) => setNewCategory({ ...newCategory, name: event.target.value })} required minLength={2} /></label><label>Parent<select value={newCategory.parent_id} onChange={(event) => setNewCategory({ ...newCategory, parent_id: event.target.value })}><option value="">Root category</option>{adminCategories.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}</select></label><button className="primary-button" type="submit">Add category</button></form></div>}
    </section>;
  }

  if (currentUser?.profile.name === "Administrator") return <AdminPanel token={adminToken} />;
  return <section className="settings-panel">
    <div className="page-heading"><div><p className="kicker">WORKSPACE SETTINGS</p><h1>Settings</h1><p className="subheading">Manage your personal workspace preferences.</p></div><button className="primary-button" onClick={saveSettings}>Save changes</button></div>
    {saved && <p className="settings-saved" role="status">Settings saved.</p>}
    <div className="settings-grid">
      <section className="settings-card"><div className="settings-card-heading"><div><h2>Profile</h2><p>Account details for the signed-in workspace.</p></div><span className="avatar avatar-indigo">{currentUser?.name.slice(0, 2).toUpperCase() ?? "AD"}</span></div><dl className="settings-details"><div><dt>Name</dt><dd>{currentUser?.name ?? "Administrator"}</dd></div><div><dt>Email</dt><dd>{currentUser?.email ?? "Not available"}</dd></div><div><dt>Role</dt><dd>{currentUser?.profile.name ?? "Administrator"}</dd></div></dl></section>
      <section className="settings-card"><div className="settings-card-heading"><div><h2>Preferences</h2><p>Choose how ServiceFlow works for you.</p></div></div><label className="settings-toggle"><span><strong>Email updates</strong><small>Receive updates about ticket activity.</small></span><input type="checkbox" checked={emailUpdates} onChange={(event) => setEmailUpdates(event.target.checked)} /></label><label className="settings-toggle"><span><strong>Compact tables</strong><small>Fit more tickets on each screen.</small></span><input type="checkbox" checked={compactTables} onChange={(event) => setCompactTables(event.target.checked)} /></label></section>
    </div>
  </section>;
}

function formatDue(date: string) {
  const hours = Math.round((new Date(date).getTime() - Date.now()) / 3600000);
  if (hours < 0) return "Breached";
  if (hours < 24) return `${hours}h remaining`;
  return `${Math.round(hours / 24)}d remaining`;
}

function formatFileSize(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function WorkflowPanel({ token }: { token: TokenPair }) {
  const [states, setStates] = useState("NEW, ASSIGNED, PLANNED, PENDING, SOLVED, CLOSED");
  const [transitions, setTransitions] = useState(JSON.stringify({
    NEW: ["ASSIGNED", "PENDING"],
    ASSIGNED: ["PLANNED", "PENDING", "SOLVED"],
    PLANNED: ["ASSIGNED", "PENDING", "SOLVED"],
    PENDING: ["ASSIGNED", "SOLVED"],
    SOLVED: ["CLOSED", "ASSIGNED"],
    CLOSED: [],
  }, null, 2));
  const [approvalRequirements, setApprovalRequirements] = useState("{}");
  const [isActive, setIsActive] = useState(true);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const headers = { Authorization: `Bearer ${token.access_token}` };

  useEffect(() => {
    let cancelled = false;
    void fetch("/api/v1/workflows/ticket", { headers })
      .then(async (response) => {
        if (!response.ok) throw new Error("Unable to load the ticket workflow.");
        const workflow = await response.json() as TicketWorkflow | null;
        if (cancelled || !workflow) return;
        setStates(workflow.states.join(", "));
        setTransitions(JSON.stringify(workflow.transitions, null, 2));
        setApprovalRequirements(JSON.stringify(workflow.approval_requirements, null, 2));
        setIsActive(workflow.is_active);
      })
      .catch((reason: Error) => {
        if (!cancelled) setError(reason.message);
      });
    return () => { cancelled = true; };
  }, [token.access_token]);

  async function saveWorkflow(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setNotice("");
    let transitionMap: Record<string, string[]>;
    let approvalMap: Record<string, number>;
    try {
      transitionMap = JSON.parse(transitions) as Record<string, string[]>;
      approvalMap = JSON.parse(approvalRequirements) as Record<string, number>;
    } catch {
      setError("Transitions and approval requirements must be valid JSON.");
      return;
    }
    const response = await fetch("/api/v1/workflows/ticket", {
      method: "PUT",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({
        states: states.split(",").map((state) => state.trim()).filter(Boolean),
        transitions: transitionMap,
        approval_requirements: approvalMap,
        is_active: isActive,
      }),
    });
    if (!response.ok) {
      setError("Workflow was not saved. Check state names, transitions, and approval counts.");
      return;
    }
    setNotice("Ticket workflow saved.");
  }

  return <section className="section-panel">
    <div className="page-heading"><div><p className="kicker">ADMINISTRATION</p><h1>Ticket workflows</h1><p className="subheading">Configure allowed ticket states, transitions, and approval gates for this entity.</p></div></div>
    <form className="table-card admin-form-card" onSubmit={saveWorkflow}>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p className="settings-saved" role="status">{notice}</p>}
      <label>States, comma separated<input value={states} onChange={(event) => setStates(event.target.value)} required /></label>
      <label>Allowed transitions (JSON)<textarea value={transitions} onChange={(event) => setTransitions(event.target.value)} rows={12} required /></label>
      <label>Required approvals by target state (JSON)<textarea value={approvalRequirements} onChange={(event) => setApprovalRequirements(event.target.value)} rows={4} placeholder={'{"SOLVED": 1}'} /></label>
      <label className="inline-check"><input type="checkbox" checked={isActive} onChange={(event) => setIsActive(event.target.checked)} /> Workflow active</label>
      <button className="primary-button" type="submit">Save workflow</button>
    </form>
  </section>;
}

function ApprovalsPanel({ token, currentUser }: { token: TokenPair; currentUser: CurrentUser | null }) {
  const [approvals, setApprovals] = useState<WorkflowApproval[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [recordId, setRecordId] = useState("");
  const [targetState, setTargetState] = useState("SOLVED");
  const [approverId, setApproverId] = useState("");
  const headers = { Authorization: `Bearer ${token.access_token}` };

  async function loadApprovals() {
    setError("");
    const response = await fetch("/api/v1/approvals/mine", { headers });
    if (!response.ok) {
      setError("Unable to load approvals assigned to you.");
      return;
    }
    setApprovals(await response.json() as WorkflowApproval[]);
  }

  useEffect(() => {
    void loadApprovals();
  }, [token.access_token]);

  async function decide(approvalId: number, decision: "APPROVED" | "REJECTED") {
    setBusyId(approvalId);
    setError("");
    setNotice("");
    const response = await fetch(`/api/v1/approvals/${approvalId}/decision`, {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    });
    setBusyId(null);
    if (!response.ok) {
      setError(`Unable to ${decision.toLowerCase()} this approval.`);
      return;
    }
    setNotice(`Approval ${decision.toLowerCase()}.`);
    await loadApprovals();
  }

  async function createApproval(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setNotice("");
    const response = await fetch("/api/v1/approvals", {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({
        record_type: "ticket",
        record_id: Number(recordId),
        transition_to: targetState,
        approver_user_id: Number(approverId),
      }),
    });
    if (!response.ok) {
      setError("Unable to create this approval. Verify the ticket, workflow target, and approver.");
      return;
    }
    setRecordId("");
    setApproverId("");
    setNotice("Approval requested.");
    await loadApprovals();
  }

  return <section className="section-panel">
    <div className="page-heading"><div><p className="kicker">WORK QUEUE</p><h1>Approvals</h1><p className="subheading">Review requests assigned directly to you or to one of your teams.</p></div><button className="secondary-button" onClick={() => void loadApprovals()}>Refresh</button></div>
    {error && <p className="error" role="alert">{error}</p>}
    {notice && <p className="settings-saved" role="status">{notice}</p>}
    {currentUser?.profile.name === "Administrator" && <form className="table-card admin-form-card" onSubmit={createApproval}>
      <h2>Request ticket approval</h2>
      <div className="form-grid"><label>Ticket ID<input type="number" min="1" value={recordId} onChange={(event) => setRecordId(event.target.value)} required /></label><label>Target state<input value={targetState} onChange={(event) => setTargetState(event.target.value)} required /></label><label>Approver user ID<input type="number" min="1" value={approverId} onChange={(event) => setApproverId(event.target.value)} required /></label></div>
      <button className="primary-button" type="submit">Request approval</button>
    </form>}
    <section className="table-card">
      {approvals.length === 0 ? <p className="empty-table">No pending approvals.</p> : approvals.map((approval) => <article className="catalogue-row" key={approval.id}>
        <div><strong>Ticket #{approval.record_id} → {approval.transition_to}</strong><small>Step {approval.step} · Requested {new Date(approval.created_at).toLocaleString()}</small>{approval.note && <small>{approval.note}</small>}</div>
        <div className="toolbar-actions"><button className="secondary-button" disabled={busyId === approval.id} onClick={() => void decide(approval.id, "REJECTED")}>Reject</button><button className="primary-button" disabled={busyId === approval.id} onClick={() => void decide(approval.id, "APPROVED")}>Approve</button></div>
      </article>)}
    </section>
  </section>;
}
