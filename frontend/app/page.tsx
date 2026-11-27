"use client";

import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import FilterBar from "./components/FilterBar";
import {
  FilterOptions, QueueName, TicketFilters, appendFilterParams, emptyFilters, filtersEqual, filtersFromProfile, filtersToProfile,
  hasStaffOnlyFilters, optionsFromResponse, parseUrl, serialiseUrl, stripStaffFilters, urlHasFilters,
} from "./lib/ticketFilters";
import AssignPicker from "./components/AssignPicker";
import RequesterTicketForm from "./components/RequesterTicketForm";
import AttachmentDropzone from "./components/AttachmentDropzone";
import ReplyComposer from "./components/ReplyComposer";
import { SafeHtml } from "./components/SafeHtml";
import { TicketAttachments } from "./components/TicketAttachments";
import { Popover } from "./components/Popover";
import ProfileMenu from "./components/ProfileMenu";
import SidebarNav, { NavEntry } from "./components/SidebarNav";
import { DrawerTab, DrawerTabs, TabPanel } from "./components/DrawerTabs";
import TicketActionsMenu from "./components/TicketActionsMenu";
import TicketDetailsTab from "./components/TicketDetailsTab";
import SLALiveBadge from "./components/SLALiveBadge";
import SLAStudioModal from "./components/SLAStudioModal";
import { TicketLinksTab, TicketSolutionsTab } from "./components/TicketLinksAndKedb";
import ChangeCalendarPanel from "./components/ChangeCalendarPanel";
import { ToastRegion, useToast } from "./components/Toast";
import NotificationBell from "./components/NotificationBell";
import InvitationAccept from "./components/InvitationAccept";
import UserManagementPanel from "./components/UserManagementPanel";
import { AttachmentItem, revokePreview, unsentCount, uploadItems } from "./lib/attachments";

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
  status_label?: string | null;
  priority: number;
  priority_label?: string | null;
  urgency?: number;
  impact?: number;
  opened_at: string;
  ttr_due_at?: string | null;
  assignee_id?: number | null;
  assignee_name?: string | null;
  team_id?: number | null;
  team_name?: string | null;
  category_id?: number | null;
  ticket_type_id?: number | null;
  requester_name?: string | null;
  requester_active?: boolean | null;
  requester_vip?: boolean | null;
  flagged?: boolean;
  sla_paused?: boolean;
  paused_time_seconds?: number;
  allowed_transitions?: string[];
  priority_code?: string | null;
  sla_effective_start_at?: string | null;
  first_responded_at?: string | null;
  sla_matched_tier?: string | null;
  sla_matched_reason?: string | null;
  breach_code?: string | null;
  breach_reason?: string | null;
  sla_state?: {
    health: string;
    is_paused: boolean;
    priority_code?: string;
    matched_tier?: string;
    response_breached?: boolean;
    resolution_breached?: boolean;
  } | null;
};
type TimelineItem = { kind: string; body: string; created_at: string; state?: string; is_private?: boolean };
type CurrentUser = { id: number; name: string; email: string; entity_id?: number; entity?: { id: number; name: string } | null; profile: { name: string; interface: string; rights?: Record<string, boolean> } };
type Requester = { id: number; name: string; email: string };
type Category = { id: number; name: string };
type Group = { id: number; name: string };
type TicketType = { id: number; name: string; itil_class: string; fields: Array<{ field_definition_id: number; key?: string; required: boolean; page: string }> };
type FormField = { key: string; label: string; type: string; options: Record<string, unknown>; validation: Record<string, unknown>; required: boolean; editable: boolean; default_value: unknown; page: string; show_on_portal: boolean; show_condition: Record<string, unknown> };
type FormSchema = { ticket_type_id: number; name: string; pages: Record<string, FormField[]> };
type PriorityOption = { value: number; label: string };
type AssignmentPreview = { team_name: string | null; agent_name: string | null; priority: number; sla: { sla_name: string | null; response_target: string | null; fix_target: string | null } };
type TicketWorkflow = { id?: number; entity_id?: number; record_type?: string; states: string[]; transitions: Record<string, string[]>; approval_requirements: Record<string, number>; is_active: boolean };
type WorkflowApproval = { id: number; record_type: string; record_id: number; transition_to: string; step: number; status: string; note: string | null; created_at: string };
type AnalyticsOverview = {
  total_tickets: number;
  open_tickets: number;
  resolved_tickets: number;
  high_priority_tickets: number;
  unassigned_tickets: number;
  sla_due_tickets: number;
  status_counts: Record<string, number>;
  priority_counts: Record<number, number>;
};

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
    calendar: "M19 4h-1V2h-2v2H8V2H6v2H5c-1.11 0-1.99.9-1.99 2L3 20a2 2 0 0 0 2 2h14c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2Zm0 16H5V10h14v10Z",
    chart: "M4 19V5M4 19h17M8 16v-3M12 16V8M16 16v-6M20 16v-9",
    settings: "M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7ZM19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-1.7 1.7-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5v.2h-2.4v-.2a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1L8 17l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H6.7v-2.4h.2a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.9L8 8.6l1.7-1.7.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.5v-.2h2.4v.2a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1 1.7 1.7-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.5 1h.2v2.4h-.2a1.7 1.7 0 0 0-1.5 1Z",
    search: "m20 20-4.4-4.4M17 11a6 6 0 1 1-12 0 6 6 0 0 1 12 0Z",
    bell: "M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9ZM10 21h4",
    close: "m6 6 12 12M18 6 6 18",
    arrow: "M5 12h14m-6-6 6 6-6 6",
    sun: "M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364-.707-.707M6.343 6.343l-.707-.707m12.728 0-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z",
    moon: "M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z",
    clock: "M12 6v6l4 2m6-2a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z",
    check: "M20 6 9 17l-5-5",
    flag: "M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1zm0 7v-7",
    alert: "M12 9v4m0 4h.01M21.73 18 13.73 4a2 2 0 0 0-3.48 0L2.27 18A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z",
    star: "M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z",
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24" className="icon"><path d={paths[name]} /></svg>;
}
type ThemeMode = "light" | "dark" | "system";

function useTheme() {
  const [themeMode, setThemeMode] = useState<ThemeMode>("system");
  const [resolvedTheme, setResolvedTheme] = useState<"light" | "dark">("light");

  useEffect(() => {
    const saved = (window.localStorage.getItem("svc_theme") as ThemeMode) || "system";
    setThemeMode(saved);
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const active = saved === "system" ? (media.matches ? "dark" : "light") : saved;
    setResolvedTheme(active);
    document.documentElement.setAttribute("data-theme", active);

    const onMediaChange = () => {
      const current = (window.localStorage.getItem("svc_theme") as ThemeMode) || "system";
      if (current === "system") {
        const next = media.matches ? "dark" : "light";
        setResolvedTheme(next);
        document.documentElement.setAttribute("data-theme", next);
      }
    };
    media.addEventListener("change", onMediaChange);
    return () => media.removeEventListener("change", onMediaChange);
  }, []);

  const handleThemeChange = (mode: ThemeMode) => {
    setThemeMode(mode);
    let active: "light" | "dark" = "light";
    if (mode === "system") {
      window.localStorage.removeItem("svc_theme");
      active = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    } else {
      window.localStorage.setItem("svc_theme", mode);
      active = mode;
    }
    setResolvedTheme(active);
    document.documentElement.setAttribute("data-theme", active);
  };

  const toggleTheme = () => {
    const next = resolvedTheme === "dark" ? "light" : "dark";
    handleThemeChange(next);
  };

  return { themeMode, resolvedTheme, handleThemeChange, toggleTheme };
}

