from datetime import UTC, datetime
from typing import Any
from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.orm import selectinload

from app.deps import CurrentUser, DbSession, require_admin, require_helpdesk
from app.models import AuditLog, SLA, SLABreachCode, SLAHoldInterval, Ticket, WorkdayCalendar
from app.schemas_sla import (
    SLABreachCodeRead,
    SLAPolicyCreate,
    SLAPolicyRead,
    SLAPolicyUpdate,
    SLAPreviewRequest,
    SLAPreviewResponse,
    WorkdayCalendarCreate,
    WorkdayCalendarRead,
    WorkdayCalendarUpdate,
)
from app.services.sla_engine import (
    CLOSED_STATUSES,
    HOLD_STATUSES,
    add_working_minutes,
    as_utc,
    calculate_working_minutes_between,
    evaluate_ticket_sla,
)
from app.services.sla_service import (
    evaluate_ticket_live,
    get_ticket_policy_and_calendar,
    handle_status_sla_transition,
)

router = APIRouter(tags=["sla"])


# ==========================================
# 1. Workday Calendars
# ==========================================

@router.get("/sla/calendars", response_model=list[WorkdayCalendarRead])
async def list_calendars(user: CurrentUser, db: DbSession) -> list[WorkdayCalendar]:
    calendars = list(await db.scalars(
        select(WorkdayCalendar)
        .where(WorkdayCalendar.entity_id == user.entity_id)
        .order_by(WorkdayCalendar.id.asc())
    ))
    return calendars


@router.get("/sla/calendars/{calendar_id}", response_model=WorkdayCalendarRead)
async def get_calendar(calendar_id: int, user: CurrentUser, db: DbSession) -> WorkdayCalendar:
    cal = await db.scalar(
        select(WorkdayCalendar).where(
            WorkdayCalendar.id == calendar_id,
            WorkdayCalendar.entity_id == user.entity_id,
        )
    )
    if not cal:
        raise HTTPException(status_code=404, detail="Workday calendar not found")
    return cal


@router.post("/sla/calendars", response_model=WorkdayCalendarRead, status_code=status.HTTP_201_CREATED)
async def create_calendar(payload: WorkdayCalendarCreate, user: CurrentUser, db: DbSession) -> WorkdayCalendar:
    require_admin(user)
    cal = WorkdayCalendar(
        entity_id=user.entity_id,
        **payload.model_dump(),
    )
    db.add(cal)
    await db.commit()
    await db.refresh(cal)
    return cal


@router.patch("/sla/calendars/{calendar_id}", response_model=WorkdayCalendarRead)
async def update_calendar(
    calendar_id: int,
    payload: WorkdayCalendarUpdate,
    user: CurrentUser,
    db: DbSession,
) -> WorkdayCalendar:
    require_admin(user)
    cal = await db.scalar(
        select(WorkdayCalendar).where(
            WorkdayCalendar.id == calendar_id,
            WorkdayCalendar.entity_id == user.entity_id,
        )
    )
    if not cal:
        raise HTTPException(status_code=404, detail="Workday calendar not found")

    for key, val in payload.model_dump(exclude_unset=True).items():
        setattr(cal, key, val)

    await db.commit()
    await db.refresh(cal)
    return cal


