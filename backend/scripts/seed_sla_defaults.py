import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from datetime import UTC, datetime
from sqlalchemy import select
from app.core.db import SessionLocal
from app.models import WorkdayCalendar, SLA, SLABreachCode, Entity

DEFAULT_BREACH_CODES = [
    ("VENDOR_DELAY", "External Vendor / 3rd-Party Delay", "Delay caused by external supplier or third-party service provider"),
    ("CUSTOMER_UNRESPONSIVE", "End-User Unresponsive Before Hold", "Customer did not provide required information before hold status was applied"),
    ("RESOURCE_SHORTAGE", "Agent / Specialist Capacity Constraint", "Technical team capacity or specialist availability constraint"),
    ("COMPLEX_ROOT_CAUSE", "Complex Multi-System Root Cause", "Investigation required cross-functional troubleshooting and multi-system debugging"),
    ("MISROUTED_TRIAGE", "Initial Triage / Queue Misrouting", "Ticket was routed to the wrong support team during initial intake"),
    ("INFRASTRUCTURE_OUTAGE", "Major Infrastructure / Upstream Outage", "Wide-scale telecommunications, cloud, or power outage hindered progress")
]

DEFAULT_PRIORITIES_CONFIG = {
    "P1": {"response_minutes": 15, "resolution_minutes": 120, "ola_minutes": 60},
    "P2": {"response_minutes": 30, "resolution_minutes": 240, "ola_minutes": 120},
    "P3": {"response_minutes": 60, "resolution_minutes": 480, "ola_minutes": 240},
    "P4": {"response_minutes": 120, "resolution_minutes": 1440, "ola_minutes": 720}
}

async def seed():
    async with SessionLocal() as db:
        entity = await db.scalar(select(Entity).order_by(Entity.id))
        entity_id = entity.id if entity else None

        # 1. Breach Codes
        for code, label, desc in DEFAULT_BREACH_CODES:
            existing = await db.scalar(select(SLABreachCode).where(SLABreachCode.code == code))
            if not existing:
                db.add(SLABreachCode(code=code, label=label, description=desc, is_active=True))
                print(f"Seeded breach code: {code}")

        # 2. Calendars
        cal_standard = await db.scalar(select(WorkdayCalendar).where(WorkdayCalendar.name == "Standard Business Hours"))
        if not cal_standard:
            cal_standard = WorkdayCalendar(
                name="Standard Business Hours",
                timezone="UTC",
                is_24_7=False,
                workdays=[1, 2, 3, 4, 5],
                start_time="09:00",
                end_time="17:30",
                breaks=[{"name": "Lunch", "start": "12:00", "end": "13:00"}],
                holidays=[],
                delayed_start_cutoff="17:00",
                entity_id=entity_id,
                is_active=True
            )
            db.add(cal_standard)
            await db.flush()
            print("Seeded Standard Business Hours calendar")

        cal_247 = await db.scalar(select(WorkdayCalendar).where(WorkdayCalendar.name == "24x7 Mission Critical"))
        if not cal_247:
            cal_247 = WorkdayCalendar(
                name="24x7 Mission Critical",
                timezone="UTC",
                is_24_7=True,
                workdays=[1, 2, 3, 4, 5, 6, 7],
                start_time="00:00",
                end_time="23:59",
                breaks=[],
                holidays=[],
                delayed_start_cutoff=None,
                entity_id=entity_id,
                is_active=True
            )
            db.add(cal_247)
            await db.flush()
            print("Seeded 24x7 Mission Critical calendar")

        # 3. Update existing SLAs with default calendar and priorities_config
        slas = (await db.scalars(select(SLA))).all()
        for sla in slas:
            if not sla.priorities_config:
                sla.priorities_config = DEFAULT_PRIORITIES_CONFIG
            if not sla.calendar_id and cal_standard:
                sla.calendar_id = cal_standard.id
            if not sla.warning1_pct:
                sla.warning1_pct = 50
            if not sla.warning2_pct:
                sla.warning2_pct = 75
            print(f"Updated SLA '{sla.name}' with priorities_config and calendar_id")

        if not slas and cal_standard and entity_id:
            db.add(SLA(
                name="Default Service SLA",
                calendar_id=cal_standard.id,
                priorities_config=DEFAULT_PRIORITIES_CONFIG,
                warning1_pct=50,
                warning2_pct=75,
                auto_escalate_on_breach=True,
                require_breach_code=True,
                entity_id=entity_id,
                is_active=True
            ))
            print("Seeded Default Service SLA")

        await db.commit()
        print("SLA Defaults Seeding Complete")

if __name__ == "__main__":
    asyncio.run(seed())