export default function Home() {
  const { themeMode, resolvedTheme, handleThemeChange, toggleTheme } = useTheme();
  const [email, setEmail] = useState("admin@svcinfosys.in");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [accountActivated, setAccountActivated] = useState(false);
  const [token, setToken] = useState<TokenPair | null>(null);
  const [invitationToken, setInvitationToken] = useState("");

  useEffect(() => {
    const url = new URL(window.location.href);
    const fragmentParams = new URLSearchParams(url.hash.slice(1));
    const hasFragmentInvite = fragmentParams.has("invite");
    const hasQueryInvite = url.searchParams.has("invite");
    const invite = fragmentParams.get("invite") ?? url.searchParams.get("invite");
    if (hasFragmentInvite || hasQueryInvite) {
      url.searchParams.delete("invite");
      fragmentParams.delete("invite");
      url.hash = fragmentParams.size ? `#${fragmentParams.toString()}` : "";
      window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
      if (invite) setInvitationToken(invite);
    }
    try {
      const stored = window.localStorage.getItem("svc_auth_token");
      if (stored) {
        setToken(JSON.parse(stored) as TokenPair);
      }
    } catch { /* ignore */ }
  }, []);

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("");
    setAccountActivated(false);
    const response = await fetch("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => null);
      const detail = err && typeof err.detail === "string" ? err.detail : "";
      setMessage(detail || (response.status === 401 ? "Invalid email or password." : `Login error (HTTP ${response.status}). Please check your connection or reload.`));
      return;
    }
    const pair = (await response.json()) as TokenPair;
    try { window.localStorage.setItem("svc_auth_token", JSON.stringify(pair)); } catch { /* ignore */ }
    setToken(pair);
  }

  async function signOut(expiryNotice?: string) {
    if (token) {
      // Revoke the refresh token server-side; local sign-out proceeds even if the call fails.
      await fetch("/api/v1/auth/logout", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token.access_token}` },
        body: JSON.stringify({ refresh_token: token.refresh_token }),
      }).catch(() => undefined);
    }
    try { window.localStorage.removeItem("svc_auth_token"); } catch { /* ignore */ }
    Object.keys(window.localStorage).filter((key) => key.startsWith("svc-ticket-draft:")).forEach((key) => window.localStorage.removeItem(key));
    window.history.replaceState(null, "", window.location.pathname);
    setPassword("");
    setToken(null);
    if (typeof expiryNotice === "string") {
      setMessage(expiryNotice);
    }
  }

  if (invitationToken) return <InvitationAccept invitationToken={invitationToken} onComplete={(invitedEmail) => {
    window.history.replaceState(null, "", window.location.pathname);
    setInvitationToken("");
    setEmail(invitedEmail);
    setMessage("Your account is active. Sign in with your new password.");
    setAccountActivated(true);
  }} />;
  if (token) return <TicketWorkspace token={token} onSignOut={signOut} onTokenUpdated={setToken} themeMode={themeMode} resolvedTheme={resolvedTheme} onThemeChange={handleThemeChange} onToggleTheme={toggleTheme} />;

  return (
    <main className="login-page">
      <section className="login-panel">
        <div className="login-brand">
          <img src="/svc-logo.png" alt="SVC Infosystems" />
          <span>SVC ServiceFlow</span>
          <span className="login-brand-badge">ITSM v1.0</span>
        </div>
        <div className="login-copy">
          <p className="kicker">High-velocity IT Operations</p>
          <h1>Request to Resolution</h1>
          <p>One focused workspace for technicians, employees, and IT leadership.</p>
          <div className="preview-ticket-card">
            <div className="preview-card-head">
              <span className="preview-ticket-id">
                <span className="font-mono">INCIDENT · #0039</span>
              </span>
              <span className="preview-p1-badge">P1 HIGH</span>
            </div>
            <div className="preview-card-body">
              <h3>ERP Database Gateway Timeout</h3>
              <div className="preview-telemetry-row">
                <span className="preview-sla-target">
                  <span className="preview-sla-dot" aria-hidden="true" />
                  2h 14m remaining
                </span>
                <span className="preview-assignee">
                  <span>Assigned · Infrastructure Tier 2</span>
                </span>
              </div>
            </div>
            <div className="preview-card-footer">
              <span>Diagnostic bundle received</span>
              <span className="font-mono">14m ago</span>
            </div>
          </div>
        </div>
        <div className="login-footer">
          <span>Enterprise Service Desk</span>
          <span className="font-mono">v1.0.4</span>
        </div>
      </section>
      <section className="login-form-wrap" style={{ position: "relative" }}>
        <div style={{ position: "absolute", top: 24, right: 32 }}>
          <button
            type="button"
            className="theme-toggle-btn"
            onClick={toggleTheme}
            title={`Switch to ${resolvedTheme === "dark" ? "light" : "dark"} mode`}
            aria-label="Toggle theme"
          >
            {resolvedTheme === "dark" ? <><Icon name="sun" /> Light</> : <><Icon name="moon" /> Dark</>}
          </button>
        </div>
        <div className="login-form-card">
          <form className="login-form" onSubmit={login}>
            <div className="mobile-brand"><img src="/svc-logo.png" alt="SVC Infosystems" /><strong>SVC ServiceFlow</strong></div>
            <p className="kicker">Service Operations</p>
            <h2>Sign in to ServiceFlow</h2>
            <p className="form-help">Use your work account to continue.</p>
            <label>Work email<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} required autoComplete="email" /></label>
            <label>Password<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} required autoComplete="current-password" /></label>
            <div className="form-options">
              <label className="checkbox"><input type="checkbox" /> Remember me</label>
              <button type="button" className="text-button" style={{ color: "var(--muted)" }}>Forgot password?</button>
            </div>
            <button className="primary-button login-button" type="submit">Sign in <Icon name="arrow" /></button>
            {message && <p className={accountActivated ? "settings-saved" : "error"} role={accountActivated ? "status" : "alert"}>{message}</p>}
          </form>
        </div>
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
  })}
    </div>;
}

function TicketWorkspace({
  token,
  onSignOut,
  onTokenUpdated,
  themeMode,
  resolvedTheme,
  onThemeChange,
  onToggleTheme,
}: {
  token: TokenPair;
  onSignOut: (notice?: string) => Promise<void> | void;
  onTokenUpdated?: (fresh: TokenPair) => void;
  themeMode: ThemeMode;
  resolvedTheme: "light" | "dark";
  onThemeChange: (mode: ThemeMode) => void;
  onToggleTheme: () => void;
}) {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [analytics, setAnalytics] = useState<AnalyticsOverview | null>(null);
  const [selected, setSelected] = useState<Ticket | null>(null);
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<TicketFilters>(emptyFilters);
  const [filterOptions, setFilterOptions] = useState<FilterOptions | null>(null);
  const [filterOptionsError, setFilterOptionsError] = useState("");
  const [listLoading, setListLoading] = useState(false);
  const [urlReady, setUrlReady] = useState(false);
  const [userLoaded, setUserLoaded] = useState(false);
  const urlHadState = useRef(false);
  const urlHadQueue = useRef(false);
  const queueDefaulted = useRef(false);
  const loadSeq = useRef(0);
  const [priorityOptions, setPriorityOptions] = useState<PriorityOption[]>([]);
  const [activeNav, setActiveNav] = useState("Tickets");
  const [showDrawer, setShowDrawer] = useState(true);
  const [error, setError] = useState("");
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [requesters, setRequesters] = useState<Requester[]>([]);
  const [requesterSearch, setRequesterSearch] = useState("");
  const [selectedRequesterId, setSelectedRequesterId] = useState<number | null>(null);
  const [agents, setAgents] = useState<Requester[]>([]);
  const [assignmentMessage, setAssignmentMessage] = useState("");
  const [assignmentPickerTicketId, setAssignmentPickerTicketId] = useState<number | null>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [ticketType, setTicketType] = useState("");
  const [ticketTypes, setTicketTypes] = useState<TicketType[]>([]);
  const [formSchema, setFormSchema] = useState<FormSchema | null>(null);
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
  const [attachmentItems, setAttachmentItems] = useState<AttachmentItem[]>([]);
  const [createdTicket, setCreatedTicket] = useState<Ticket | null>(null);
  const [createError, setCreateError] = useState("");
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [slaStudioOpen, setSlaStudioOpen] = useState(false);
  const [drawerTab, setDrawerTab] = useState<DrawerTab>("activity");
  const [peopleView, setPeopleView] = useState<"all" | "technicians" | "requesters" | "users">("all");
  const { toast, show: showToast, dismiss: dismissToast } = useToast();
  const hamburgerRef = useRef<HTMLButtonElement>(null);
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
  const viewAllAccess = Boolean(currentUser?.profile.rights?.["ticket:view_all"] || currentUser?.profile.rights?.["*"]);
  const tabsAccess = queueAccess || viewAllAccess;
  const canSelfClaim = currentUser?.profile.rights?.["ticket:self_claim"] === true || currentUser?.profile.rights?.["*"] === true;
  const canViewUnassigned = currentUser?.profile.rights?.["ticket:view_unassigned"] === true || currentUser?.profile.rights?.["*"] === true;
  const canAssignTickets = Boolean(
    currentUser?.profile.rights?.["ticket:assign_any"]
    || currentUser?.profile.rights?.["ticket:bulk_edit"]
    || currentUser?.profile.rights?.["ticket:edit"]
    || currentUser?.profile.rights?.["ticket:update"]
    || currentUser?.profile.rights?.["ticket:assign"]
    || currentUser?.profile.rights?.["*"],
  );
  const canUpdateTickets = Boolean(
    currentUser?.profile.rights?.["ticket:edit"]
    || currentUser?.profile.rights?.["ticket:update"]
    || currentUser?.profile.rights?.["tickets:update"]
    || currentUser?.profile.rights?.["ticket:bulk_edit"]
    || currentUser?.profile.rights?.["*"],
  );
  // Administration visibility follows the '*' right; backend require_admin still checks the profile name (to be replaced by a named admin right in the roles/permissions step).
  const canAccessAdministration = currentUser?.profile.rights?.["*"] === true;
  const requesterAccess = Boolean(currentUser?.profile.rights?.["ticket:own_only"]);
  const draftKey = currentUser ? `svc-ticket-draft:${currentUser.id}` : null;

  const activeFilters = requesterAccess ? stripStaffFilters(filters) : filters;
  function changeFilters(next: TicketFilters) {
    setFilters(next);
    setListPage(1);
    setActiveListProfileId("");
  }

  const searchInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    function handleGlobalKeys(event: globalThis.KeyboardEvent) {
      if (event.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes((event.target as HTMLElement)?.tagName)) {
        event.preventDefault();
        searchInputRef.current?.focus();
      } else if (event.key === "Escape" && showDrawer && !showCreate && assignmentPickerTicketId === null) {
        setShowDrawer(false);
      }
    }
    window.addEventListener("keydown", handleGlobalKeys);
    return () => window.removeEventListener("keydown", handleGlobalKeys);
  }, [showDrawer, showCreate, assignmentPickerTicketId]);

  function handleHeaderSort(column: TicketListColumn) {
    if (["id", "title", "status", "priority", "opened_at"].includes(column)) {
      if (listSort === column) {
        setListDirection((current) => (current === "asc" ? "desc" : "asc"));
      } else {
        setListSort(column);
        setListDirection("desc");
      }
      setListPage(1);
    }
  }

  const tokenRef = useRef<TokenPair>(token);
  useEffect(() => {
    tokenRef.current = token;
  }, [token]);

  const apiHeaders = { Authorization: `Bearer ${token.access_token}` };
  const apiAuthorization = apiHeaders.Authorization;
  const entityName = currentUser?.entity?.name ?? "";

  const refreshingRef = useRef<Promise<string | null> | null>(null);

  const refreshToken = useCallback(async (): Promise<string | null> => {
    const currentRefreshToken = tokenRef.current?.refresh_token;
    if (!currentRefreshToken) {
      void onSignOut("Your session has expired. Please sign in again.");
      return null;
    }
    if (refreshingRef.current) return refreshingRef.current;

    refreshingRef.current = (async () => {
      try {
        const resp = await fetch("/api/v1/auth/refresh", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: currentRefreshToken }),
        });
        if (!resp.ok) {
          void onSignOut("Your session has expired. Please sign in again.");
          return null;
        }
        const fresh = (await resp.json()) as TokenPair;
        tokenRef.current = fresh;
        try { window.localStorage.setItem("svc_auth_token", JSON.stringify(fresh)); } catch { /* ignore */ }
        onTokenUpdated?.(fresh);
        return fresh.access_token;
      } catch {
        return null;
      } finally {
        refreshingRef.current = null;
      }
    })();

    return refreshingRef.current;
  }, [onSignOut, onTokenUpdated]);

  const authFetch = useCallback(
    async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const activeAccessToken = tokenRef.current?.access_token;
      const headers = new Headers(init?.headers);
      if (activeAccessToken) {
        headers.set("Authorization", `Bearer ${activeAccessToken}`);
      }

      let resp = await fetch(input, {
        ...init,
        headers,
      });

      if (resp.status === 401) {
        const freshAccessToken = await refreshToken();
        if (freshAccessToken) {
          headers.set("Authorization", `Bearer ${freshAccessToken}`);
          resp = await fetch(input, {
            ...init,
            headers,
          });
        }
      }
      return resp;
    },
    [refreshToken],
  );

  // Proactive background silent token renewal every 20 minutes
  useEffect(() => {
    const interval = window.setInterval(() => {
      void refreshToken();
    }, 20 * 60 * 1000);
    return () => window.clearInterval(interval);
  }, [refreshToken]);

  const loadAnalytics = useCallback(async () => {
    try {
      const resp = await authFetch("/api/v1/tickets/analytics/overview");
      if (resp.ok) setAnalytics((await resp.json()) as AnalyticsOverview);
    } catch { /* ignore */ }
  }, [authFetch]);

  useEffect(() => {
    if (activeNav === "Overview" || activeNav === "Reports") {
      void loadAnalytics();
    }
  }, [activeNav, loadAnalytics]);
  useEffect(() => {
    if (!mobileNavOpen) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setMobileNavOpen(false); hamburgerRef.current?.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileNavOpen]);
  useEffect(() => {
    // Deep link: ?ticket=ID opens that ticket once the user is known.
    if (!currentUser) return;
    const id = Number(new URLSearchParams(window.location.search).get("ticket"));
    if (!Number.isInteger(id) || id <= 0) return;
    void authFetch(`/api/v1/tickets/${id}`).then(async (response) => {
      if (response.ok) { await selectTicket((await response.json()) as Ticket); }
      else showToast("error", "That ticket is not available to you.");
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentUser?.id, authFetch]);
  function navigateTo(label: string) {
    setActiveNav(label);
    setMobileNavOpen(false);
  }
  const navItems: NavEntry[] = [
    {
      icon: "ticket", label: "Tickets", badge: tickets.filter((ticket) => !["SOLVED", "CLOSED"].includes(ticket.status)).length,
      children: tabsAccess ? ((viewAllAccess ? [
        ["unassigned", "Unassigned"],
        ["all", "All tickets"],
      ] : [
        ["all", "All visible"],
        ["mine", "My tickets"],
        ["teams", "My teams"],
        ...(canViewUnassigned ? [["unassigned", "Unassigned"]] : []),
      ]) as Array<[("all" | "mine" | "teams" | "unassigned"), string]>).map(([key, label]) => ({
        key, label, count: queueCounts[key], active: queueFilter === key,
        onSelect: () => { setQueueFilter(key); setListPage(1); },
      })) : undefined,
    },
    { icon: "grid", label: "Overview" },
    {
      icon: "users", label: "People",
      children: currentUser?.profile.interface === "helpdesk" ? [
        { key: "technicians", label: "Technicians", count: agents.length, active: peopleView === "technicians", onSelect: () => setPeopleView("technicians") },
        { key: "requesters", label: "Requesters", active: peopleView === "requesters", onSelect: () => setPeopleView("requesters") },
        ...(currentUser.profile.rights?.["*"] ? [{ key: "users", label: "Users", active: peopleView === "users", onSelect: () => setPeopleView("users") }] : []),
      ] : undefined,
    },
    { icon: "calendar", label: "Change Calendar" },
    { icon: "chart", label: "Reports" },
    ...(currentUser?.profile.interface === "helpdesk" ? [{ icon: "ticket", label: "Approvals" }, ...(currentUser.profile.name === "Administrator" ? [{ icon: "settings", label: "Workflows" }] : [])] : []),
  ];
  function listErrorMessage(status: number) {
    if (status === 422) return "Some filter values are not valid. Check the date range and selected options, then try again.";
    if (status === 403) return "You do not have access to one or more of the selected filters.";
    return "Unable to load tickets.";
  }
  async function loadTickets() {
    const seq = ++loadSeq.current;
    setListLoading(true);
    const params = new URLSearchParams({
      page: String(listPage),
      page_size: String(listPageSize),
      sort: listSort,
      direction: listDirection,
    });
    const filterParams = new URLSearchParams();
    appendFilterParams(filterParams, activeFilters);
    if (query.trim()) filterParams.set("search", query.trim());
    filterParams.forEach((value, key) => params.append(key, value));
    if (tabsAccess && queueFilter !== "all") params.set("queue", queueFilter);
    try {
      const response = await authFetch(`/api/v1/tickets?${params.toString()}`);
      if (seq !== loadSeq.current) return;
      if (!response.ok) { setError(listErrorMessage(response.status)); return; }
      const result = (await response.json()) as {
        items: Ticket[];
        total: number;
        priority_options: PriorityOption[];
      };
      if (seq !== loadSeq.current) return;
      setError("");
      setTickets(result.items);
      setListTotal(result.total);
      setPriorityOptions(result.priority_options ?? []);
      const countQuery = filterParams.toString() ? `?${filterParams.toString()}` : "";
      if (tabsAccess) {
        const queueResponse = await authFetch(`/api/v1/tickets/queue-counts${countQuery}`);
        if (queueResponse.ok && seq === loadSeq.current) setQueueCounts(await queueResponse.json() as typeof queueCounts);
      }
      const countsResponse = await authFetch(`/api/v1/tickets/team-counts${countQuery}`);
      if (countsResponse.ok && seq === loadSeq.current) setTeamCounts((await countsResponse.json()) as Array<{ team_id: number | null; count: number }>);
    } catch {
      if (seq === loadSeq.current) setError("Unable to load tickets.");
    } finally {
      if (seq === loadSeq.current) setListLoading(false);
    }
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
    setFilters(filtersFromProfile(config));
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
        ...filtersToProfile(activeFilters),
        sort: listSort,
        direction: listDirection,
        page_size: listPageSize,
      };
    const response = await authFetch("/api/v1/tickets/list-profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
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
    const response = await authFetch(`/api/v1/tickets/list-profiles/${profile.id}/set-default`, {
      method: "POST",
    });
    if (!response.ok) { setError("Unable to set this profile as the default."); return; }
    const updated = (await response.json()) as SavedListProfile;
    setListProfiles((profiles) => profiles.map((item) => item.kind === updated.kind ? { ...item, is_default: item.id === updated.id } : item));
    setError("");
  }

  async function selectTicket(ticket: Ticket) {
    setSelected(ticket);
    if (selected?.id !== ticket.id) setDrawerTab("activity");
    setAssignmentMessage("");
    setShowDrawer(true);
    const [detailResponse, timelineResponse] = await Promise.all([
      authFetch(`/api/v1/tickets/${ticket.id}`),
      authFetch(`/api/v1/tickets/${ticket.id}/timeline`),
    ]);
    if (detailResponse.ok) setSelected((await detailResponse.json()) as Ticket);
    if (timelineResponse.ok) setTimeline((await timelineResponse.json()) as TimelineItem[]);
  }
  async function onTicketAssigned(updated: Partial<Ticket> & Pick<Ticket, "id">) {
    setSelected((current) => current?.id === updated.id ? { ...current, ...updated } : current);
    setTickets((items) => items.map((item) => item.id === updated.id ? { ...item, ...updated } : item));
    setAssignmentMessage(`Ticket #${updated.id} assignment updated.`);
    const response = await authFetch(`/api/v1/tickets/${updated.id}/timeline`);
    if (response.ok) setTimeline((await response.json()) as TimelineItem[]);
    await loadTickets();
  }
  async function claimTicket(ticket: Ticket) {
    const previousTickets = tickets;
    const previousSelected = selected;
    setTickets((items) => items.map((item) => item.id === ticket.id ? { ...item, assignee_id: currentUser?.id ?? null, status: item.status === "NEW" ? "ASSIGNED" : item.status } : item));
    if (selected?.id === ticket.id) setSelected({ ...selected, assignee_id: currentUser?.id ?? null, status: selected.status === "NEW" ? "ASSIGNED" : selected.status });
    const response = await authFetch(`/api/v1/tickets/${ticket.id}/claim`, { method: "POST" });
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
  async function quickUpdateStatus(ticket: Ticket, newStatus: string) {
    try {
      const response = await authFetch(`/api/v1/tickets/${ticket.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: newStatus }),
      });
      if (!response.ok) {
        const detail = ((await response.json().catch(() => ({}))) as { detail?: string }).detail;
        showToast("error", detail || "Could not update status.");
        return;
      }
      showToast("success", `Ticket #${ticket.id} status updated to ${statusLabels[newStatus] ?? newStatus}.`);
      await Promise.all([selectTicket(ticket), loadTickets()]);
    } catch {
      showToast("error", "Network error updating ticket status.");
    }
  }
  useEffect(() => {
    const { filters: urlFilters, search, queue } = parseUrl(window.location.search);
    urlHadState.current = urlHasFilters(window.location.search);
    urlHadQueue.current = queue !== null;
    setFilters(urlFilters);
    setQuery(search);
    if (queue) setQueueFilter(queue);
    setUrlReady(true);
    function onPopState() {
      const next = parseUrl(window.location.search);
      setFilters(next.filters);
      setQuery(next.search);
      setQueueFilter(next.queue ?? "all");
      setListPage(1);
    }
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);
  useEffect(() => {
    if (!urlReady || !userLoaded) return;
    if (viewAllAccess && !urlHadQueue.current && !queueDefaulted.current) return;
    const queueParam = tabsAccess ? queueFilter : null;
    const next = serialiseUrl(activeFilters, query, queueParam);
    if (next === window.location.search) return;
    const current = parseUrl(window.location.search);
    const searchOnly = filtersEqual(current.filters, activeFilters) && (current.queue === queueParam || current.queue === null);
    const url = `${window.location.pathname}${next}${window.location.hash}`;
    if (searchOnly) window.history.replaceState(null, "", url); else window.history.pushState(null, "", url);
  }, [urlReady, userLoaded, filters, query, queueFilter, tabsAccess, viewAllAccess, requesterAccess]);
  useEffect(() => {
    if (requesterAccess && hasStaffOnlyFilters(filters)) setFilters(stripStaffFilters(filters));
  }, [requesterAccess, filters]);
  useEffect(() => {
    if (!urlReady || !userLoaded) return;
    void loadTickets();
  }, [urlReady, userLoaded, query, filters, listPage, listPageSize, listSort, listDirection, queueFilter, tabsAccess]);
  useEffect(() => { if (viewAllAccess && !urlHadQueue.current) { queueDefaulted.current = true; setQueueFilter("unassigned"); setListPage(1); } }, [viewAllAccess]);
  useEffect(() => {
    let active = true;
    async function loadUser() {
      try {
        const response = await authFetch("/api/v1/auth/me");
        if (response.ok) {
          const user = (await response.json()) as CurrentUser;
          if (active) setCurrentUser(user);
        }
      } catch {
        // ignore
      } finally {
        if (active) setUserLoaded(true);
      }
    }
    void loadUser();
    return () => { active = false; };
  }, [authFetch]);
  useEffect(() => {
    if (!currentUser) return;
    void authFetch("/api/v1/tickets/filter-options")
      .then((response) => response.ok ? response.json() : Promise.reject(new Error(String(response.status))))
      .then((raw) => { setFilterOptions(optionsFromResponse(raw)); setFilterOptionsError(""); })
      .catch(() => setFilterOptionsError("Filter options could not be loaded. Filtering by name is unavailable until you refresh."));
  }, [currentUser, authFetch]);
  useEffect(() => {
    if (!currentUser) return;
    void authFetch("/api/v1/tickets/list-profiles")
      .then((response) => {
        if (!response.ok) { setError("Unable to load saved ticket profiles."); return null; }
        return response.json() as Promise<SavedListProfile[]>;
      })
      .then((loaded) => {
        if (!loaded) return;
        setListProfiles(loaded);
        const defaultFilter = loaded.find((profile) => profile.kind === "filter" && profile.is_default);
        const defaultColumns = loaded.find((profile) => profile.kind === "column" && profile.is_default);
        if (defaultFilter && !urlHadState.current) applyListProfile(defaultFilter);
        if (defaultColumns && Array.isArray(defaultColumns.config.columns)) setListColumns(defaultColumns.config.columns as TicketListColumn[]);
      }, () => setError("Unable to load saved ticket profiles."));
    if (currentUser.profile.interface === "helpdesk") {
      void authFetch("/api/v1/requesters")
        .then((response) => response.ok ? response.json() : [])
        .then((users: Requester[]) => setRequesters(users));
    }
    void Promise.all([
      authFetch("/api/v1/categories").then((response) => response.ok ? response.json() : []),
      authFetch("/api/v1/groups").then((response) => response.ok ? response.json() : []),
    ]).then(([categoryList, groupList]: [Category[], Group[]]) => {
      setCategories(categoryList); setGroups(groupList);
    });
    if (currentUser.profile.interface === "helpdesk") {
      void authFetch("/api/v1/agents")
        .then((response) => response.ok ? response.json() : [])
        .then((agentList: Requester[]) => setAgents(agentList));
    }
    void authFetch("/api/v1/ticket-types")
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
  }, [currentUser, authFetch]);
  useEffect(() => {
    // Wait for the numeric id resolved from the ticket types list; the endpoint rejects anything else with 422.
    if (!/^\d+$/.test(ticketType)) { setFormSchema(null); return; }
    let cancelled = false;
    void authFetch(`/api/v1/ticket-types/${ticketType}/form`)
      .then((response) => response.ok ? response.json() : null)
      .then((schema: FormSchema | null) => {
        if (cancelled) return;
        setFormSchema(schema);
        if (schema) {
          const defaults = Object.fromEntries(Object.values(schema.pages).flat().filter((field) => field.default_value !== null).map((field) => [field.key, field.default_value]));
          setCustomFields((current) => ({ ...defaults, ...current }));
        }
      });
    return () => { cancelled = true; };
  }, [ticketType, authFetch]);
  useEffect(() => {
    if (!/^\d+$/.test(ticketType) || !showCreate) return;
    const params = new URLSearchParams({ ticket_type_id: ticketType, urgency, impact });
    void authFetch(`/api/v1/tickets/assignment-preview?${params.toString()}`)
      .then((response) => response.ok ? response.json() : null)
      .then((preview: AssignmentPreview | null) => setAssignmentPreview(preview));
  }, [ticketType, urgency, impact, showCreate, authFetch]);
  useEffect(() => {
    if (!showCreate || ticketTypes.length > 0) return;
    void authFetch("/api/v1/ticket-types")
      .then((response) => response.ok ? response.json() as Promise<TicketType[]> : [])
      .then((types: TicketType[]) => {
        if (!types || types.length === 0) return;
        setTicketTypes(types);
        if (!ticketType || !types.some((t) => String(t.id) === ticketType)) {
          const incident = types.find((item) => item.itil_class === "INCIDENT") ?? types[0];
          if (incident) setTicketType(String(incident.id));
        }
      })
      .catch(() => undefined);
  }, [showCreate, ticketTypes.length, authFetch]);
  useEffect(() => {
    if (showCreate) {
      const saved = draftKey ? window.localStorage.getItem(draftKey) : null;
      if (saved) {
        try {
          const draft = JSON.parse(saved) as { savedAt?: number; title?: string; description?: string; type?: string; fields?: Record<string, unknown> };
          if (!draft.savedAt || Date.now() - draft.savedAt > 7 * 24 * 60 * 60 * 1000) {
            window.localStorage.removeItem(draftKey!);
          } else {
            setNewTitle(draft.title ?? "");
            setNewDescription(draft.description ?? "");
            if (draft.type && /^\d+$/.test(String(draft.type))) {
              setTicketType(String(draft.type));
            } else if (!ticketType && ticketTypes.length > 0) {
              const incident = ticketTypes.find((t) => t.itil_class === "INCIDENT") ?? ticketTypes[0];
              if (incident) setTicketType(String(incident.id));
            }
            setCustomFields(draft.fields ?? {});
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
  async function createTicket(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCreateError("");
    if (createdTicket) {
      await completeUploads(createdTicket);
      return;
    }
    if (attachmentItems.some((item) => item.status === "error")) {
      setCreateError("Remove the attachments that failed validation (for example files over 10 MB) before submitting.");
      return;
    }
    const title = requesterAccess ? String(customFields.summary ?? "") : newTitle;
    const description = requesterAccess ? String(customFields.details ?? "") : newDescription;
    const ticketFields = { ...customFields };
    if (requesterAccess) {
      delete ticketFields.attachments;
      delete ticketFields.priority;
      delete ticketFields.urgency;
      delete ticketFields.impact;
      delete ticketFields.user;
      delete ticketFields.organisation;
    }
    const response = await authFetch("/api/v1/tickets", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title,
        description,
        type: ticketTypes.find((item) => String(item.id) === ticketType)?.itil_class ?? "INCIDENT",
        ticket_type_id: Number(ticketType),
        ...(requesterAccess
          ? { priority: customFields.priority }
          : { urgency: Number(urgency), impact: Number(impact) }),
        ...(requesterAccess ? {} : { category_id: categoryId ? Number(categoryId) : null }),
        ...(!requesterAccess && selectedRequesterId ? { requester_id: selectedRequesterId } : {}),
        ...(!requesterAccess && assigneeId ? { assignee_id: Number(assigneeId) } : {}),
        custom_fields: {
          ...ticketFields,
          summary: title,
          details: description,
          ...(!requesterAccess ? {
            user: selectedRequesterId ?? currentUser?.id,
            category: categoryId ? Number(categoryId) : undefined,
            urgency: Number(urgency),
            impact: Number(impact),
            agent: assigneeId ? Number(assigneeId) : undefined,
          } : {}),
        },
      }),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => null);
      const detail = err && typeof err.detail === "string" ? err.detail : (Array.isArray(err?.detail) ? err.detail.map((d: { msg?: string }) => d.msg).join(", ") : "");
      setCreateError(detail || "Unable to submit ticket. Please check required fields and try again.");
      return;
    }
    if (draftKey) window.localStorage.removeItem(draftKey);
    const ticket = (await response.json()) as Ticket;
    await completeUploads(ticket);
  }

  async function completeUploads(ticket: Ticket) {
    const ids = attachmentItems.filter((item) => item.status !== "done").map((item) => item.id);
    const failed = await uploadItems(ticket.id, ids, attachmentItems, setAttachmentItems, apiHeaders);
    if (failed) {
      setCreatedTicket(ticket);
      setCreateError(`Ticket #${ticket.id} was created, but ${failed} attachment${failed === 1 ? "" : "s"} could not be uploaded. Retry or remove them, then press Create ticket again.`);
      await loadTickets();
      return;
    }
    const defaultFields = requesterAccess && formSchema
      ? Object.fromEntries(Object.values(formSchema.pages).flat().filter((field) => field.default_value !== null).map((field) => [field.key, field.default_value]))
      : {};
    attachmentItems.forEach(revokePreview);
    setCreatedTicket(null);
    setNewTitle(""); setNewDescription(""); setAttachmentItems([]); setRequesterSearch(""); setSelectedRequesterId(null); setImpact("3"); setUrgency("3"); setCategoryId(""); setAssigneeId(""); setCustomFields(defaultFields);
    if (submitAnother) {
      setNewTitle(""); setNewDescription(""); setCustomFields(defaultFields); setSubmitAnother(false);
    } else setShowCreate(false);
    await loadTickets();
    await selectTicket(ticket);
  }

  function closeCreate() {
    if (unsentCount(attachmentItems) > 0 && !window.confirm("Some attachments have not been sent. Leave without sending them?")) return;
    attachmentItems.forEach(revokePreview);
    setAttachmentItems([]);
    setCreatedTicket(null);
    setShowCreate(false);
  }

  function renderTicketColumn(ticket: Ticket, column: TicketListColumn) {
    if (column === "title") return <div className="ticket-cell"><strong className="ticket-title-row"><span className="ticket-id-tag font-mono">#{ticket.id}</span> {ticket.requester_vip && <span className="badge-vip">VIP</span>}{ticket.flagged && <span className="badge-flag">Flagged</span>}{ticket.requester_active === false && <span className="badge-inactive">Inactive</span>}<span>{ticket.title}</span></strong><span>{ticket.description.slice(0, 62)}{ticket.description.length > 62 ? "…" : ""}</span></div>;
    if (column === "id") return <span className="ticket-id-tag font-mono">#{ticket.id}</span>;
    if (column === "status") return <span className={`status status-${ticket.status.toLowerCase()}`}><i />{ticket.status_label ?? statusLabels[ticket.status] ?? ticket.status}</span>;
    if (column === "priority") {
      const pCode = ticket.priority_code || `P${ticket.priority}`;
      return (
        <span className={`priority priority-${ticket.priority}`} style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
          <span className="font-mono" style={{ fontSize: "11px", fontWeight: 600, padding: "1px 6px", borderRadius: "4px", backgroundColor: pCode === "P1" ? "#fee2e2" : pCode === "P2" ? "#ffedd5" : "var(--badge-neutral-bg)", color: pCode === "P1" ? "#991b1b" : pCode === "P2" ? "#9a3412" : "inherit" }}>
            {pCode}
          </span>
          <span>{ticket.priority_label ?? `Priority ${ticket.priority}`}</span>
          {!requesterAccess && <b className="font-mono">{ticket.priority}</b>}
        </span>
      );
    }
    if (column === "opened_at") return new Date(ticket.opened_at).toLocaleDateString();
    if (column === "requester_name") return ticket.requester_name ?? "—";
    if (column === "requester_active") return ticket.requester_active == null ? "—" : ticket.requester_active ? "Active" : "Inactive";
    if (column === "requester_vip") return ticket.requester_vip ? <span className="badge-vip">VIP</span> : "—";
    if (column === "flagged") return ticket.flagged ? "Flagged" : "—";
    if (column === "assignee_id") return <span className="assignee"><span className="avatar avatar-orange">AT</span>{ticket.assignee_id ? "Assigned" : "Unassigned"}</span>;
    if (column === "ttr_due_at") {
      const isPaused = ticket.status === "PENDING" || ticket.sla_paused;
      return (
        <SLALiveBadge
          dueAt={ticket.ttr_due_at}
          solvedAt={["SOLVED", "CLOSED"].includes(ticket.status) ? "resolved" : undefined}
          isPaused={isPaused}
          health={ticket.sla_state?.health}
        />
      );
    }
    return null;
  }
  function clearFilters() {
    setQuery("");
    setFilters(emptyFilters);
    setListPage(1);
    setActiveListProfileId("");
  }
  const openCount = tickets.filter((ticket) => !["SOLVED", "CLOSED"].includes(ticket.status)).length;
  const resolvedCount = tickets.filter((ticket) => ["SOLVED", "CLOSED"].includes(ticket.status)).length;
  const highPriorityCount = tickets.filter((ticket) => ticket.priority <= 2).length;

  return <div className="app-shell">
    {mobileNavOpen && <button className="mobile-nav-overlay" aria-label="Close navigation" onClick={() => { setMobileNavOpen(false); hamburgerRef.current?.focus(); }} />}
    <aside id="app-sidebar" className={`sidebar ${mobileNavOpen ? "sidebar-mobile-open" : ""}`}>
      <div className="sidebar-logo"><img src="/svc-logo.png" alt="SVC Infosystems" /><span>SVC ServiceFlow</span></div>
      <Popover
        wrapClassName="workspace-switcher-wrap"
        triggerClassName="workspace-switcher"
        panelClassName="workspace-menu"
        ariaLabel="Workspace menu"
        label={(open) => <><span className="avatar avatar-indigo">SI</span><span className="workspace-name">Service desk <small>{currentUser?.profile.name ?? "Operations"}</small></span><span className={`chevron ${open ? "chevron-open" : ""}`}>⌄</span></>}
      >
        {({ close }) => <>
          <div className="workspace-menu-heading"><span className="avatar avatar-indigo">SI</span><span><strong>Service desk</strong><small>{currentUser?.email ?? "Operations workspace"}</small></span><span className="menu-check"><Icon name="check" /></span></div>
          <div className="workspace-menu-divider" />
          <button type="button" role="menuitem" onClick={() => { close(); navigateTo("Overview"); }}><Icon name="grid" /><span>Workspace overview</span></button>
          {canAccessAdministration && <button type="button" role="menuitem" onClick={() => { close(); navigateTo("Settings"); }}><Icon name="settings" /><span>Administration</span></button>}
          <button type="button" role="menuitem" onClick={() => { close(); void onSignOut(); }}><span className="menu-signout">↗</span><span>Sign out</span></button>
        </>}
      </Popover>
      <SidebarNav items={navItems} activeNav={activeNav} renderIcon={(name) => <Icon name={name} />} onNavigate={navigateTo} />
      <div className="sidebar-bottom"><button className={`nav-item ${activeNav === "Settings" ? "active" : ""}`} onClick={() => navigateTo("Settings")}><Icon name="settings" /><span>Settings</span></button><div className="user-card"><span className="avatar avatar-slate">{currentUser?.name.slice(0, 2).toUpperCase() ?? "AD"}</span><span><strong>{currentUser?.name ?? "Admin"}</strong><small>{currentUser?.profile.name ?? "Administrator"}</small></span><button aria-label="Sign out" onClick={() => void onSignOut()}>↗</button></div></div>
    </aside>
    <main className="main-canvas">
      <header className="topbar"><div className="topbar-leading"><button ref={hamburgerRef} className="mobile-menu-button icon-button" aria-label={mobileNavOpen ? "Close navigation" : "Open navigation"} aria-expanded={mobileNavOpen} aria-controls="app-sidebar" onClick={() => setMobileNavOpen((open) => !open)}><span /><span /><span /></button><div className="breadcrumb">Workspace <span>/</span> <strong>{activeNav}</strong></div></div><div className="topbar-actions">
          {!requesterAccess && (
            <button
              type="button"
              className="sla-topbar-btn"
              title="SLA Studio & Analytics"
              aria-label="SLA Configuration Studio"
              onClick={() => setSlaStudioOpen(true)}
            >
              <Icon name="clock" />
              <span>SLA Studio</span>
            </button>
          )}<button type="button" className="theme-toggle-btn" title={`Switch to ${resolvedTheme === "dark" ? "light" : "dark"} mode`} aria-label="Toggle theme" onClick={onToggleTheme}><Icon name={resolvedTheme === "dark" ? "sun" : "moon"} /></button><NotificationBell authorization={apiAuthorization} userId={currentUser?.id} fetchFn={authFetch} onOpenTicket={(id) => { setActiveNav("Tickets"); void authFetch(`/api/v1/tickets/${id}`).then(async (response) => { if (response.ok) await selectTicket((await response.json()) as Ticket); else showToast("error", "That ticket is no longer available to you."); }); }} /><ProfileMenu user={currentUser} authorization={apiAuthorization} onSignOut={onSignOut} /></div></header>
      <div className="page-content">
        {activeNav === "Settings" ? <SettingsPanel currentUser={currentUser} adminToken={token} themeMode={themeMode} onThemeChange={onThemeChange} /> : activeNav === "Workflows" ? <WorkflowPanel token={token} /> : activeNav === "Approvals" ? <ApprovalsPanel token={token} currentUser={currentUser} /> : activeNav === "Change Calendar" ? <ChangeCalendarPanel tickets={tickets} onSelectTicket={selectTicket} authFetch={authFetch} onRefreshTickets={loadTickets} /> : activeNav === "Overview" ? <OverviewPanel tickets={tickets} openCount={openCount} resolvedCount={resolvedCount} highPriorityCount={highPriorityCount} analytics={analytics} onRefresh={loadAnalytics} /> : activeNav === "People" ? <PeoplePanel currentUser={currentUser} requesters={requesters} agents={agents} view={peopleView} token={token} /> : activeNav === "Reports" ? <ReportsPanel tickets={tickets} priorityOptions={priorityOptions} requester={requesterAccess} analytics={analytics} onRefresh={loadAnalytics} /> : <>
        <div className="page-heading"><div><p className="kicker">Service Operations</p><h1>{activeNav === "Tickets" ? "Tickets" : activeNav}</h1><p className="subheading">{currentUser?.profile.interface === "self_service" ? "Report an incident or request a service, then track your tickets here." : queueAccess ? "Tickets assigned to you, your teams, or awaiting a technician." : "Manage, prioritize, and resolve requests across your organization."}</p></div><button className="primary-button" onClick={() => setShowCreate(true)}><span>+</span> Create ticket</button></div>
        <div className="metric-row"><div className="metric"><span className="metric-label">Open tickets</span><strong>{openCount}</strong><small className="metric-up">+12% <span>vs last week</span></small></div><div className="metric"><span className="metric-label">Awaiting response</span><strong>{tickets.filter((ticket) => ticket.status === "PENDING").length}</strong><small>Needs attention</small></div><div className="metric"><span className="metric-label">SLA compliance</span><strong>94.2%</strong><small className="metric-up">+2.4% <span>vs last month</span></small></div><div className="metric"><span className="metric-label">Avg. resolution</span><strong>6h 24m</strong><small>Across all tickets</small></div></div>
        <section className="table-card">
          {tabsAccess && <div className="list-controls" aria-label="Ticket queues">
            {(viewAllAccess ? [
              ["unassigned", "Unassigned", queueCounts.unassigned],
              ["all", "All tickets", queueCounts.all],
            ] as const : [
              ["all", "All visible", queueCounts.all],
              ["mine", "My tickets", queueCounts.mine],
              ["teams", "My teams", queueCounts.teams],
              ...(canViewUnassigned ? [["unassigned", "Unassigned", queueCounts.unassigned] as const] : []),
            ] as const).map(([queue, label, count]) => <button key={queue} type="button" className={`secondary-button ${queueFilter === queue ? "button-active" : ""}`} aria-pressed={queueFilter === queue} onClick={() => { setQueueFilter(queue); setListPage(1); }}>{label} <b>{count}</b></button>)}
          </div>}
          <div className="team-counts" aria-label="Team ticket counts">{teamCounts.map((team) => <span className="team-count" key={team.team_id ?? "unassigned"}>Team {team.team_id ?? "Unassigned"} <b>{team.count}</b></span>)}</div>
          <div className="table-toolbar">
            <div className="search-box">
              <Icon name="search" />
              <input
                ref={searchInputRef}
                value={query}
                aria-label="Search tickets"
                onChange={(event) => { setQuery(event.target.value); setListPage(1); }}
                placeholder="Search tickets by ID, title, or keyword (Press '/' to search)"
              />
            </div>
            <div className="toolbar-actions">
              <Popover
                wrapClassName="toolbar-menu-wrap"
                triggerClassName="secondary-button"
                panelClassName="view-options-popover"
                ariaLabel="View & Display Options"
                label={() => <><Icon name="settings" /><span>View</span></>}
              >
                {() => (
                  <div className="view-options-panel">
                    <div className="view-section">
                      <span className="view-section-title">Sort by</span>
                      <div className="view-row">
                        <select value={listSort} onChange={(e) => { setListSort(e.target.value); setListPage(1); }}>
                          <option value="opened_at">Opened date</option>
                          <option value="priority">Priority</option>
                          <option value="status">Status</option>
                          <option value="title">Title</option>
                          <option value="id">ID</option>
                        </select>
                        <button type="button" className="secondary-button" style={{ minWidth: 65 }} onClick={() => setListDirection((curr) => curr === "asc" ? "desc" : "asc")}>
                          {listDirection === "asc" ? "↑ Asc" : "↓ Desc"}
                        </button>
                      </div>
                    </div>
                    <div className="view-section">
                      <span className="view-section-title">Page size</span>
                      <select value={listPageSize} onChange={(e) => { setListPageSize(Number(e.target.value)); setListPage(1); }}>
                        <option value="10">10 per page</option>
                        <option value="25">25 per page</option>
                        <option value="50">50 per page</option>
                        <option value="100">100 per page</option>
                      </select>
                    </div>
                    {!requesterAccess && (
                      <div className="view-section">
                        <span className="view-section-title">Saved views</span>
                        <div className="view-row">
                          <select value={activeListProfileId} onChange={(e) => applyListProfile(listProfiles.find((p) => String(p.id) === e.target.value))}>
                            <option value="">Default queue view</option>
                            {listProfiles.map((p) => <option key={p.id} value={p.id}>{p.name}{p.is_default ? " (default)" : ""}</option>)}
                          </select>
                          <button type="button" className="secondary-button" disabled={!activeListProfileId || listProfiles.find((p) => String(p.id) === activeListProfileId)?.is_default} onClick={() => void setDefaultListProfile()}>
                            Default
                          </button>
                        </div>
                      </div>
                    )}
                    <div className="view-section">
                      <span className="view-section-title">Visible columns</span>
                      <div className="view-columns-grid">
                        {ticketListColumns.map(({ key, label }) => (
                          <label key={key} className="view-column-check">
                            <input
                              type="checkbox"
                              checked={listColumns.includes(key)}
                              onChange={(e) => setListColumns((cols) => e.target.checked ? [...cols, key] : cols.length > 1 ? cols.filter((c) => c !== key) : cols)}
                            />
                            <span>{label}</span>
                          </label>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </Popover>
              <Popover wrapClassName="toolbar-menu-wrap" triggerClassName="more-button" panelClassName="more-popover" ariaLabel="More ticket actions" label="•••">
                {({ close }) => <><button type="button" role="menuitem" onClick={() => { close(); void loadTickets(); }}>Refresh tickets</button><button type="button" role="menuitem" onClick={() => { close(); clearFilters(); }}>Clear all filters</button></>}
              </Popover>
            </div>
          </div>
          <FilterBar
            filters={activeFilters}
            options={filterOptions}
            optionsError={filterOptionsError}
            staff={!requesterAccess}
            search={query}
            total={listTotal}
            loading={listLoading}
            onChange={changeFilters}
            onClearSearch={() => { setQuery(""); setListPage(1); }}
            onClearAll={clearFilters}
          />
          {assignmentMessage && <p className="success-message" role="status">{assignmentMessage}</p>}
          {error && <p className="error" role="alert">{error}</p>}
          <div className="table-meta"><span><strong>{listTotal}</strong> {listTotal === 1 ? "ticket" : "tickets"}</span></div>
          <div className="ticket-table-wrap">
            <table>
              <thead>
                <tr>
                  <th className="check-col"><input type="checkbox" aria-label="Select all tickets" /></th>
                  {listColumns.map((column) => {
                    const isSortable = ["id", "title", "status", "priority", "opened_at"].includes(column);
                    return (
                      <th
                        key={column}
                        className={isSortable ? `th-sortable ${listSort === column ? "th-sorted" : ""}` : ""}
                        onClick={() => isSortable && handleHeaderSort(column)}
                        title={isSortable ? `Click to sort by ${ticketListColumns.find((item) => item.key === column)?.label ?? column}` : undefined}
                      >
                        <span className="th-content">
                          {ticketListColumns.find((item) => item.key === column)?.label ?? column}
                          {isSortable && listSort === column && (
                            <span className="sort-arrow">{listDirection === "asc" ? " ↑" : " ↓"}</span>
                          )}
                        </span>
                      </th>
                    );
                  })}
                  <th />
                </tr>
              </thead>
              <tbody>
                {tickets.map((ticket) => (
                  <tr key={ticket.id} className={selected?.id === ticket.id ? "row-selected" : ""} onClick={() => void selectTicket(ticket)}>
                    <td className="check-col" onClick={(event) => event.stopPropagation()}><input type="checkbox" aria-label={`Select ticket ${ticket.id}`} /></td>
                    {listColumns.map((column) => <td key={column}>{renderTicketColumn(ticket, column)}</td>)}
                    <td>
                      {queueAccess && canSelfClaim && !ticket.assignee_id && <button className="secondary-button" onClick={(event) => { event.stopPropagation(); void claimTicket(ticket); }}>Claim</button>}
                      {canAssignTickets && <button className="secondary-button" onClick={(event) => { event.stopPropagation(); void selectTicket(ticket); setAssignmentPickerTicketId(ticket.id); }}>Assign</button>}
                      <button className="row-menu" onClick={(event) => event.stopPropagation()}>•••</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!tickets.length && !listLoading && (
              <div className="empty-table">
                <strong>{queueAccess ? "No tickets in this queue" : "No tickets found"}</strong>
                <span>{error ? "The list could not be loaded. Adjust the filters and try again." : "No tickets match the current filters. Remove a filter or use Clear all."}</span>
              </div>
            )}
            {listLoading && !tickets.length && <div className="empty-table" role="status"><strong>Loading tickets…</strong></div>}
          </div>
          <div className="pagination-controls">
            <span className="pagination-info">
              Showing {listTotal > 0 ? (listPage - 1) * listPageSize + 1 : 0}–{Math.min(listPage * listPageSize, listTotal)} of {listTotal} tickets
            </span>
            <div className="pagination-actions">
              <button
                type="button"
                className="secondary-button pagination-btn"
                disabled={listPage <= 1}
                onClick={() => setListPage((page) => Math.max(1, page - 1))}
              >
                Previous
              </button>
              <span className="pagination-page-badge">
                Page {listPage} of {Math.max(1, Math.ceil(listTotal / listPageSize))}
              </span>
              <button
                type="button"
                className="secondary-button pagination-btn"
                disabled={listPage * listPageSize >= listTotal}
                onClick={() => setListPage((page) => page + 1)}
              >
                Next
              </button>
            </div>
          </div>
        </section>
        </>}
      </div>
    </main>
    {assignmentPickerTicketId !== null && currentUser && (() => {
      const ticket = selected?.id === assignmentPickerTicketId
        ? selected
        : tickets.find((item) => item.id === assignmentPickerTicketId);
      return ticket ? <AssignPicker
        ticketId={ticket.id}
        token={token}
        currentUserId={currentUser.id}
        currentAssigneeId={ticket.assignee_id ?? null}
        currentAssigneeName={ticket.assignee_name}
        currentTeamId={ticket.team_id}
        currentTeamName={ticket.team_name}
        teams={groups}
        onClose={() => setAssignmentPickerTicketId(null)}
        onAssigned={onTicketAssigned}
      /> : null;
    })()}
    {showDrawer && selected && <aside className="detail-drawer">
      <div className="drawer-header"><span className="kicker">TICKET #{selected.id}</span><button className="icon-button" onClick={() => setShowDrawer(false)} aria-label="Close details (Esc)"><Icon name="close" /></button></div>
      <div className="drawer-title">
        <div className="drawer-title-row">
          <h2>{selected.title}</h2>
          <span className={`status status-${selected.status.toLowerCase()}`}><i />{selected.status_label ?? statusLabels[selected.status] ?? selected.status}</span>
        </div>
        <div className="drawer-actions-toolbar">
          {queueAccess && canSelfClaim && !selected.assignee_id && <button className="secondary-button" onClick={() => void claimTicket(selected)}>Claim</button>}
          {canAssignTickets && <button className="primary-button assign-primary" onClick={() => setAssignmentPickerTicketId(selected.id)}>{selected.assignee_id ? "Reassign" : "Assign"}</button>}
          {canUpdateTickets && (selected.status === "ASSIGNED" || selected.status === "PLANNED") && <><button className="secondary-button" title="Put ticket on hold (pauses SLA)" onClick={() => void quickUpdateStatus(selected, "PENDING")}>On hold</button><button className="secondary-button" title="Resolve ticket" onClick={() => void quickUpdateStatus(selected, "SOLVED")}>Resolve</button></>}
          {canUpdateTickets && selected.status === "PENDING" && <><button className="primary-button" style={{ background: "var(--accent)" }} title="Resume active work (resumes SLA)" onClick={() => void quickUpdateStatus(selected, "ASSIGNED")}>Resume</button><button className="secondary-button" title="Resolve ticket" onClick={() => void quickUpdateStatus(selected, "SOLVED")}>Resolve</button></>}
          {canUpdateTickets && selected.status === "SOLVED" && <><button className="secondary-button" title="Close ticket" onClick={() => void quickUpdateStatus(selected, "CLOSED")}>Close</button><button className="secondary-button" title="Reopen ticket" onClick={() => void quickUpdateStatus(selected, "ASSIGNED")}>Reopen</button></>}
          <TicketActionsMenu ticket={selected} authorization={apiAuthorization} canAssign={canAssignTickets} canUpdate={canUpdateTickets} staff={!requesterAccess} priorityOptions={priorityOptions} ticketTypes={ticketTypes} onAssign={() => setAssignmentPickerTicketId(selected.id)} onSuccess={async (message) => { showToast("success", message); await Promise.all([selectTicket(selected), loadTickets()]); }} onError={(message) => showToast("error", message)} />
        </div>
      </div><div className="assignment-summary"><span>Raised by: <strong>{selected.requester_name ?? "Unknown"}</strong></span><span>Assigned to: <strong>{selected.assignee_name || (selected.assignee_id ? `User #${selected.assignee_id}` : "Unassigned")}</strong></span><span>Type: <strong>{ticketTypes.find((item) => item.id === selected.ticket_type_id)?.name ?? "—"}</strong></span><span>Created: <strong>{new Date(selected.opened_at).toLocaleString()}</strong></span><span>Priority: <strong>{selected.priority_label ?? `Priority ${selected.priority}`}{requesterAccess ? "" : ` (${selected.priority})`}</strong></span>{!requesterAccess && <span>Team: <strong>{selected.team_name || "Unassigned"}</strong></span>}</div><DrawerTabs tab={drawerTab} onChange={setDrawerTab} /><div className="drawer-content"><TabPanel tabId="details" tab={drawerTab}><TicketDetailsTab ticket={selected} authorization={apiAuthorization} staff={!requesterAccess} categories={categories} ticketTypes={ticketTypes} entityName={entityName} onRefresh={() => selectTicket(selected)} /></TabPanel><TabPanel tabId="activity" tab={drawerTab}><section className="drawer-section"><h3>Description</h3><SafeHtml className="ticket-description" html={selected.description} /></section><TicketAttachments ticketId={selected.id} headers={apiHeaders} /><section className="drawer-section"><h3>Conversation</h3><div className="timeline">{timeline.map((item, index) => <article key={`${item.kind}-${index}`} className={item.is_private ? "private-note" : ""}><span className="timeline-dot" /><div className="timeline-head"><strong>{item.is_private ? "Internal note" : item.kind}</strong><time>{new Date(item.created_at).toLocaleString()}</time></div><p>{item.body}</p></article>)}{!timeline.length && <p className="muted">No activity yet.</p>}</div></section><ReplyComposer ticketId={selected.id} headers={apiHeaders} onSent={() => selectTicket(selected)} /><section className="drawer-section metadata"><h3>Ticket details</h3><dl><div><dt>Requester</dt><dd>{selected.requester_name ?? "Unknown"}</dd></div><div><dt>Category</dt><dd>{selected.category_id ? `Category ${selected.category_id}` : "Uncategorized"}</dd></div><div><dt>Opened</dt><dd>{new Date(selected.opened_at).toLocaleDateString()}</dd></div><div><dt>Assignee</dt><dd>{selected.assignee_name || (selected.assignee_id ? `User #${selected.assignee_id}` : "Unassigned")}</dd></div>{!requesterAccess && <div><dt>Team</dt><dd>{selected.team_name || "Unassigned"}</dd></div>}</dl></section></TabPanel><TabPanel tabId="links" tab={drawerTab}><TicketLinksTab ticketId={selected.id} ticketTitle={selected.title} authorization={apiAuthorization} onRefreshTicket={() => selectTicket(selected)} showToast={showToast} /></TabPanel><TabPanel tabId="solutions" tab={drawerTab}><TicketSolutionsTab ticketId={selected.id} ticketTitle={selected.title} authorization={apiAuthorization} onRefreshTicket={() => selectTicket(selected)} showToast={showToast} /></TabPanel></div></aside>}
    <ToastRegion toast={toast} onDismiss={dismissToast} />
    {showCreate && (
      <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closeCreate(); }}>
        <form
          className="create-modal"
          onSubmit={createTicket}
          onKeyDown={(event) => {
            if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
              event.preventDefault();
              event.currentTarget.requestSubmit();
            }
          }}
        >
          <div className="modal-header">
            <div><p className="kicker">NEW REQUEST</p><h2>Create a ticket</h2></div>
            <button type="button" className="icon-button" onClick={closeCreate} aria-label="Close"><Icon name="close" /></button>
          </div>
          {requesterAccess ? (
            <RequesterTicketForm
              ticketTypes={ticketTypes}
              ticketType={ticketType}
              onTicketTypeChange={setTicketType}
              schema={formSchema}
              values={customFields}
              onValuesChange={setCustomFields}
              items={attachmentItems}
              setItems={setAttachmentItems}
              headers={apiHeaders}
              createdTicketId={createdTicket?.id ?? null}
            />
          ) : (
            <>
              <label>
                Ticket type
                <select value={ticketType} onChange={(event) => setTicketType(event.target.value)} required>
                  <option value="">Select a request type</option>
                  {ticketTypes.map((type) => <option key={type.id} value={type.id}>{type.itil_class === "INCIDENT" ? "Report an issue · Incident" : type.itil_class === "REQUEST" ? "Request a service · Service Request" : type.name}</option>)}
                </select>
              </label>
              <label>
                Requester
                <input
                  list="requester-options"
                  value={requesterSearch}
                  onChange={(event) => {
                    const value = event.target.value;
                    setRequesterSearch(value);
                    const match = requesters.find((requester) => `${requester.name} — ${requester.email}` === value);
                    setSelectedRequesterId(match?.id ?? null);
                  }}
                  placeholder="Search by name or email"
                />
                <datalist id="requester-options">{requesters.map((requester) => <option key={requester.id} value={`${requester.name} — ${requester.email}`} />)}</datalist>
                {selectedRequesterId && (() => {
                  const requester = requesters.find((item) => item.id === selectedRequesterId);
                  return requester ? <span className="requester-card"><strong>{requester.name}</strong><small>{requester.email} · Organisation: current entity · Active requester</small><span className="badge-vip">VIP</span></span> : null;
                })()}
              </label>
              <label>Summary<input value={newTitle} onChange={(event) => setNewTitle(event.target.value)} minLength={5} required placeholder="Summarize the issue" /></label>
              <label>Description<textarea value={newDescription} onChange={(event) => setNewDescription(event.target.value)} minLength={10} required placeholder="Tell the support team what happened and what you need." /></label>
              {formSchema
                ? <DynamicFields schema={formSchema} values={customFields} onChange={(key, value) => setCustomFields((current) => ({ ...current, [key]: value }))} portal={false} />
                : <p className="form-help">Loading the selected ticket form…</p>}
              <AttachmentDropzone items={attachmentItems} setItems={setAttachmentItems} headers={apiHeaders} ticketId={createdTicket?.id ?? null} />
            </>
          )}
          {createError && <p className="error" role="alert">{createError}</p>}
          {!requesterAccess && <aside className="new-ticket-sidebar" aria-label="Ticket preview">
            <p className="kicker">LIVE ROUTING</p>
            <div className={`priority-preview priority-${assignmentPreview?.priority ?? 3}`}>Priority {assignmentPreview?.priority ?? "—"}</div>
            <dl>
              {assignmentPreview?.team_name && <div><dt>Suggested team</dt><dd>{assignmentPreview.team_name}</dd></div>}
              {assignmentPreview?.agent_name && <div><dt>Suggested agent</dt><dd>{assignmentPreview.agent_name}</dd></div>}
              {assignmentPreview?.sla.sla_name && <div><dt>SLA</dt><dd>{assignmentPreview.sla.sla_name}</dd></div>}
              <div><dt>Response target</dt><dd>{assignmentPreview?.sla.response_target ? new Date(assignmentPreview.sla.response_target).toLocaleString() : "Pending selection"}</dd></div>
              <div><dt>Fix target</dt><dd>{assignmentPreview?.sla.fix_target ? new Date(assignmentPreview.sla.fix_target).toLocaleString() : "Pending selection"}</dd></div>
            </dl>
            <p className="kicker">KNOWLEDGE SUGGESTIONS</p>
            <small>{newTitle.trim() ? "Search suggestions will appear as knowledge articles are connected to this summary." : "Start typing a Summary to see suggested articles."}</small>
          </aside>}
          <div className="modal-actions">
            <label className="checkbox"><input type="checkbox" checked={submitAnother} onChange={(event) => setSubmitAnother(event.target.checked)} /> Create another</label>
            <button type="button" className="secondary-button" onClick={() => { if (!newTitle && !newDescription || window.confirm("Discard this ticket draft?")) closeCreate(); }}>Cancel</button>
            <button type="button" className="secondary-button" onClick={saveDraft}>Save draft</button>
            <button type="submit" className="primary-button" disabled={!formSchema}>Submit ticket <Icon name="arrow" /></button>
          </div>
        </form>
      </div>
    )}
        <SLAStudioModal
        authorization={apiAuthorization}
        isOpen={slaStudioOpen}
        onClose={() => setSlaStudioOpen(false)}
      />
    </div>;
}

function OverviewPanel({
  tickets,
  openCount,
  resolvedCount,
  highPriorityCount,
  analytics,
  onRefresh,
}: {
  tickets: Ticket[];
  openCount: number;
  resolvedCount: number;
  highPriorityCount: number;
  analytics?: AnalyticsOverview | null;
  onRefresh?: () => void;
}) {
  const statusCounts = analytics?.status_counts
    ?? tickets.reduce<Record<string, number>>((counts, ticket) => ({ ...counts, [ticket.status]: (counts[ticket.status] ?? 0) + 1 }), {});
  const total = analytics?.total_tickets ?? tickets.length;
  const open = analytics?.open_tickets ?? openCount;
  const resolved = analytics?.resolved_tickets ?? resolvedCount;
  const highPriority = analytics?.high_priority_tickets ?? highPriorityCount;

  return (
    <section className="section-panel">
      <div className="page-heading">
        <div>
          <p className="kicker">SERVICE OPERATIONS</p>
          <h1>Workspace overview</h1>
          <p className="subheading">Monitor service health and the work requiring attention across the entire organization.</p>
        </div>
        {onRefresh && <button className="secondary-button" onClick={onRefresh}>Refresh analytics</button>}
      </div>
      <div className="metric-row">
        <div className="metric"><span className="metric-label">Open tickets</span><strong>{open}</strong><small>Needs attention</small></div>
        <div className="metric"><span className="metric-label">Resolved tickets</span><strong>{resolved}</strong><small>Current workspace</small></div>
        <div className="metric"><span className="metric-label">High priority</span><strong>{highPriority}</strong><small>Priority 1 or 2</small></div>
        <div className="metric"><span className="metric-label">Total requests</span><strong>{total}</strong><small>Database total</small></div>
      </div>
      <div className="panel-grid">
        <section className="table-card panel-card">
          <div className="panel-card-heading"><h2>Ticket status</h2><span>Live database distribution</span></div>
          {Object.entries(statusCounts).map(([status, count]) => (
            <div className="bar-row" key={status}>
              <span>{statusLabels[status] ?? status}</span>
              <b><i style={{ width: `${total ? Math.max(8, (count / total) * 100) : 0}%` }} /></b>
              <strong>{count}</strong>
            </div>
          ))}
        </section>
        <section className="table-card panel-card">
          <div className="panel-card-heading"><h2>Recent activity</h2><span>Latest requests</span></div>
          {tickets.slice(0, 5).map((ticket) => (
            <div className="activity-row" key={ticket.id}>
              <span className={`status status-${ticket.status.toLowerCase()}`}><i />#{ticket.id}</span>
              <strong>{ticket.title}</strong>
              <small>{ticket.status_label ?? statusLabels[ticket.status] ?? ticket.status}</small>
            </div>
          ))}
        </section>
      </div>
    </section>
  );
}

function PeoplePanel({ currentUser, requesters, agents, view, token }: { currentUser: CurrentUser | null; requesters: Requester[]; agents: Requester[]; view: "all" | "technicians" | "requesters" | "users"; token: TokenPair }) {
  if (view === "users" && currentUser?.profile.rights?.["*"]) {
    return <section className="section-panel"><div className="page-heading"><div><p className="kicker">People and Teams</p><h1>User management</h1><p className="subheading">Invite users, manage account status, and assign profiles in your entity.</p></div></div><UserManagementPanel authorization={`Bearer ${token.access_token}`} currentUserId={currentUser.id} /></section>;
  }
  const everyone = [...agents, ...requesters.filter((requester) => !agents.some((agent) => agent.id === requester.id))];
  const people = view === "technicians" ? agents : view === "requesters" ? everyone.filter((person) => !agents.some((agent) => agent.id === person.id)) : everyone;
  return <section className="section-panel"><div className="page-heading"><div><p className="kicker">People and Teams</p><h1>People</h1><p className="subheading">Manage requesters and service desk participants in your workspace.</p></div></div><div className="table-card people-card">{currentUser?.profile.interface !== "helpdesk" ? <div className="empty-table"><strong>{currentUser?.name}</strong><span>Requester profile and ticket access are managed by your service desk.</span></div> : people.length ? people.map((person) => <div className="person-row" key={person.id}><span className="avatar avatar-indigo">{person.name.slice(0, 2).toUpperCase()}</span><div><strong>{person.name}</strong><small>{person.email}</small></div><span className="person-role">{agents.some((agent) => agent.id === person.id) ? "Technician" : "Requester"}</span></div>) : <div className="empty-table"><strong>No people found</strong><span>Active users in this entity will appear here.</span></div>}</div></section>;
}

function ReportsPanel({
  tickets,
  priorityOptions,
  requester,
  analytics,
  onRefresh,
}: {
  tickets: Ticket[];
  priorityOptions: PriorityOption[];
  requester: boolean;
  analytics?: AnalyticsOverview | null;
  onRefresh?: () => void;
}) {
  const priorities = requester
    ? [
      { label: "Critical", values: [1] },
      { label: "High", values: [2] },
      { label: "Medium", values: [3] },
      { label: "Low", values: [4, 5] },
    ]
    : priorityOptions.map(({ value, label }) => ({ label: `${label} (${value})`, values: [value] }));

  const total = analytics?.total_tickets ?? tickets.length;
  const resolved = analytics?.resolved_tickets ?? tickets.filter((t) => ["SOLVED", "CLOSED"].includes(t.status)).length;
  const unassigned = analytics?.unassigned_tickets ?? tickets.filter((t) => !t.assignee_id).length;
  const slaDue = analytics?.sla_due_tickets ?? tickets.filter((t) => t.ttr_due_at).length;
  const completionRate = total ? Math.round((resolved / total) * 100) : 0;

  return (
    <section className="section-panel">
      <div className="page-heading">
        <div>
          <p className="kicker">SERVICE ANALYTICS</p>
          <h1>Reports</h1>
          <p className="subheading">Understand volume, priority, and workload across all organization tickets.</p>
        </div>
        {onRefresh && <button className="secondary-button" onClick={onRefresh}>Refresh report</button>}
      </div>
      <div className="metric-row">
        <div className="metric"><span className="metric-label">Ticket volume</span><strong>{total}</strong><small>Total in database</small></div>
        <div className="metric"><span className="metric-label">SLA due</span><strong>{slaDue}</strong><small>Tickets with deadlines</small></div>
        <div className="metric"><span className="metric-label">Unassigned</span><strong>{unassigned}</strong><small>Awaiting ownership</small></div>
        <div className="metric"><span className="metric-label">Completion</span><strong>{completionRate}%</strong><small>Solved or closed</small></div>
      </div>
      <section className="table-card report-card">
        <div className="panel-card-heading"><h2>Tickets by priority</h2><span>Database distribution</span></div>
        {priorities.map(({ label, values }) => {
          const count = analytics?.priority_counts
            ? values.reduce((sum, v) => sum + (analytics.priority_counts[v] ?? 0), 0)
            : tickets.filter((ticket) => values.includes(ticket.priority)).length;
          return (
            <div className="bar-row" key={label}>
              <span>{label}</span>
              <b><i style={{ width: `${total ? Math.max(count ? 8 : 0, (count / total) * 100) : 0}%` }} /></b>
              <strong>{count}</strong>
            </div>
          );
        })}
      </section>
    </section>
  );
}

function SettingsPanel({
  currentUser,
  adminToken,
  themeMode,
  onThemeChange,
}: {
  currentUser: CurrentUser | null;
  adminToken: TokenPair;
  themeMode?: ThemeMode;
  onThemeChange?: (mode: ThemeMode) => void;
}) {
  const [emailUpdates, setEmailUpdates] = useState(true);
  const [compactTables, setCompactTables] = useState(false);
  const [saved, setSaved] = useState(false);
  const hasAdminRights = Boolean(
    currentUser?.profile.rights?.["ticket:view_all"]
    || currentUser?.profile.rights?.["*"],
  );

  function saveSettings() {
    setSaved(true);
    window.setTimeout(() => setSaved(false), 2200);
  }

  type AdminField = { id: number; key: string; label: string; type: string; options: Record<string, unknown>; validation: Record<string, unknown>; is_system: boolean };
  type AdminTicketType = { id: number; name: string; itil_class: string; default_priority: number; default_sla_id?: number | null; color?: string | null; fields: Array<{ field_definition_id: number; order: number; page: string; required: boolean; visible_to_roles: string[]; editable_by_roles: string[]; default_value: unknown; show_on_portal: boolean; show_condition: Record<string, unknown> }> };
  type AdminCategory = { id: number; name: string; parent_id?: number | null; entity_id: number };

  function AdminPanel({ token }: { token: TokenPair }) {
    const headers = { Authorization: `Bearer ${token.access_token}` };
    const [section, setSection] = useState("Ticket types");
    const [fields, setFields] = useState<AdminField[]>([]);
    const [types, setTypes] = useState<AdminTicketType[]>([]);
    const [adminCategories, setAdminCategories] = useState<AdminCategory[]>([]);
    const [newCategory, setNewCategory] = useState({ name: "", parent_id: "" });
    const [selectedType, setSelectedType] = useState<AdminTicketType | null>(null);
    const [draft, setDraft] = useState<AdminTicketType | null>(null);
    const [matrix, setMatrix] = useState<number[][]>([]);
    const [notice, setNotice] = useState("");
    const [dirty, setDirty] = useState(false);
    const [draggedIndex, setDraggedIndex] = useState<number | null>(null);
    const [newField, setNewField] = useState({ key: "", label: "", type: "text" });

    async function loadAdminData() {
      const [fieldResponse, typeResponse, matrixResponse, categoryResponse] = await Promise.all([
        fetch("/api/v1/field-definitions", { headers }),
        fetch("/api/v1/ticket-types", { headers }),
        fetch("/api/v1/priority-matrix", { headers }),
        fetch("/api/v1/categories", { headers }),
      ]);
      if (fieldResponse.ok) setFields(await fieldResponse.json());
      if (typeResponse.ok) {
        const loaded = await typeResponse.json();
        setTypes(loaded);
        if (!selectedType && loaded[0]) { setSelectedType(loaded[0]); setDraft(loaded[0]); }
      }
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
    async function createCategory(event: FormEvent<HTMLFormElement>) {
      event.preventDefault();
      const response = await fetch("/api/v1/categories", { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify({ name: newCategory.name, parent_id: newCategory.parent_id ? Number(newCategory.parent_id) : null }) });
      if (response.ok) { setNewCategory({ name: "", parent_id: "" }); setNotice("Category added."); await loadAdminData(); }
      else setNotice("Category could not be added. Check the parent and entity.");
    }

    return <section className="admin-panel">
      <div className="page-heading"><div><p className="kicker">ADMINISTRATION</p><h1>Service configuration</h1><p className="subheading">Design the forms and routing rules used by SVC ServiceFlow.</p></div></div>
      <div className="admin-tabs">{["Ticket types", "Field catalogue", "Priority matrix", "Categories"].map((item) => <button key={item} className={section === item ? "active" : ""} onClick={() => setSection(item)}>{item}</button>)}</div>
      {notice && <p className="settings-saved" role="status">{notice}</p>}
      {section === "Ticket types" && <div className="admin-layout"><section className="table-card admin-list"><div className="panel-card-heading"><h2>Ticket types</h2><button className="secondary-button" onClick={() => { const fresh = { id: 0, name: "New ticket type", itil_class: "REQUEST", default_priority: 3, fields: [] }; setSelectedType(fresh); setDraft(fresh); }}>New type</button></div>{types.map((type) => <button className={`admin-list-row ${selectedType?.id === type.id ? "selected" : ""}`} key={type.id} onClick={() => editType(type)}><span><strong>{type.name}</strong><small>{type.itil_class} · {type.fields.length} fields</small></span><b>›</b></button>)}</section>{draft && <section className="table-card admin-editor"><div className="panel-card-heading"><div><h2>Ticket type editor</h2><span>Drag order with the arrows, then preview the portal form.</span></div><button className="primary-button" onClick={() => void saveType()}>Save type</button></div><div className="form-grid"><label>Name<input value={draft.name} onChange={(event) => updateDraft({ name: event.target.value })} required /></label><label>ITIL class<select value={draft.itil_class} onChange={(event) => updateDraft({ itil_class: event.target.value })}><option value="INCIDENT">Incident</option><option value="REQUEST">Service Request</option><option value="PROBLEM">Problem</option><option value="CHANGE">Change</option></select></label><label>Default priority<input type="number" min="1" max="5" value={draft.default_priority} onChange={(event) => updateDraft({ default_priority: Number(event.target.value) })} /></label></div><h3 className="admin-subtitle">Field layout</h3>      <div className="field-layout">{draft.fields.map((field, index) => { const definition = fields.find((item) => item.id === field.field_definition_id); const replace = (changes: Partial<typeof field>) => updateDraft({ fields: draft.fields.map((item, itemIndex) => itemIndex === index ? { ...item, ...changes } : item) }); return <div className="field-layout-row" key={`${field.field_definition_id}-${index}`} draggable onDragStart={() => setDraggedIndex(index)} onDragOver={(event) => event.preventDefault()} onDrop={() => dropField(index)}><span className="drag-handle">⋮⋮</span><strong>{definition?.label ?? `Field ${field.field_definition_id}`}</strong><select value={field.page} onChange={(event) => replace({ page: event.target.value })}><option value="details">Details</option><option value="classification">Classification</option><option value="schedule">Schedule</option></select><label className="inline-check"><input type="checkbox" checked={field.required} onChange={(event) => replace({ required: event.target.checked })} /> Required</label><label className="inline-check"><input type="checkbox" checked={field.show_on_portal} onChange={(event) => replace({ show_on_portal: event.target.checked })} /> Visible</label><label className="inline-check"><input type="checkbox" checked={field.editable_by_roles.includes("Requester")} onChange={(event) => replace({ editable_by_roles: event.target.checked ? [...new Set([...field.editable_by_roles, "Requester"])] : field.editable_by_roles.filter((role) => role !== "Requester") })} /> Requester editable</label><input className="field-default-input" placeholder="Default value" value={field.default_value == null ? "" : String(field.default_value)} onChange={(event) => replace({ default_value: event.target.value || null })} /><input className="field-condition-input" placeholder="Condition JSON" value={Object.keys(field.show_condition ?? {}).length ? JSON.stringify(field.show_condition) : ""} onChange={(event) => { try { replace({ show_condition: event.target.value ? JSON.parse(event.target.value) : {} }); } catch { /* wait for valid JSON */ } }} /><button type="button" className="icon-button" onClick={() => moveField(index, -1)}>↑</button><button type="button" className="icon-button" onClick={() => moveField(index, 1)}>↓</button></div>; })}</div><div className="field-picker-row">{fields.filter((field) => !draft.fields.some((item) => item.field_definition_id === field.id)).map((field) => <button key={field.id} className="secondary-button" onClick={() => updateDraft({ fields: [...draft.fields, { field_definition_id: field.id, order: draft.fields.length, page: "details", required: false, visible_to_roles: ["Administrator", "Technician", "Requester"], editable_by_roles: ["Administrator", "Technician"], default_value: null, show_on_portal: false, show_condition: {} }] })}>+ {field.label}</button>)}</div><h3 className="admin-subtitle">Live portal preview</h3><div className="form-preview">{draft.fields.map((field) => { const definition = fields.find((item) => item.id === field.field_definition_id); return <label key={field.field_definition_id}>{definition?.label ?? "Field"}{field.required && <span> *</span>}<input placeholder={String(field.default_value ?? `Enter ${definition?.label?.toLowerCase() ?? "value"}`)} disabled /></label>; })}</div></section>}</div>}
      {section === "Field catalogue" && <div className="admin-two-column"><section className="table-card"><div className="panel-card-heading"><h2>Field catalogue</h2><span>{fields.length} definitions</span></div>{fields.map((field) => <div className="catalogue-row" key={field.id}><strong>{field.label}</strong><span>{field.key}</span><small>{field.type}{field.is_system ? " · System" : ""}</small></div>)}</section><form className="table-card admin-form-card" onSubmit={createField}><h2>Add custom field</h2><p>Create a field once and reuse it across ticket types.</p><label>Key<input value={newField.key} onChange={(event) => setNewField({ ...newField, key: event.target.value })} placeholder="business_unit" required /></label><label>Label<input value={newField.label} onChange={(event) => setNewField({ ...newField, label: event.target.value })} placeholder="Business unit" required /></label><label>Type<select value={newField.type} onChange={(event) => setNewField({ ...newField, type: event.target.value })}>{["text", "textarea", "number", "date", "datetime", "select", "multiselect", "checkbox", "url"].map((type) => <option key={type}>{type}</option>)}</select></label><button className="primary-button" type="submit">Add field</button></form></div>}
      {section === "Priority matrix" && <section className="table-card matrix-card"><div className="panel-card-heading"><div><h2>Priority matrix</h2><span>Rows are urgency, columns are impact.</span></div><button className="primary-button" onClick={() => void saveMatrix()}>Save matrix</button></div><div className="matrix-grid"><div /><span>1</span><span>2</span><span>3</span><span>4</span><span>5</span>{matrix.map((row, rowIndex) => <><strong key={`label-${rowIndex}`}>{rowIndex + 1}</strong>{row.map((value, columnIndex) => <input key={`${rowIndex}-${columnIndex}`} type="number" min="1" max="5" value={value} onChange={(event) => setMatrix(matrix.map((currentRow, currentRowIndex) => currentRowIndex === rowIndex ? currentRow.map((current, currentColumn) => currentColumn === columnIndex ? Number(event.target.value) : current) : currentRow))} />)}</>)}</div></section>}
      {section === "Categories" && <div className="admin-two-column"><section className="table-card"><div className="panel-card-heading"><h2>Category tree</h2><span>{adminCategories.length} categories · up to four levels</span></div>{adminCategories.length ? adminCategories.map((category) => { let level = 0; let parent = category.parent_id; while (parent && level < 4) { level += 1; parent = adminCategories.find((item) => item.id === parent)?.parent_id; } return <div className="catalogue-row category-tree-row" key={category.id} style={{ paddingLeft: `${level * 20}px` }}><strong>{level ? "↳ " : ""}{category.name}</strong><span>{level === 0 ? "Root" : `Level ${level + 1}`}</span><small>Entity scoped</small></div>; }) : <div className="empty-table"><strong>No categories configured</strong><span>Add the first category to start the service catalogue tree.</span></div>}</section><form className="table-card admin-form-card" onSubmit={createCategory}><h2>Add category</h2><p>Categories are restricted to the signed-in entity and may be nested four levels deep.</p><label>Name<input value={newCategory.name} onChange={(event) => setNewCategory({ ...newCategory, name: event.target.value })} required minLength={2} /></label><label>Parent<select value={newCategory.parent_id} onChange={(event) => setNewCategory({ ...newCategory, parent_id: event.target.value })}><option value="">Root category</option>{adminCategories.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}</select></label><button className="primary-button" type="submit">Add category</button></form></div>}
    </section>;
  }

  if (hasAdminRights) return <AdminPanel token={adminToken} />;
  return <section className="settings-panel">
    <div className="page-heading"><div><p className="kicker">Workspace Settings</p><h1>Settings</h1><p className="subheading">Manage your personal workspace preferences.</p></div><button className="primary-button" onClick={saveSettings}>Save changes</button></div>
    {saved && <p className="settings-saved" role="status">Settings saved.</p>}
    <div className="settings-grid">
      <section className="settings-card"><div className="settings-card-heading"><div><h2>Profile</h2><p>Account details for the signed-in workspace.</p></div><span className="avatar avatar-indigo">{currentUser?.name.slice(0, 2).toUpperCase() ?? "AD"}</span></div><dl className="settings-details"><div><dt>Name</dt><dd>{currentUser?.name ?? "Administrator"}</dd></div><div><dt>Email</dt><dd>{currentUser?.email ?? "Not available"}</dd></div><div><dt>Role</dt><dd>{currentUser?.profile.name ?? "Administrator"}</dd></div></dl></section>
      <section className="settings-card"><div className="settings-card-heading"><div><h2>Preferences</h2><p>Choose how ServiceFlow works for you.</p></div></div><label className="settings-toggle"><span><strong>Email updates</strong><small>Receive updates about ticket activity.</small></span><input type="checkbox" checked={emailUpdates} onChange={(event) => setEmailUpdates(event.target.checked)} /></label><label className="settings-toggle"><span><strong>Compact tables</strong><small>Fit more tickets on each screen.</small></span><input type="checkbox" checked={compactTables} onChange={(event) => setCompactTables(event.target.checked)} /></label>{onThemeChange && <label className="settings-toggle"><span><strong>Theme mode</strong><small>Choose between light, dark, or system default.</small></span><select className="settings-theme-select" value={themeMode ?? "system"} onChange={(event) => onThemeChange(event.target.value as ThemeMode)}><option value="system">System default</option><option value="light">Light</option><option value="dark">Dark</option></select></label>}</section>
    </div>
  </section>;
}

function formatDue(date: string) {
  const hours = Math.round((new Date(date).getTime() - Date.now()) / 3600000);
  if (hours < 0) return "Breached";
  if (hours < 24) return `${hours}h remaining`;
  return `${Math.round(hours / 24)}d remaining`;
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
