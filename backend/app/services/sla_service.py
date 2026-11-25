from datetime import UTC, datetime
from typing import Any
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import AuditLog, SLA, SLABreachCode, SLAHoldInterval, Ticket, User, WorkdayCalendar
from app.services.sla_engine import (
    CLOSED_STATUSES,
    HOLD_STATUSES,
    add_working_minutes,
    as_utc,
    calculate_itil_priority,
    calculate_working_minutes_between,
    evaluate_ticket_sla,
    get_effective_sla_start,
    resolve_sla_policy,
)


async def get_ticket_policy_and_calendar(ticket: Ticket, db: AsyncSession) -> tuple[SLA | None, WorkdayCalendar | None]:
    policy: SLA | None = None
    if ticket.sla_id:
        policy = await db.scalar(
            select(SLA)
            .options(selectinload(SLA.calendar_rel))
            .where(SLA.id == ticket.sla_id)
        )
    if not policy:
        # Fallback to entity default SLA
        policy = await db.scalar(
            select(SLA)
            .options(selectinload(SLA.calendar_rel))
            .where(SLA.entity_id == ticket.entity_id, SLA.is_active.is_(True))
            .order_by(SLA.id.asc())
            .limit(1)
        )
    calendar: WorkdayCalendar | None = None
    if policy:
        if policy.calendar_rel:
            calendar = policy.calendar_rel
        elif policy.calendar_id:
            calendar = await db.scalar(
                select(WorkdayCalendar).where(WorkdayCalendar.id == policy.calendar_id)
            )
    if not calendar:
        # Fallback to default calendar
        calendar = await db.scalar(
            select(WorkdayCalendar)
            .where(WorkdayCalendar.entity_id == ticket.entity_id, WorkdayCalendar.is_active.is_(True))
            .order_by(WorkdayCalendar.id.asc())
            .limit(1)
        )
    return policy, calendar


async def apply_initial_sla(ticket: Ticket, db: AsyncSession, requester_user: User | None = None) -> None:
    is_vip = False
    if requester_user:
        is_vip = bool(getattr(requester_user, "is_vip", False) or getattr(requester_user, "requester_vip", False))
    elif ticket.requester_id:
        req = await db.scalar(select(User).where(User.id == ticket.requester_id))
        if req:
            is_vip = bool(getattr(req, "is_vip", False) or getattr(req, "requester_vip", False))

    priority_code = calculate_itil_priority(ticket.impact or 3, ticket.urgency or 3, is_vip)
    ticket.priority_code = priority_code

    asset_id = ticket.custom_fields.get("asset_id") if isinstance(ticket.custom_fields, dict) else None
    contract_id = ticket.custom_fields.get("contract_id") if isinstance(ticket.custom_fields, dict) else None
    rule_tags = ticket.custom_fields.get("tags") if isinstance(ticket.custom_fields, dict) else None
    site_id = ticket.custom_fields.get("site_id") if isinstance(ticket.custom_fields, dict) else None

    # Load active SLAs for this entity
    entity_slas = list(await db.scalars(
        select(SLA)
        .options(selectinload(SLA.calendar_rel))
        .where(SLA.entity_id == ticket.entity_id, SLA.is_active.is_(True))
        .order_by(SLA.id.asc())
    ))
    slas_by_id = {s.id: s for s in entity_slas}

    resolved = resolve_sla_policy(
        global_override_sla_id=ticket.sla_id if ticket.sla_id and is_vip else None,
        asset={"sla_id": int(asset_id)} if asset_id and str(asset_id).isdigit() else None,
        contract={"sla_id": int(contract_id)} if contract_id and str(contract_id).isdigit() else None,
        ticket_type_default_sla_id=ticket.sla_id,
        slas_by_id=slas_by_id,
    )

    policy = resolved.get("sla")
    tier_name = resolved.get("matched_tier", "Site / Ticket Type Default")
    reason_name = resolved.get("matched_reason", "Entity default SLA policy applied")

    if not policy and entity_slas:
        policy = next((s for s in entity_slas if getattr(s, "is_default", False)), entity_slas[0])

    if policy:
        ticket.sla_id = policy.id
        ticket.sla_matched_tier = tier_name
        ticket.sla_matched_reason = reason_name

        calendar = policy.calendar_rel
        if not calendar and policy.calendar_id:
            calendar = await db.scalar(select(WorkdayCalendar).where(WorkdayCalendar.id == policy.calendar_id))
        if not calendar:
            calendar = await db.scalar(select(WorkdayCalendar).where(WorkdayCalendar.entity_id == ticket.entity_id, WorkdayCalendar.is_active.is_(True)).order_by(WorkdayCalendar.id.asc()).limit(1))

        p_targets = (policy.priorities_config or {}).get(priority_code, {"response_mins": 60, "resolution_mins": 480})
        opened_at = ticket.opened_at or datetime.now(UTC)
        effective_start = get_effective_sla_start(opened_at, calendar)
        ticket.sla_effective_start_at = effective_start

        response_mins = int(p_targets.get("response_mins") or 60)
        resolution_mins = int(p_targets.get("resolution_mins") or 480)

        ticket.tto_due_at = add_working_minutes(effective_start, response_mins, calendar)
        ticket.ttr_due_at = add_working_minutes(effective_start, resolution_mins, calendar, extend_to_eod=bool(policy.extend_to_eod))


