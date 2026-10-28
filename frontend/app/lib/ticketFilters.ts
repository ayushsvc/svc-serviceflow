export type DateBasis = "created" | "updated";

export type TicketFilters = {
  status: string[];
  priority: string[];
  assignee: string[];
  requester: string[];
  type: string[];
  category: string[];
  basis: DateBasis;
  from: string;
  to: string;
};

export type FilterOption = { id: string; label: string; hint?: string };
export type FilterOptions = {
  statuses: FilterOption[];
  priorities: FilterOption[];
  assignees: FilterOption[];
  requesters: FilterOption[];
  types: FilterOption[];
  categories: FilterOption[];
};

export const emptyFilters: TicketFilters = {
  status: [], priority: [], assignee: [], requester: [], type: [], category: [], basis: "created", from: "", to: "",
};

const STORED_STATUS_LABELS: Record<string, string> = {
  NEW: "Open", ASSIGNED: "Assigned", PLANNED: "In Progress", PENDING: "On Hold", SOLVED: "Resolved", CLOSED: "Closed",
};
const PRIORITY_LABELS: Record<string, string> = { "1": "Critical", "2": "High", "3": "Medium", "4": "Low", "5": "Low" };
const QUEUES = ["all", "mine", "teams", "unassigned"] as const;
export type QueueName = (typeof QUEUES)[number];

// Query-string keys used in the browser URL; API keys are produced separately.
const URL_KEYS: Array<[keyof TicketFilters, string]> = [
  ["status", "status"], ["priority", "priority"], ["assignee", "assignee_id"],
  ["requester", "requester_id"], ["type", "ticket_type_id"], ["category", "category_id"],
];
const ID_KEYS: Array<keyof TicketFilters> = ["assignee", "requester", "type", "category"];
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function uniq(values: string[]) {
  return Array.from(new Set(values));
}

export function normaliseStatus(value: string) {
  const trimmed = value.trim();
  return STORED_STATUS_LABELS[trimmed.toUpperCase()] ?? trimmed;
}

export function normalisePriority(value: string) {
  const trimmed = value.trim();
  return PRIORITY_LABELS[trimmed] ?? trimmed.charAt(0).toUpperCase() + trimmed.slice(1).toLowerCase();
}

function cleanIds(values: unknown[]) {
  return uniq(values.map((value) => String(value).trim()).filter((value) => /^\d+$/.test(value)));
}

