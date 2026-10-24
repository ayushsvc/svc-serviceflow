from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.deps import CurrentUser, DbSession, require_admin, require_helpdesk
from app.models import (
    Group,
    GroupMember,
    Ticket,
    TicketWorkflow,
    User,
    WorkflowApproval,
)
from app.schemas_workflows import (
    ApprovalCreate,
    ApprovalDecision,
    ApprovalRead,
    TicketWorkflowRead,
    TicketWorkflowWrite,
)
from app.services.audit import record_audit

router = APIRouter(tags=["workflows"])
SUPPORTED_RECORD_TYPES = {"ticket"}


def validate_record_type(record_type: str) -> None:
    if record_type not in SUPPORTED_RECORD_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported workflow record type")


async def get_ticket_in_entity(ticket_id: int, entity_id: int, db: DbSession) -> Ticket:
    ticket = await db.scalar(
        select(Ticket).where(
            Ticket.id == ticket_id,
            Ticket.entity_id == entity_id,
            Ticket.deleted_at.is_(None),
        )
    )
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@router.get("/workflows/{record_type}", response_model=TicketWorkflowRead | None)
async def get_workflow(record_type: str, user: CurrentUser, db: DbSession) -> TicketWorkflow | None:
    require_helpdesk(user)
    validate_record_type(record_type)
    return await db.scalar(
        select(TicketWorkflow).where(
            TicketWorkflow.entity_id == user.entity_id,
            TicketWorkflow.record_type == record_type,
            TicketWorkflow.is_active.is_(True),
        )
    )


@router.put("/workflows/{record_type}", response_model=TicketWorkflowRead)
async def set_workflow(
    record_type: str,
    payload: TicketWorkflowWrite,
    user: CurrentUser,
    db: DbSession,
) -> TicketWorkflow:
    require_admin(user)
    validate_record_type(record_type)
    workflow = await db.scalar(
        select(TicketWorkflow).where(
            TicketWorkflow.entity_id == user.entity_id,
            TicketWorkflow.record_type == record_type,
        )
    )
    if workflow is None:
        workflow = TicketWorkflow(
            entity_id=user.entity_id,
            record_type=record_type,
            states=payload.states,
            transitions=payload.transitions,
            approval_requirements=payload.approval_requirements,
            is_active=payload.is_active,
        )
        db.add(workflow)
        await db.flush()
        action = "created"
    else:
        workflow.states = payload.states
        workflow.transitions = payload.transitions
        workflow.approval_requirements = payload.approval_requirements
        workflow.is_active = payload.is_active
        action = "updated"
    await record_audit(
        db,
        entity="ticket_workflow",
        entity_id=workflow.id,
        action=action,
        actor_id=user.id,
        diff={
            "record_type": record_type,
            "states": payload.states,
            "transitions": payload.transitions,
            "approval_requirements": payload.approval_requirements,
            "is_active": payload.is_active,
        },
    )
    await db.commit()
    await db.refresh(workflow)
    return workflow


@router.get("/approvals", response_model=list[ApprovalRead])
async def list_approvals(
    user: CurrentUser,
    db: DbSession,
    record_type: str = Query(default="ticket"),
    record_id: int | None = Query(default=None, gt=0),
) -> list[WorkflowApproval]:
    require_helpdesk(user)
    validate_record_type(record_type)
    query = select(WorkflowApproval).where(
        WorkflowApproval.entity_id == user.entity_id,
        WorkflowApproval.record_type == record_type,
    )
    if record_id is not None:
        await get_ticket_in_entity(record_id, user.entity_id, db)
        query = query.where(WorkflowApproval.record_id == record_id)
    return list((await db.scalars(query.order_by(WorkflowApproval.step, WorkflowApproval.id))).all())