async def handle_status_sla_transition(
    ticket: Ticket,
    old_status: str,
    new_status: str,
    user_id: int | None,
    db: AsyncSession,
    hold_reason: str | None = None,
) -> None:
    old_norm = (old_status or "").lower()
    new_norm = (new_status or "").lower()

    old_is_hold = old_norm in HOLD_STATUSES
    new_is_hold = new_norm in HOLD_STATUSES

    # Case 1: Entering hold/pause status
    if not old_is_hold and new_is_hold:
        interval = SLAHoldInterval(
            ticket_id=ticket.id,
            reason=hold_reason or new_norm,
            start_time=datetime.now(UTC),
            created_by_id=user_id,
        )
        db.add(interval)
        db.add(
            AuditLog(
                entity="sla",
                entity_id=ticket.id,
                action="sla_paused",
                diff={"status": new_status, "reason": interval.reason},
                actor_id=user_id,
            )
        )

    # Case 2: Exiting hold/pause status
    elif old_is_hold and not new_is_hold:
        active_intervals = list(await db.scalars(
            select(SLAHoldInterval)
            .where(SLAHoldInterval.ticket_id == ticket.id, SLAHoldInterval.end_time.is_(None))
            .order_by(SLAHoldInterval.start_time.desc())
        ))
        now = datetime.now(UTC)
        for interval in active_intervals:
            interval.end_time = now

        policy, calendar = await get_ticket_policy_and_calendar(ticket, db)
        
        # Calculate working minutes elapsed during hold and push remaining deadlines forward
        total_hold_working_mins = 0
        for interval in active_intervals:
            working_mins = calculate_working_minutes_between(interval.start_time, now, calendar)
            total_hold_working_mins += working_mins

        if total_hold_working_mins > 0:
            if ticket.tto_due_at and ticket.first_responded_at is None:
                ticket.tto_due_at = add_working_minutes(ticket.tto_due_at, total_hold_working_mins, calendar)
            if ticket.ttr_due_at and (ticket.status or "").upper() not in CLOSED_STATUSES:
                extend_eod = bool(getattr(policy, "extend_to_eod", False)) if policy else False
                ticket.ttr_due_at = add_working_minutes(ticket.ttr_due_at, total_hold_working_mins, calendar, extend_to_eod=extend_eod)

        db.add(
            AuditLog(
                entity="sla",
                entity_id=ticket.id,
                action="sla_resumed",
                diff={
                    "status": new_status,
                    "hold_working_mins_compensated": total_hold_working_mins,
                },
                actor_id=user_id,
            )
        )


async def handle_requester_reply_resume(ticket: Ticket, user: User, db: AsyncSession) -> bool:
    if ticket.requester_id and user.id == ticket.requester_id:
        current_status = (ticket.status or "").lower()
        if current_status in HOLD_STATUSES:
            resumed_status = "ASSIGNED" if ticket.assignee_id else "NEW"
            await handle_status_sla_transition(ticket, ticket.status, resumed_status, user.id, db)
            ticket.status = resumed_status

            policy, calendar = await get_ticket_policy_and_calendar(ticket, db)
            if policy and policy.reset_response_on_user_reply:
                ticket.first_responded_at = None
                ticket.response_breach_triggered = False
                p_targets = (policy.priorities_config or {}).get(ticket.priority_code or "P3", {})
                resp_mins = int(p_targets.get("response_mins") or 60)
                ticket.tto_due_at = add_working_minutes(datetime.now(UTC), resp_mins, calendar)

            return True
    return False


async def handle_first_response(
    ticket: Ticket,
    author: User,
    db: AsyncSession,
    is_private: bool = False,
) -> None:
    if is_private:
        return
    if ticket.first_responded_at is None and author.id != ticket.requester_id:
        now = datetime.now(UTC)
        ticket.first_responded_at = now
        if ticket.tto_due_at and now > ticket.tto_due_at:
            ticket.response_breach_triggered = True
        db.add(
            AuditLog(
                entity="sla",
                entity_id=ticket.id,
                action="sla_first_response",
                diff={
                    "first_responded_at": now.isoformat(),
                    "response_breached": bool(ticket.response_breach_triggered),
                },
                actor_id=author.id,
            )
        )


async def validate_breach_resolution(
    ticket: Ticket,
    new_status: str,
    breach_code: str | None,
    breach_reason: str | None,
    db: AsyncSession,
) -> None:
    if (new_status or "").upper() in CLOSED_STATUSES or (new_status or "").lower() in CLOSED_STATUSES:
        state = await evaluate_ticket_live(ticket, db)
        is_breached = bool(state.get("resolution", {}).get("breached") or (ticket.ttr_due_at and datetime.now(UTC) > as_utc(ticket.ttr_due_at)))
        
        policy, _ = await get_ticket_policy_and_calendar(ticket, db)
        require_code = getattr(policy, "require_breach_code", True) if policy else True

        if is_breached:
            ticket.breach_triggered = True
            if require_code:
                if not breach_code or not breach_code.strip():
                    raise HTTPException(
                        status_code=400,
                        detail="Resolution SLA breached. A valid breach justification code and rationale are mandatory to solve or close this ticket.",
                    )
                code_obj = await db.scalar(
                    select(SLABreachCode).where(
                        SLABreachCode.code == breach_code.strip(),
                        SLABreachCode.is_active.is_(True),
                    )
                )
                if not code_obj:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Breach justification code '{breach_code}' is not recognized or is inactive.",
                    )
                ticket.breach_code = breach_code.strip()
                ticket.breach_reason = (breach_reason or "").strip()
        else:
            ticket.breach_code = None
            ticket.breach_reason = None


async def evaluate_ticket_live(ticket: Ticket, db: AsyncSession) -> dict[str, Any]:
    policy, calendar = await get_ticket_policy_and_calendar(ticket, db)
    intervals = list(await db.scalars(
        select(SLAHoldInterval)
        .where(SLAHoldInterval.ticket_id == ticket.id)
        .order_by(SLAHoldInterval.start_time.asc())
    ))
    return evaluate_ticket_sla(ticket, policy, calendar, intervals)