export function toLocalDate(value: unknown): string {
  if (typeof value !== "string" || !value) return "";
  if (DATE_RE.test(value)) return value;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function startOfDayIso(date: string) {
  return new Date(`${date}T00:00:00`).toISOString();
}

export function endOfDayIso(date: string) {
  return new Date(`${date}T23:59:59.999`).toISOString();
}

export function dateRangeInvalid(filters: TicketFilters) {
  return Boolean(filters.from && filters.to && filters.from > filters.to);
}

export function hasStaffOnlyFilters(filters: TicketFilters) {
  return Boolean(filters.assignee.length || filters.requester.length || filters.type.length || filters.category.length || filters.basis === "updated");
}

export function stripStaffFilters(filters: TicketFilters): TicketFilters {
  return { ...filters, assignee: [], requester: [], type: [], category: [], basis: "created" };
}

export function activeFilterCount(filters: TicketFilters) {
  return filters.status.length + filters.priority.length + filters.assignee.length + filters.requester.length
    + filters.type.length + filters.category.length + (filters.from ? 1 : 0) + (filters.to ? 1 : 0);
}

// Appends only set values; the API rejects empty values with 422.
export function appendFilterParams(params: URLSearchParams, filters: TicketFilters) {
  filters.status.forEach((value) => params.append("status", value));
  filters.priority.forEach((value) => params.append("priority", value));
  filters.assignee.forEach((value) => params.append("assignee_id", value));
  filters.requester.forEach((value) => params.append("requester_id", value));
  filters.type.forEach((value) => params.append("ticket_type_id", value));
  filters.category.forEach((value) => params.append("category_id", value));
  if (dateRangeInvalid(filters)) return;
  const prefix = filters.basis === "updated" ? "updated" : "created";
  if (filters.from && DATE_RE.test(filters.from)) params.set(`${prefix}_from`, startOfDayIso(filters.from));
  if (filters.to && DATE_RE.test(filters.to)) params.set(`${prefix}_to`, endOfDayIso(filters.to));
}

export function serialiseUrl(filters: TicketFilters, search: string, queue: QueueName | null) {
  const params = new URLSearchParams();
  URL_KEYS.forEach(([key, name]) => (filters[key] as string[]).forEach((value) => params.append(name, value)));
  if (filters.from || filters.to) params.set("basis", filters.basis);
  if (filters.from) params.set("from", filters.from);
  if (filters.to) params.set("to", filters.to);
  if (search.trim()) params.set("q", search.trim());
  if (queue) params.set("queue", queue);
  const text = params.toString();
  return text ? `?${text}` : "";
}

export function parseUrl(searchText: string): { filters: TicketFilters; search: string; queue: QueueName | null } {
  const params = new URLSearchParams(searchText);
  const all = (name: string) => params.getAll(name).filter((value) => value.trim() !== "");
  const filters: TicketFilters = {
    ...emptyFilters,
    status: uniq(all("status").map(normaliseStatus)),
    priority: uniq(all("priority").map(normalisePriority)),
    assignee: cleanIds(all("assignee_id")),
    requester: cleanIds(all("requester_id")),
    type: cleanIds(all("ticket_type_id")),
    category: cleanIds(all("category_id")),
    basis: params.get("basis") === "updated" ? "updated" : "created",
    from: DATE_RE.test(params.get("from") ?? "") ? params.get("from")! : "",
    to: DATE_RE.test(params.get("to") ?? "") ? params.get("to")! : "",
  };
  const queueParam = params.get("queue");
  const queue = QUEUES.find((item) => item === queueParam) ?? null;
  return { filters, search: params.get("q") ?? "", queue };
}

export function urlHasFilters(searchText: string) {
  const { filters, search } = parseUrl(searchText);
  return activeFilterCount(filters) > 0 || Boolean(search);
}

function asList(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  return value === undefined || value === null || value === "" ? [] : [value];
}

// Accepts old profiles (single status string, numeric priority) and the new list forms.
export function filtersFromProfile(config: Record<string, unknown>): TicketFilters {
  const updated = !config.created_from && !config.created_to && Boolean(config.updated_from || config.updated_to);
  return {
    status: uniq(asList(config.status).map((value) => normaliseStatus(String(value))).filter((value) => value && value !== "All tickets")),
    priority: uniq(asList(config.priority).map((value) => normalisePriority(String(value))).filter(Boolean)),
    assignee: cleanIds(asList(config.assignee_id)),
    requester: cleanIds(asList(config.requester_id)),
    type: cleanIds(asList(config.ticket_type_id)),
    category: cleanIds(asList(config.category_id)),
    basis: updated ? "updated" : "created",
    from: toLocalDate(updated ? config.updated_from : config.created_from),
    to: toLocalDate(updated ? config.updated_to : config.created_to),
  };
}

export function filtersToProfile(filters: TicketFilters): Record<string, unknown> {
  const config: Record<string, unknown> = {};
  if (filters.status.length) config.status = filters.status;
  if (filters.priority.length) config.priority = filters.priority;
  if (filters.assignee.length) config.assignee_id = filters.assignee.map(Number);
  if (filters.requester.length) config.requester_id = filters.requester.map(Number);
  if (filters.type.length) config.ticket_type_id = filters.type.map(Number);
  if (filters.category.length) config.category_id = filters.category.map(Number);
  if (!dateRangeInvalid(filters)) {
    const prefix = filters.basis === "updated" ? "updated" : "created";
    if (filters.from) config[`${prefix}_from`] = startOfDayIso(filters.from);
    if (filters.to) config[`${prefix}_to`] = endOfDayIso(filters.to);
  }
  return config;
}

export function filtersEqual(a: TicketFilters, b: TicketFilters) {
  return JSON.stringify(a) === JSON.stringify(b);
}

export function sameIdKey(key: keyof TicketFilters) {
  return ID_KEYS.includes(key);
}

type RawOptions = {
  statuses?: Array<{ label: string }>;
  priorities?: Array<{ label: string }>;
  assignees?: Array<{ id: number; name: string; is_active?: boolean }>;
  requesters?: Array<{ id: number; name: string; is_active?: boolean }>;
  ticket_types?: Array<{ id: number; name: string }>;
  categories?: Array<{ id: number; name: string }>;
};

export function optionsFromResponse(raw: RawOptions): FilterOptions {
  const people = (list: RawOptions["assignees"]) => (list ?? []).map((item) => ({
    id: String(item.id), label: item.name, hint: item.is_active === false ? "Inactive" : undefined,
  }));
  const named = (list: Array<{ id: number; name: string }> | undefined) => (list ?? []).map((item) => ({ id: String(item.id), label: item.name }));
  return {
    statuses: (raw.statuses ?? []).map((item) => ({ id: item.label, label: item.label })),
    priorities: (raw.priorities ?? []).map((item) => ({ id: item.label, label: item.label })),
    assignees: people(raw.assignees),
    requesters: people(raw.requesters),
    types: named(raw.ticket_types),
    categories: named(raw.categories),
  };
}