@router.post("/approvals", response_model=ApprovalRead, status_code=status.HTTP_201_CREATED)
async def create_approval(
    payload: ApprovalCreate,
    user: CurrentUser,
    db: DbSession,
) -> WorkflowApproval:
    require_admin(user)
    validate_record_type(payload.record_type)
    await get_ticket_in_entity(payload.record_id, user.entity_id, db)
    workflow = await db.scalar(
        select(TicketWorkflow).where(
            TicketWorkflow.entity_id == user.entity_id,
            TicketWorkflow.record_type == payload.record_type,
            TicketWorkflow.is_active.is_(True),
        )
    )
    if workflow is None or payload.transition_to not in workflow.states:
        raise HTTPException(status_code=422, detail="Approval target must be a state in an active workflow")
    if workflow.approval_requirements.get(payload.transition_to, 0) < payload.step:
        raise HTTPException(status_code=422, detail="Approval step exceeds the configured requirement")
    if payload.approver_user_id is not None:
        approver = await db.scalar(
            select(User).where(
                User.id == payload.approver_user_id,
                User.entity_id == user.entity_id,
                User.is_active.is_(True),
            )
        )
    else:
        approver = await db.scalar(
            select(Group).where(
                Group.id == payload.approver_group_id,
                Group.entity_id == user.entity_id,
            )
        )
    if approver is None:
        raise HTTPException(status_code=422, detail="Approver is outside your entity or inactive")
    approval = WorkflowApproval(entity_id=user.entity_id, requested_by=user.id, **payload.model_dump())
    db.add(approval)
    await db.flush()
    await record_audit(
        db,
        entity="workflow_approval",
        entity_id=approval.id,
        action="requested",
        actor_id=user.id,
        diff={"record_type": payload.record_type, "record_id": payload.record_id, "transition_to": payload.transition_to},
    )
    await db.commit()
    await db.refresh(approval)
    return approval


@router.get("/approvals/mine", response_model=list[ApprovalRead])
async def my_approvals(user: CurrentUser, db: DbSession) -> list[WorkflowApproval]:
    require_helpdesk(user)
    group_ids = select(GroupMember.group_id).where(GroupMember.user_id == user.id)
    query = select(WorkflowApproval).where(
        WorkflowApproval.entity_id == user.entity_id,
        WorkflowApproval.status == "PENDING",
        (
            (WorkflowApproval.approver_user_id == user.id)
            | (WorkflowApproval.approver_group_id.in_(group_ids))
        ),
    )
    return list((await db.scalars(query.order_by(WorkflowApproval.created_at))).all())


@router.post("/approvals/{approval_id}/decision", response_model=ApprovalRead)
async def decide_approval(
    approval_id: int,
    payload: ApprovalDecision,
    user: CurrentUser,
    db: DbSession,
) -> WorkflowApproval:
    require_helpdesk(user)
    approval = await db.scalar(
        select(WorkflowApproval).where(
            WorkflowApproval.id == approval_id,
            WorkflowApproval.entity_id == user.entity_id,
        ).with_for_update()
    )
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval.status != "PENDING":
        raise HTTPException(status_code=409, detail="Approval has already been decided")
    is_assigned_user = approval.approver_user_id == user.id
    is_assigned_group_member = False
    if approval.approver_group_id is not None:
        is_assigned_group_member = (
            await db.scalar(
                select(GroupMember.group_id).where(
                    GroupMember.group_id == approval.approver_group_id,
                    GroupMember.user_id == user.id,
                )
            )
            is not None
        )
    if not (is_assigned_user or is_assigned_group_member):
        raise HTTPException(status_code=403, detail="This approval is not assigned to you")
    approval.status = payload.decision
    approval.note = payload.note
    approval.decided_by = user.id
    approval.decided_at = datetime.now(UTC)
    await record_audit(
        db,
        entity="workflow_approval",
        entity_id=approval.id,
        action=payload.decision.lower(),
        actor_id=user.id,
        diff={"record_type": approval.record_type, "record_id": approval.record_id, "note": payload.note},
    )
    await db.commit()
    await db.refresh(approval)
    return approval