@router.delete("/sla/calendars/{calendar_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_calendar(calendar_id: int, user: CurrentUser, db: DbSession) -> None:
    require_admin(user)
    cal = await db.scalar(
        select(WorkdayCalendar).where(
            WorkdayCalendar.id == calendar_id,
            WorkdayCalendar.entity_id == user.entity_id,
        )
    )
    if not cal:
        raise HTTPException(status_code=404, detail="Workday calendar not found")

    cal.is_active = False
    await db.commit()


# ==========================================
# 2. SLA Policies
# ==========================================

@router.get("/sla/policies", response_model=list[SLAPolicyRead])
async def list_policies(user: CurrentUser, db: DbSession) -> list[SLA]:
    policies = list(await db.scalars(
        select(SLA)
        .options(selectinload(SLA.calendar_rel))
        .where(SLA.entity_id == user.entity_id)
        .order_by(SLA.id.asc())
    ))
    for p in policies:
        p.calendar = p.calendar_rel
    return policies


@router.get("/sla/policies/{policy_id}", response_model=SLAPolicyRead)
async def get_policy(policy_id: int, user: CurrentUser, db: DbSession) -> SLA:
    p = await db.scalar(
        select(SLA)
        .options(selectinload(SLA.calendar_rel))
        .where(SLA.id == policy_id, SLA.entity_id == user.entity_id)
    )
    if not p:
        raise HTTPException(status_code=404, detail="SLA policy not found")
    p.calendar = p.calendar_rel
    return p


@router.post("/sla/policies", response_model=SLAPolicyRead, status_code=status.HTTP_201_CREATED)
async def create_policy(payload: SLAPolicyCreate, user: CurrentUser, db: DbSession) -> SLA:
    require_admin(user)
    if payload.is_default:
        existing_defaults = list(await db.scalars(
            select(SLA).where(SLA.entity_id == user.entity_id, SLA.is_default.is_(True))
        ))
        for existing in existing_defaults:
            existing.is_default = False

    data = payload.model_dump()
    priorities_config = data.get("priorities_config") or {}
    p3_target = priorities_config.get("P3", {})
    tto_mins = int(p3_target.get("response_mins") or 60)
    ttr_mins = int(p3_target.get("resolution_mins") or 480)

    policy = SLA(
        entity_id=user.entity_id,
        tto_minutes=tto_mins,
        ttr_minutes=ttr_mins,
        **data,
    )
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    policy.calendar = policy.calendar_rel
    return policy


@router.patch("/sla/policies/{policy_id}", response_model=SLAPolicyRead)
async def update_policy(
    policy_id: int,
    payload: SLAPolicyUpdate,
    user: CurrentUser,
    db: DbSession,
) -> SLA:
    require_admin(user)
    policy = await db.scalar(
        select(SLA)
        .options(selectinload(SLA.calendar_rel))
        .where(SLA.id == policy_id, SLA.entity_id == user.entity_id)
    )
    if not policy:
        raise HTTPException(status_code=404, detail="SLA policy not found")

    update_data = payload.model_dump(exclude_unset=True)
    if update_data.get("is_default"):
        existing_defaults = list(await db.scalars(
            select(SLA).where(SLA.entity_id == user.entity_id, SLA.is_default.is_(True), SLA.id != policy_id)
        ))
        for existing in existing_defaults:
            existing.is_default = False

    for key, val in update_data.items():
        setattr(policy, key, val)

    if "priorities_config" in update_data and policy.priorities_config:
        p3_target = policy.priorities_config.get("P3", {})
        if p3_target:
            policy.tto_minutes = int(p3_target.get("response_mins") or policy.tto_minutes)
            policy.ttr_minutes = int(p3_target.get("resolution_mins") or policy.ttr_minutes)

    await db.commit()
    await db.refresh(policy)
    policy.calendar = policy.calendar_rel
    return policy


@router.delete("/sla/policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(policy_id: int, user: CurrentUser, db: DbSession) -> None:
    require_admin(user)
    policy = await db.scalar(
        select(SLA).where(SLA.id == policy_id, SLA.entity_id == user.entity_id)
    )
    if not policy:
        raise HTTPException(status_code=404, detail="SLA policy not found")
    policy.is_active = False
    await db.commit()


# ==========================================
# 3. Breach Justification Codes
# ==========================================

@router.get("/sla/breach-codes", response_model=list[SLABreachCodeRead])
async def list_breach_codes(user: CurrentUser, db: DbSession) -> list[SLABreachCode]:
    codes = list(await db.scalars(
        select(SLABreachCode).where(SLABreachCode.is_active.is_(True)).order_by(SLABreachCode.id.asc())
    ))
    return codes


@router.post("/sla/breach-codes", response_model=SLABreachCodeRead, status_code=status.HTTP_201_CREATED)
async def create_breach_code(code: str, label: str, description: str | None, user: CurrentUser, db: DbSession) -> SLABreachCode:
    require_admin(user)
    existing = await db.scalar(select(SLABreachCode).where(SLABreachCode.code == code.strip()))
    if existing:
        raise HTTPException(status_code=409, detail=f"Breach code '{code}' already exists")
    bc = SLABreachCode(code=code.strip(), label=label.strip(), description=description, is_active=True)
    db.add(bc)
    await db.commit()
    await db.refresh(bc)
    return bc


# ==========================================
# 4. Ticket Hold Intervals & Pause/Resume
# ==========================================

@router.get("/sla/tickets/{ticket_id}/holds")
async def get_ticket_holds(ticket_id: int, user: CurrentUser, db: DbSession) -> list[dict[str, Any]]:
    ticket = await db.scalar(select(Ticket).where(Ticket.id == ticket_id, Ticket.entity_id == user.entity_id))
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    intervals = list(await db.scalars(
        select(SLAHoldInterval)
        .where(SLAHoldInterval.ticket_id == ticket_id)
        .order_by(SLAHoldInterval.start_time.asc())
    ))
    res = []
    now = datetime.now(UTC)
    for it in intervals:
        end = it.end_time or now
        mins = max(0, int((end - it.start_time).total_seconds() // 60))
        res.append({
            "id": it.id,
            "ticket_id": it.ticket_id,
            "reason": it.reason,
            "start_time": it.start_time.isoformat(),
            "end_time": it.end_time.isoformat() if it.end_time else None,
            "is_active": it.end_time is None,
            "duration_minutes": mins,
            "created_by_id": it.created_by_id,
        })
    return res


@router.post("/sla/tickets/{ticket_id}/pause")
async def pause_ticket_sla(
    ticket_id: int,
    reason: str = Query(default="awaiting_user"),
    user: CurrentUser = None,
    db: DbSession = None,
) -> dict[str, Any]:
    require_helpdesk(user)
    ticket = await db.scalar(select(Ticket).where(Ticket.id == ticket_id, Ticket.entity_id == user.entity_id))
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    target_status = "PENDING"
    await handle_status_sla_transition(ticket, ticket.status, target_status, user.id, db, hold_reason=reason)
    ticket.status = target_status
    await db.commit()
    await db.refresh(ticket)
    return {"ticket_id": ticket.id, "status": ticket.status, "sla_paused": True, "hold_reason": reason}


@router.post("/sla/tickets/{ticket_id}/resume")
async def resume_ticket_sla(
    ticket_id: int,
    user: CurrentUser,
    db: DbSession,
) -> dict[str, Any]:
    require_helpdesk(user)
    ticket = await db.scalar(select(Ticket).where(Ticket.id == ticket_id, Ticket.entity_id == user.entity_id))
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    target_status = "ASSIGNED" if ticket.assignee_id else "NEW"
    await handle_status_sla_transition(ticket, ticket.status, target_status, user.id, db)
    ticket.status = target_status
    await db.commit()
    await db.refresh(ticket)
    return {"ticket_id": ticket.id, "status": ticket.status, "sla_paused": False}


# ==========================================
# 5. Live SLA State Evaluation
# ==========================================

@router.get("/sla/tickets/{ticket_id}/state")
async def get_ticket_sla_state(ticket_id: int, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    ticket = await db.scalar(select(Ticket).where(Ticket.id == ticket_id, Ticket.entity_id == user.entity_id))
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    state = await evaluate_ticket_live(ticket, db)
    return state


# ==========================================
# 6. SLA Simulation / Preview
# ==========================================

@router.post("/sla/preview", response_model=SLAPreviewResponse)
async def preview_sla_due(payload: SLAPreviewRequest, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    calendar: WorkdayCalendar | None = None
    if payload.calendar_id:
        calendar = await db.scalar(
            select(WorkdayCalendar).where(
                WorkdayCalendar.id == payload.calendar_id,
                WorkdayCalendar.entity_id == user.entity_id,
            )
        )
    if not calendar:
        calendar = await db.scalar(
            select(WorkdayCalendar)
            .where(WorkdayCalendar.entity_id == user.entity_id, WorkdayCalendar.is_active.is_(True))
            .order_by(WorkdayCalendar.id.asc())
            .limit(1)
        )

    start = payload.start_time or datetime.now(UTC)
    due_at = add_working_minutes(start, payload.minutes_to_add, calendar, extend_to_eod=payload.extend_to_eod)
    return {
        "calculated_due_at": due_at,
        "calendar_name": calendar.name if calendar else "Default 24x7",
        "calendar_timezone": calendar.timezone if calendar else "UTC",
    }


# ==========================================
# 7. SLA Analytics / Compliance Metrics
# ==========================================

@router.get("/sla/analytics")
async def get_sla_analytics(user: CurrentUser, db: DbSession) -> dict[str, Any]:
    all_tickets = list(await db.scalars(
        select(Ticket).where(Ticket.entity_id == user.entity_id, Ticket.deleted_at.is_(None))
    ))
    total = len(all_tickets)
    if total == 0:
        return {
            "total_tickets": 0,
            "compliance_pct": 100.0,
            "response_compliance_pct": 100.0,
            "resolution_compliance_pct": 100.0,
            "breached_count": 0,
            "on_track_count": 0,
            "paused_count": 0,
            "avg_mttr_minutes": 0,
            "breach_reasons": {},
        }

    resp_breached = 0
    res_breached = 0
    paused = 0
    on_track = 0
    resolved_mttr_mins: list[int] = []
    breach_reasons: dict[str, int] = {}

    now = datetime.now(UTC)
    for t in all_tickets:
        is_closed = (t.status or "").upper() in CLOSED_STATUSES
        is_paused = (t.status or "").lower() in HOLD_STATUSES

        if is_paused:
            paused += 1

        # Response SLA check
        tto_utc = as_utc(t.tto_due_at)
        ttr_utc = as_utc(t.ttr_due_at)
        if t.response_breach_triggered or (tto_utc and not t.first_responded_at and not is_closed and now > tto_utc):
            resp_breached += 1

        # Resolution SLA check
        if t.breach_triggered or (ttr_utc and not is_closed and now > ttr_utc):
            res_breached += 1
            if t.breach_code:
                breach_reasons[t.breach_code] = breach_reasons.get(t.breach_code, 0) + 1
        else:
            if not is_paused:
                on_track += 1

        if is_closed and t.solved_at and t.opened_at:
            mins = max(0, int((t.solved_at - t.opened_at).total_seconds() // 60))
            resolved_mttr_mins.append(mins)

    resp_comp = round(((total - resp_breached) / total) * 100, 1)
    res_comp = round(((total - res_breached) / total) * 100, 1)
    overall_comp = round(((total - max(resp_breached, res_breached)) / total) * 100, 1)
    avg_mttr = round(sum(resolved_mttr_mins) / len(resolved_mttr_mins), 1) if resolved_mttr_mins else 0

    return {
        "total_tickets": total,
        "compliance_pct": overall_comp,
        "response_compliance_pct": resp_comp,
        "resolution_compliance_pct": res_comp,
        "breached_count": res_breached,
        "on_track_count": on_track,
        "paused_count": paused,
        "avg_mttr_minutes": avg_mttr,
        "breach_reasons": breach_reasons,
    }
