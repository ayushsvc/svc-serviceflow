"""HaloSLA Engine — Deterministic Business-Calendar, Cascading SLA Hierarchy,
Workflow Hold/Pause, and Real-Time Evaluation.

Modeled after HaloITSM 4 core pillars:
 1. SLA Architecture: Cascading assignment precedence + Workday calendars + Priority matrix
 2. SLA Workflow: Status-driven hold/pause & multi-stage escalation thresholds (50%, 75%, 100%)
 3. SLA Logic in Ticketing: Working-minutes arithmetic excluding off-hours, breaks, holidays & holds
 4. SLA Principle: Measurable Response / Resolution / OLA targets with breach accountability
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PRIORITY_MATRIX = {
    "critical": {"critical": "P1", "high": "P1", "medium": "P2", "low": "P3"},
    "high":     {"critical": "P1", "high": "P2", "medium": "P2", "low": "P3"},
    "medium":   {"critical": "P2", "high": "P2", "medium": "P3", "low": "P4"},
    "low":      {"critical": "P3", "high": "P3", "medium": "P4", "low": "P4"},
}

INT_TO_LEVEL = {
    1: "critical",
    2: "high",
    3: "medium",
    4: "low",
    5: "low",
}

PRIORITY_TO_NUMERIC = {
    "P1": 1,
    "P2": 2,
    "P3": 3,
    "P4": 4,
}

NUMERIC_TO_PRIORITY = {
    1: "P1",
    2: "P2",
    3: "P3",
    4: "P4",
    5: "P4",
}

HOLD_STATUSES = {
    "awaiting_user",
    "awaiting_vendor",
    "awaiting_approval",
    "on_hold",
    "pending",
    "AWAITING_USER",
    "AWAITING_VENDOR",
    "AWAITING_APPROVAL",
    "ON_HOLD",
    "PENDING",
}

CLOSED_STATUSES = {
    "solved",
    "closed",
    "SOLVED",
    "CLOSED",
}

DEFAULT_BREACH_CODES = [
    {"code": "VENDOR_DELAY", "label": "External Vendor / 3rd-Party Delay"},
    {"code": "CUSTOMER_UNRESPONSIVE", "label": "End-User Unresponsive Before Hold"},
    {"code": "RESOURCE_SHORTAGE", "label": "Agent / Specialist Capacity Constraint"},
    {"code": "COMPLEX_ROOT_CAUSE", "label": "Complex Multi-System Root Cause"},
    {"code": "MISROUTED_TRIAGE", "label": "Initial Triage / Queue Misrouting"},
    {"code": "INFRASTRUCTURE_OUTAGE", "label": "Major Infrastructure / Upstream Outage"},
]


def normalize_level(val: Any) -> str:
    if isinstance(val, int):
        return INT_TO_LEVEL.get(val, "medium")
    if not val:
        return "medium"
    s = str(val).strip().lower()
    if s in ("1", "critical", "crit", "urgent"):
        return "critical"
    if s in ("2", "high"):
        return "high"
    if s in ("3", "medium", "med"):
        return "medium"
    if s in ("4", "5", "low"):
        return "low"
    return "medium"


def calculate_itil_priority(impact: Any, urgency: Any, is_vip: bool = False) -> str:
    """Derive ITIL Impact x Urgency priority (P1-P4), applying optional VIP 1-tier escalation."""
    imp = normalize_level(impact)
    urg = normalize_level(urgency)
    base = PRIORITY_MATRIX.get(imp, {}).get(urg, "P3")
    if not is_vip:
        return base
    rank = PRIORITY_TO_NUMERIC.get(base, 3)
    boosted_rank = max(1, rank - 1)
    return NUMERIC_TO_PRIORITY.get(boosted_rank, "P1")


def parse_time_minutes(hhmm: str) -> int:
    """Parse 'HH:MM' string into minutes since 00:00."""
    try:
        parts = str(hhmm).strip().split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return h * 60 + m
    except (ValueError, IndexError):
        return 540  # Default 09:00


def get_zone_info(tz_name: str | None) -> ZoneInfo:
    if not tz_name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _cal_get(cal: Any, key: str, default: Any = None) -> Any:
    if cal is None:
        return default
    if isinstance(cal, dict):
        return cal.get(key, default)
    val = getattr(cal, key, default)
    return default if val is None else val


def get_zoned_parts(dt: datetime, tz_name: str | None) -> dict:
    """Extract local date, ISO weekday (1=Mon..7=Sun), and minute of day in local timezone."""
    tz = get_zone_info(tz_name)
    local_dt = dt.astimezone(tz) if dt.tzinfo else dt.replace(tzinfo=UTC).astimezone(tz)
    return {
        "date_str": local_dt.strftime("%Y-%m-%d"),
        "iso_weekday": local_dt.isoweekday(),  # 1=Monday..7=Sunday
        "minute_of_day": local_dt.hour * 60 + local_dt.minute,
        "local_dt": local_dt,
    }


def get_day_working_segments(date_str: str, iso_weekday: int, calendar: Any) -> list[tuple[int, int]]:
    """Return active working intervals [(start_min, end_min), ...] in a local day,
    subtracting lunch/shift breaks and holidays.
    """
    if _cal_get(calendar, "is_24_7"):
        return [(0, 1440)]

    holidays = set(_cal_get(calendar, "holidays") or [])
    if date_str in holidays:
        return []

    workdays = set(_cal_get(calendar, "workdays") or [1, 2, 3, 4, 5])
    if iso_weekday not in workdays:
        return []

    start_min = parse_time_minutes(_cal_get(calendar, "start_time") or "09:00")
    end_min = parse_time_minutes(_cal_get(calendar, "end_time") or "17:30")
    if end_min <= start_min:
        return []

    segments = [(start_min, end_min)]
    breaks = _cal_get(calendar, "breaks") or []
    for brk in breaks:
        b_start = parse_time_minutes(brk.get("start", "12:00"))
        b_end = parse_time_minutes(brk.get("end", "13:00"))
        if b_end <= b_start:
            continue
        next_segs = []
        for s, e in segments:
            if b_end <= s or b_start >= e:
                next_segs.append((s, e))
            else:
                if s < b_start:
                    next_segs.append((s, b_start))
                if b_end < e:
                    next_segs.append((b_end, e))
        segments = next_segs

    return segments


def get_effective_sla_start(logged_at: datetime, calendar: Any) -> datetime:
    """Determine effective SLA start timestamp, honoring Delayed Start cutoff if configured.
    If ticket was logged after cutoff on a working day, clock starts at the next working day's opening.
    """
    if _cal_get(calendar, "is_24_7") or not _cal_get(calendar, "delayed_start_cutoff"):
        return logged_at

    tz_name = _cal_get(calendar, "timezone", "UTC")
    cutoff_min = parse_time_minutes(_cal_get(calendar, "delayed_start_cutoff"))
    parts = get_zoned_parts(logged_at, tz_name)

    segs = get_day_working_segments(parts["date_str"], parts["iso_weekday"], calendar)
    if segs and parts["minute_of_day"] >= cutoff_min:
        tz = get_zone_info(tz_name)
        # Advance day-by-day to find next working day opening
        cur_date = parts["local_dt"].date() + timedelta(days=1)
        for _ in range(30):  # Search up to 30 days
            cur_date_str = cur_date.strftime("%Y-%m-%d")
            cur_weekday = cur_date.isoweekday()
            day_segs = get_day_working_segments(cur_date_str, cur_weekday, calendar)
            if day_segs:
                first_start_min = day_segs[0][0]
                h = first_start_min // 60
                m = first_start_min % 60
                eff_local = datetime(cur_date.year, cur_date.month, cur_date.day, h, m, 0, tzinfo=tz)
                return eff_local.astimezone(UTC)
            cur_date += timedelta(days=1)

    return logged_at


def calculate_working_minutes_between(
    from_dt: datetime,
    to_dt: datetime,
    calendar: dict,
    hold_intervals: list[Any] | None = None,
) -> float:
    """Compute working minutes elapsed between from_dt and to_dt, excluding off-hours,
    breaks, holidays, and hold intervals.
    """
    from_dt = as_utc(from_dt)
    to_dt = as_utc(to_dt)
    if not from_dt or not to_dt or to_dt <= from_dt:
        return 0.0

    holds = hold_intervals or []
    from_ts = from_dt.timestamp()
    to_ts = to_dt.timestamp()

    # Clamp and merge hold intervals
    clamped_holds = []
    for h in holds:
        start_raw = getattr(h, "start_time", None) if not isinstance(h, dict) else h.get("start_time")
        end_raw = getattr(h, "end_time", None) if not isinstance(h, dict) else h.get("end_time")

        if not start_raw:
            continue
        h_start = as_utc(start_raw).timestamp() if isinstance(start_raw, datetime) else start_raw
        h_end = as_utc(end_raw).timestamp() if isinstance(end_raw, datetime) else (end_raw if end_raw else to_ts)

        start = max(from_ts, h_start)
        end = min(to_ts, h_end)
        if end > start:
            clamped_holds.append((start, end))

    clamped_holds.sort(key=lambda x: x[0])
    merged_holds = []
    for start, end in clamped_holds:
        if not merged_holds or start > merged_holds[-1][1]:
            merged_holds.append([start, end])
        else:
            merged_holds[-1][1] = max(merged_holds[-1][1], end)

    # Active spans where ticket is NOT on hold
    active_spans = []
    cursor = from_ts
    for h_start, h_end in merged_holds:
        if h_start > cursor:
            active_spans.append((cursor, h_start))
        cursor = max(cursor, h_end)
    if cursor < to_ts:
        active_spans.append((cursor, to_ts))

    if _cal_get(calendar, "is_24_7"):
        return sum((end - start) / 60.0 for start, end in active_spans)

    # Sum working minutes across active spans
    tz_name = _cal_get(calendar, "timezone", "UTC")
    total_minutes = 0.0

    for span_start_ts, span_end_ts in active_spans:
        t_ts = span_start_ts
        while t_ts < span_end_ts:
            dt_cursor = datetime.fromtimestamp(t_ts, tz=UTC)
            parts = get_zoned_parts(dt_cursor, tz_name)
            segs = get_day_working_segments(parts["date_str"], parts["iso_weekday"], calendar)

            if not segs:
                # Fast forward to end of local day
                skip_mins = max(1, 1440 - parts["minute_of_day"])
                t_ts = min(span_end_ts, t_ts + skip_mins * 60)
                continue

            minute_floor_ts = (int(t_ts) // 60) * 60
            next_minute_ts = min(span_end_ts, minute_floor_ts + 60)

            active_seg = any(s <= parts["minute_of_day"] < e for s, e in segs)
            if active_seg:
                total_minutes += (next_minute_ts - t_ts) / 60.0

            t_ts = next_minute_ts

    return round(total_minutes, 1)


def add_working_minutes(
    start_dt: datetime,
    target_minutes: float,
    calendar: dict,
    extend_to_eod: bool = False,
) -> datetime:
    """Add target_minutes of working time starting from start_dt under calendar."""
    if target_minutes <= 0:
        return start_dt

    if _cal_get(calendar, "is_24_7"):
        return start_dt + timedelta(minutes=target_minutes)

    tz_name = _cal_get(calendar, "timezone", "UTC")
    tz = get_zone_info(tz_name)

    remaining = float(target_minutes)
    cur_dt = start_dt.astimezone(tz) if start_dt.tzinfo else start_dt.replace(tzinfo=UTC).astimezone(tz)
    max_days = 90
    days_checked = 0

    while remaining > 1e-4 and days_checked < max_days:
        parts = get_zoned_parts(cur_dt, tz_name)
        segs = get_day_working_segments(parts["date_str"], parts["iso_weekday"], calendar)

        if not segs:
            # Advance to next day 00:00
            cur_date = parts["local_dt"].date() + timedelta(days=1)
            cur_dt = datetime(cur_date.year, cur_date.month, cur_date.day, 0, 0, 0, tzinfo=tz)
            days_checked += 1
            continue

        # Check if currently inside a segment
        active_seg = next((s for s in segs if s[0] <= parts["minute_of_day"] < s[1]), None)
        if active_seg:
            mins_left_in_seg = active_seg[1] - parts["minute_of_day"] - (parts["local_dt"].second / 60.0)
            if mins_left_in_seg >= remaining:
                cur_dt += timedelta(minutes=remaining)
                remaining = 0.0
                break
            else:
                remaining -= mins_left_in_seg
                cur_dt += timedelta(minutes=mins_left_in_seg)
                continue

        # Jump to next segment today if any
        next_seg = next((s for s in segs if s[0] > parts["minute_of_day"]), None)
        if next_seg:
            mins_to_seg = next_seg[0] - parts["minute_of_day"]
            cur_dt += timedelta(minutes=mins_to_seg)
        else:
            # Jump to next day 00:00
            cur_date = parts["local_dt"].date() + timedelta(days=1)
            cur_dt = datetime(cur_date.year, cur_date.month, cur_date.day, 0, 0, 0, tzinfo=tz)
            days_checked += 1

    if extend_to_eod:
        parts = get_zoned_parts(cur_dt, tz_name)
        segs = get_day_working_segments(parts["date_str"], parts["iso_weekday"], calendar)
        if segs:
            last_end = segs[-1][1]
            if parts["minute_of_day"] < last_end:
                cur_dt += timedelta(minutes=last_end - parts["minute_of_day"])

    return cur_dt.astimezone(UTC)


def resolve_sla_policy(
    global_override_sla_id: int | None = None,
    asset: Any = None,
    contract: Any = None,
    ticket_rules: list[Any] | None = None,
    ticket_input: dict | None = None,
    site: Any = None,
    ticket_type_default_sla_id: int | None = None,
    slas_by_id: dict[int, Any] | None = None,
) -> dict:
    """Cascading SLA Hierarchy Resolver (HaloITSM 5-tier precedence):
     1. Global SLA Override
     2. Asset / CI-level SLA
     3. Contract / Agreement SLA
     4. Matching Ticket Rule SLA
     5. Site / Ticket Type Default SLA -> Fallback
    """
    slas = slas_by_id or {}

    # Tier 1: Global Override
    if global_override_sla_id and global_override_sla_id in slas:
        return {
            "sla": slas[global_override_sla_id],
            "matched_tier": "Global Override",
            "matched_reason": "Global SLA Override is active in system settings",
        }

    # Tier 2: Asset / CI SLA
    asset_sla_id = getattr(asset, "sla_id", None) if asset else None
    if asset_sla_id and asset_sla_id in slas:
        name = getattr(asset, "name", "Configuration Item")
        return {
            "sla": slas[asset_sla_id],
            "matched_tier": "Asset / CI SLA",
            "matched_reason": f'Linked Asset "{name}" binds SLA',
        }

    # Tier 3: Contract / Agreement SLA
    contract_sla_id = getattr(contract, "sla_id", None) if contract else None
    if contract_sla_id and contract_sla_id in slas:
        name = getattr(contract, "name", "Agreement")
        return {
            "sla": slas[contract_sla_id],
            "matched_tier": "Contract / Agreement",
            "matched_reason": f'Active Contract "{name}" enforces contracted SLA',
        }

    # Tier 4: Ticket Rules / Automation
    rules = ticket_rules or []
    t_input = ticket_input or {}
    for rule in rules:
        r_sla_id = getattr(rule, "sla_id", None)
        if r_sla_id and r_sla_id in slas and getattr(rule, "is_active", True):
            # Check conditions
            conds = getattr(rule, "conditions", {}) or {}
            matched = True
            if conds.get("ticket_type") and conds["ticket_type"] != t_input.get("type"):
                matched = False
            if conds.get("category_id") and conds["category_id"] != t_input.get("category_id"):
                matched = False
            if matched:
                r_name = getattr(rule, "name", "Rule")
                return {
                    "sla": slas[r_sla_id],
                    "matched_tier": "Ticket Rule / Automation",
                    "matched_reason": f'Matched Ticket Rule "{r_name}"',
                }

    # Tier 5: Site or Ticket Type Default
    site_sla_id = getattr(site, "default_sla_id", None) if site else None
    if site_sla_id and site_sla_id in slas:
        s_name = getattr(site, "name", "Site")
        return {
            "sla": slas[site_sla_id],
            "matched_tier": "Site Default",
            "matched_reason": f'Default SLA for Site "{s_name}"',
        }

    if ticket_type_default_sla_id and ticket_type_default_sla_id in slas:
        return {
            "sla": slas[ticket_type_default_sla_id],
            "matched_tier": "Ticket Type Default",
            "matched_reason": "Default SLA for Ticket Type",
        }

    # System Fallback
    fallback = next(iter(slas.values()), None)
    return {
        "sla": fallback,
        "matched_tier": "System Fallback",
        "matched_reason": "Default organizational SLA policy",
    }


def evaluate_ticket_sla(
    ticket: Any,
    sla: Any,
    calendar: dict,
    hold_intervals: list[dict] | None = None,
    now_dt: datetime | None = None,
) -> dict:
    """Evaluate complete real-time SLA snapshot for a ticket."""
    now = as_utc(now_dt) or datetime.now(UTC)
    holds = hold_intervals or []

    priorities_cfg = getattr(sla, "priorities_config", {}) or {}
    priority_code = getattr(ticket, "priority_code", None) or f"P{getattr(ticket, 'priority', 3)}"
    p_config = priorities_cfg.get(priority_code) or priorities_cfg.get("P3") or {
        "response_minutes": getattr(sla, "tto_minutes", 60),
        "resolution_minutes": getattr(sla, "ttr_minutes", 480),
        "ola_minutes": 240,
    }

    response_target_mins = p_config.get("response_minutes", 60)
    resolution_target_mins = p_config.get("resolution_minutes", 480)
    ola_target_mins = p_config.get("ola_minutes", int(resolution_target_mins * 0.6))

    opened_at = as_utc(getattr(ticket, "opened_at", now))
    eff_start = as_utc(getattr(ticket, "sla_effective_start_at", None)) or get_effective_sla_start(opened_at, calendar)
    eff_start = as_utc(eff_start)

    status_str = (getattr(ticket, "status", "NEW") or "").lower()
    is_on_hold = status_str in HOLD_STATUSES
    is_closed = status_str in CLOSED_STATUSES

    first_responded = as_utc(getattr(ticket, "first_responded_at", None))
    resolved_at = as_utc(getattr(ticket, "solved_at", None) or getattr(ticket, "closed_at", None))

    response_end = first_responded if first_responded else now
    resolution_end = resolved_at if is_closed and resolved_at else now

    response_elapsed_mins = calculate_working_minutes_between(eff_start, response_end, calendar, holds)
    resolution_elapsed_mins = calculate_working_minutes_between(eff_start, resolution_end, calendar, holds)

    total_hold_seconds = 0.0
    for h in holds:
        start_raw = getattr(h, "start_time", None) if not isinstance(h, dict) else h.get("start_time")
        end_raw = getattr(h, "end_time", None) if not isinstance(h, dict) else h.get("end_time")
        if start_raw:
            s_ts = as_utc(start_raw).timestamp() if isinstance(start_raw, datetime) else start_raw
            e_ts = as_utc(end_raw).timestamp() if isinstance(end_raw, datetime) else (as_utc(now).timestamp() if not end_raw else end_raw)
            if e_ts > s_ts:
                total_hold_seconds += (e_ts - s_ts)

    response_pct = round((response_elapsed_mins / response_target_mins * 100), 1) if response_target_mins > 0 else 0.0
    resolution_pct = round((resolution_elapsed_mins / resolution_target_mins * 100), 1) if resolution_target_mins > 0 else 0.0
    ola_pct = round((resolution_elapsed_mins / ola_target_mins * 100), 1) if ola_target_mins > 0 else 0.0

    response_met = bool(first_responded) and (response_elapsed_mins <= response_target_mins)
    response_breached = response_elapsed_mins > response_target_mins

    resolution_met = is_closed and (resolution_elapsed_mins <= resolution_target_mins)
    resolution_breached = resolution_elapsed_mins > resolution_target_mins

    ola_breached = resolution_elapsed_mins > ola_target_mins

    warn1_pct = getattr(sla, "warning1_pct", 50)
    warn2_pct = getattr(sla, "warning2_pct", 75)

    if is_closed:
        sla_health = "breached" if (resolution_breached or response_breached) else "met"
    elif is_on_hold:
        sla_health = "breached" if (resolution_breached or response_breached) else "paused"
    elif resolution_breached or (not first_responded and response_breached):
        sla_health = "breached"
    elif resolution_pct >= warn2_pct or (not first_responded and response_pct >= warn2_pct):
        sla_health = "warning_2"
    elif resolution_pct >= warn1_pct or (not first_responded and response_pct >= warn1_pct):
        sla_health = "warning_1"
    else:
        sla_health = "on_track"

    proj_anchor = max(now, eff_start)
    response_remaining = max(0.0, response_target_mins - response_elapsed_mins)
    resolution_remaining = max(0.0, resolution_target_mins - resolution_elapsed_mins)

    extend_eod = getattr(sla, "extend_to_eod", False)

    if first_responded:
        projected_response_due = first_responded
    elif response_breached:
        projected_response_due = add_working_minutes(eff_start, response_target_mins, calendar, extend_eod)
    else:
        projected_response_due = add_working_minutes(proj_anchor, response_remaining, calendar, extend_eod)

    if resolved_at:
        projected_resolution_due = resolved_at
    elif resolution_breached:
        projected_resolution_due = add_working_minutes(eff_start, resolution_target_mins, calendar, extend_eod)
    else:
        projected_resolution_due = add_working_minutes(proj_anchor, resolution_remaining, calendar, extend_eod)

    return {
        "effective_start_at": eff_start,
        "is_on_hold": is_on_hold,
        "is_closed": is_closed,
        "sla_health": sla_health,
        "health": sla_health,
        "total_hold_minutes": round(total_hold_seconds / 60, 1),
        "response": {
            "target_minutes": response_target_mins,
            "elapsed_minutes": response_elapsed_mins,
            "remaining_minutes": round(response_remaining, 1),
            "percent": response_pct,
            "responded": bool(first_responded),
            "met": response_met,
            "breached": response_breached,
            "due_at": projected_response_due,
        },
        "resolution": {
            "target_minutes": resolution_target_mins,
            "elapsed_minutes": resolution_elapsed_mins,
            "remaining_minutes": round(resolution_remaining, 1),
            "percent": resolution_pct,
            "resolved": is_closed,
            "met": resolution_met,
            "breached": resolution_breached,
            "due_at": projected_resolution_due,
        },
        "ola": {
            "target_minutes": ola_target_mins,
            "elapsed_minutes": resolution_elapsed_mins,
            "percent": ola_pct,
            "breached": ola_breached,
        },
        "thresholds": {
            "warning1_pct": warn1_pct,
            "warning2_pct": warn2_pct,
        },
    }
