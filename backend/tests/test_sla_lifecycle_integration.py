import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from fastapi import HTTPException
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import AuditLog, SLA, SLABreachCode, SLAHoldInterval, Ticket, User, WorkdayCalendar
from app.services.sla_service import (
    apply_initial_sla,
    evaluate_ticket_live,
    handle_first_response,
    handle_requester_reply_resume,
    handle_status_sla_transition,
    validate_breach_resolution,
)
from app.workers.tasks import _check_sla_breaches


class TestSLALifecycleIntegration(unittest.TestCase):

    def run_async(self, coro):
        return asyncio.run(coro)

    def test_full_ticket_sla_lifecycle(self):
        async def scenario():
            async with SessionLocal() as db:
                # 1. Fetch admin user and standard calendar
                admin = await db.scalar(select(User).where(User.id == 1))
                self.assertIsNotNone(admin)

                # 2. Create a test ticket
                ticket = Ticket(
                    title="Integration Test SLA Ticket",
                    description="Testing complete HaloITSM SLA lifecycle",
                    type="INCIDENT",
                    urgency=2,
                    impact=2,  # P2
                    priority=2,
                    entity_id=admin.entity_id,
                    requester_id=admin.id,
                    status="NEW",
                    opened_at=datetime.now(UTC),
                )
                db.add(ticket)
                await db.flush()

                # 3. Apply initial SLA
                await apply_initial_sla(ticket, db, requester_user=admin)
                self.assertEqual(ticket.priority_code, "P2")  # Normal user: U2 x I2 = P2
                self.assertIsNotNone(ticket.tto_due_at)
                self.assertIsNotNone(ticket.ttr_due_at)
                self.assertIsNotNone(ticket.sla_effective_start_at)
                self.assertIsNotNone(ticket.sla_matched_tier)

                # VIP Boost test: VIP user with U2 x I2 gets boosted from P2 -> P1
                vip_user = User(id=8888, entity_id=admin.entity_id, email="vip@example.com")
                setattr(vip_user, "is_vip", True)
                ticket_vip = Ticket(
                    title="VIP Ticket",
                    description="VIP Ticket test",
                    type="INCIDENT",
                    urgency=2,
                    impact=2,
                    priority=2,
                    entity_id=admin.entity_id,
                    requester_id=vip_user.id,
                    status="NEW",
                    opened_at=datetime.now(UTC),
                )
                db.add(ticket_vip)
                await db.flush()
                await apply_initial_sla(ticket_vip, db, requester_user=vip_user)
                self.assertEqual(ticket_vip.priority_code, "P1")

                # 4. Agent first response
                agent = User(id=9999, entity_id=admin.entity_id, email="agent_test@example.com")
                await handle_first_response(ticket, agent, db, is_private=False)
                self.assertIsNotNone(ticket.first_responded_at)

                # 5. Transition to hold status (awaiting_user)
                old_status = ticket.status
                ticket.status = "PENDING"
                await handle_status_sla_transition(ticket, old_status, "PENDING", agent.id, db, hold_reason="awaiting_user")
                await db.flush()

                # Verify open hold interval exists
                open_interval = await db.scalar(
                    select(SLAHoldInterval)
                    .where(SLAHoldInterval.ticket_id == ticket.id, SLAHoldInterval.end_time.is_(None))
                )
                self.assertIsNotNone(open_interval)
                self.assertEqual(open_interval.reason, "awaiting_user")

                # 6. Customer replies -> auto resumes ticket from hold
                resumed = await handle_requester_reply_resume(ticket, admin, db)
                self.assertTrue(resumed)
                self.assertEqual(ticket.status, "ASSIGNED" if ticket.assignee_id else "NEW")
                await db.flush()

                # Verify hold interval closed
                closed_interval = await db.scalar(
                    select(SLAHoldInterval)
                    .where(SLAHoldInterval.id == open_interval.id)
                )
                self.assertIsNotNone(closed_interval.end_time)

                # 7. Simulate overdue ticket and test breach justification enforcement
                ticket.ttr_due_at = datetime.now(UTC) - timedelta(hours=2)  # Overdue
                
                # Attempt to resolve without breach code -> must raise HTTPException(400)
                with self.assertRaises(HTTPException) as cm:
                    await validate_breach_resolution(ticket, "SOLVED", breach_code=None, breach_reason=None, db=db)
                self.assertEqual(cm.exception.status_code, 400)

                # Resolve with invalid breach code -> must raise HTTPException(400)
                with self.assertRaises(HTTPException) as cm_inv:
                    await validate_breach_resolution(ticket, "SOLVED", breach_code="INVALID_CODE", breach_reason="Test", db=db)
                self.assertEqual(cm_inv.exception.status_code, 400)

                # Resolve with valid active breach code -> succeeds
                await validate_breach_resolution(
                    ticket,
                    "SOLVED",
                    breach_code="VENDOR_DELAY",
                    breach_reason="3rd party hardware supplier delayed part shipment.",
                    db=db,
                )
                self.assertEqual(ticket.breach_code, "VENDOR_DELAY")
                self.assertEqual(ticket.breach_reason, "3rd party hardware supplier delayed part shipment.")
                self.assertTrue(ticket.breach_triggered)

                # 8. Test live evaluation
                state = await evaluate_ticket_live(ticket, db)
                self.assertIn("sla_health", state)
                self.assertIn("response", state)
                self.assertIn("resolution", state)

                # Rollback so integration test leaves DB clean
                await db.rollback()

        self.run_async(scenario())

    def test_background_worker_multi_stage_escalation(self):
        async def scenario():
            async with SessionLocal() as db:
                admin = await db.scalar(select(User).where(User.id == 1))
                # Create a P3 ticket overdue
                overdue_ticket = Ticket(
                    title="Escalation Test Ticket",
                    description="Testing auto escalation",
                    type="INCIDENT",
                    urgency=3,
                    impact=3,
                    priority=3,
                    priority_code="P3",
                    entity_id=admin.entity_id,
                    requester_id=admin.id,
                    status="ASSIGNED",
                    opened_at=datetime.now(UTC) - timedelta(days=2),
                    ttr_due_at=datetime.now(UTC) - timedelta(hours=1),
                )
                db.add(overdue_ticket)
                await db.commit()
                await db.refresh(overdue_ticket)

                try:
                    await _check_sla_breaches()
                    await db.refresh(overdue_ticket)
                    self.assertTrue(overdue_ticket.breach_triggered)
                    # P3 auto-escalated to P2
                    self.assertEqual(overdue_ticket.priority, 2)
                    self.assertEqual(overdue_ticket.priority_code, "P2")
                finally:
                    await db.delete(overdue_ticket)
                    await db.commit()

        self.run_async(scenario())


if __name__ == "__main__":
    unittest.main()
