import unittest
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import HTTPException

from app.services.sla_engine import (
    add_working_minutes,
    as_utc,
    calculate_itil_priority,
    calculate_working_minutes_between,
    evaluate_ticket_sla,
    get_day_working_segments,
    get_effective_sla_start,
    get_zoned_parts,
    parse_time_minutes,
)


class TestSLAEngine(unittest.TestCase):

    def setUp(self):
        # Standard calendar: Mon-Fri 09:00-17:30, lunch break 12:00-13:00 (450 working mins/day)
        self.standard_calendar = {
            "name": "Standard Business Hours",
            "timezone": "UTC",
            "is_24_7": False,
            "workdays": [1, 2, 3, 4, 5],
            "start_time": "09:00",
            "end_time": "17:30",
            "breaks": [{"name": "Lunch", "start": "12:00", "end": "13:00"}],
            "holidays": ["2026-12-25", "2026-01-01"],
            "delayed_start_cutoff": "16:00",
        }

        # 24x7 calendar
        self.critical_24_7_calendar = {
            "name": "24x7 Mission Critical",
            "timezone": "UTC",
            "is_24_7": True,
            "workdays": [1, 2, 3, 4, 5, 6, 7],
            "start_time": "00:00",
            "end_time": "23:59",
            "breaks": [],
            "holidays": [],
            "delayed_start_cutoff": None,
        }

    # ==========================================
    # Pillar 1: ITIL Priority Matrix & VIP Boost
    # ==========================================

    def test_itil_priority_matrix(self):
        # Impact (1=Extensive..5=None) x Urgency (1=Critical..5=Low)
        self.assertEqual(calculate_itil_priority(1, 1, is_vip=False), "P1")
        self.assertEqual(calculate_itil_priority(2, 2, is_vip=False), "P2")
        self.assertEqual(calculate_itil_priority(3, 3, is_vip=False), "P3")
        self.assertEqual(calculate_itil_priority(4, 4, is_vip=False), "P4")
        self.assertEqual(calculate_itil_priority(5, 5, is_vip=False), "P4")

    def test_vip_priority_boost(self):
        # A normal P3 promoted to P2 with VIP boost
        self.assertEqual(calculate_itil_priority(3, 3, is_vip=False), "P3")
        self.assertEqual(calculate_itil_priority(3, 3, is_vip=True), "P2")

        # P2 promoted to P1 with VIP boost
        self.assertEqual(calculate_itil_priority(2, 2, is_vip=True), "P1")

        # P1 capped at P1
        self.assertEqual(calculate_itil_priority(1, 1, is_vip=True), "P1")

    # ==========================================
    # Pillar 2: Working Segments & Breaks
    # ==========================================

    def test_working_segments_exclude_lunch_break(self):
        # Monday (iso_weekday=1)
        segs = get_day_working_segments("2026-06-01", 1, self.standard_calendar)
        # 09:00 (540m) to 12:00 (720m) = 180 mins
        # 13:00 (780m) to 17:30 (1050m) = 270 mins
        # Total = 450 working minutes (7.5 hours)
        self.assertEqual(segs, [(540, 720), (780, 1050)])
        total_mins = sum(end - start for start, end in segs)
        self.assertEqual(total_mins, 450)

    def test_working_segments_exclude_holidays_and_weekends(self):
        # Holiday: Christmas 2026-12-25
        segs_holiday = get_day_working_segments("2026-12-25", 5, self.standard_calendar)
        self.assertEqual(segs_holiday, [])

        # Saturday (iso_weekday=6)
        segs_weekend = get_day_working_segments("2026-06-06", 6, self.standard_calendar)
        self.assertEqual(segs_weekend, [])

    # ==========================================
    # Pillar 3: Delayed-Start Cutoff
    # ==========================================

    def test_delayed_start_cutoff(self):
        # Cutoff is 16:00.
        # Ticket logged Monday 2026-06-01 at 15:30 (before cutoff) -> starts at 15:30
        logged_before = datetime(2026, 6, 1, 15, 30, tzinfo=UTC)
        eff_before = get_effective_sla_start(logged_before, self.standard_calendar)
        self.assertEqual(eff_before, logged_before)

        # Ticket logged Monday 2026-06-01 at 16:15 (after cutoff) -> starts Tuesday 09:00
        logged_after = datetime(2026, 6, 1, 16, 15, tzinfo=UTC)
        eff_after = get_effective_sla_start(logged_after, self.standard_calendar)
        expected_tuesday_open = datetime(2026, 6, 2, 9, 0, tzinfo=UTC)
        self.assertEqual(eff_after, expected_tuesday_open)

    # ==========================================
    # Pillar 4: Minute-Accurate Due Date Arithmetic
    # ==========================================

    def test_add_working_minutes_across_lunch_break(self):
        # Start at Monday 11:30. Add 60 working minutes.
        # 11:30-12:00 uses 30 mins.
        # 12:00-13:00 is lunch break (0 working mins).
        # 13:00-13:30 uses remaining 30 mins.
        # Expected due: 13:30.
        start = datetime(2026, 6, 1, 11, 30, tzinfo=UTC)
        due = add_working_minutes(start, 60, self.standard_calendar)
        self.assertEqual(due, datetime(2026, 6, 1, 13, 30, tzinfo=UTC))

    def test_add_working_minutes_across_weekend(self):
        # Friday 2026-06-05 at 16:30. Add 120 working minutes.
        # Friday 16:30-17:30 uses 60 minutes.
        # Saturday & Sunday are skipped.
        # Monday 2026-06-08 09:00 uses remaining 60 minutes -> reaches 10:00.
        start_friday = datetime(2026, 6, 5, 16, 30, tzinfo=UTC)
        due = add_working_minutes(start_friday, 120, self.standard_calendar)
        self.assertEqual(due, datetime(2026, 6, 8, 10, 0, tzinfo=UTC))

    def test_add_working_minutes_24_7(self):
        # 24x7 calendar adds straight clock minutes without skipping
        start = datetime(2026, 6, 5, 23, 0, tzinfo=UTC)
        due = add_working_minutes(start, 120, self.critical_24_7_calendar)
        self.assertEqual(due, datetime(2026, 6, 6, 1, 0, tzinfo=UTC))

    # ==========================================
    # Pillar 5: Hold / Pause Interval Math
    # ==========================================

    def test_calculate_working_minutes_with_holds(self):
        # Start Monday 09:00, End Monday 17:00 (Normally 7 hrs = 420 working mins without lunch, or 390 mins with 1hr lunch)
        from_dt = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        to_dt = datetime(2026, 6, 1, 17, 0, tzinfo=UTC)

        # Without holds: 09:00-12:00 (180m) + 13:00-17:00 (240m) = 420 minutes
        mins_no_hold = calculate_working_minutes_between(from_dt, to_dt, self.standard_calendar, hold_intervals=[])
        self.assertEqual(mins_no_hold, 420.0)

        # Hold from 10:00 to 11:30 (90 minutes paused)
        hold = {
            "start_time": datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
            "end_time": datetime(2026, 6, 1, 11, 30, tzinfo=UTC),
        }
        mins_with_hold = calculate_working_minutes_between(from_dt, to_dt, self.standard_calendar, hold_intervals=[hold])
        self.assertEqual(mins_with_hold, 330.0)  # 420 - 90 = 330 minutes

    # ==========================================
    # Pillar 6: Multi-Stage Escalation & Health Evaluation
    # ==========================================

    def test_evaluate_ticket_sla_health_stages(self):
        class MockPolicy:
            priorities_config = {
                "P1": {"response_minutes": 30, "resolution_minutes": 120},
            }
            warning1_pct = 50.0
            warning2_pct = 75.0
            extend_to_eod = False

        class MockTicket:
            priority_code = "P1"
            status = "ASSIGNED"
            opened_at = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
            sla_effective_start_at = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
            first_responded_at = datetime(2026, 6, 1, 9, 10, tzinfo=UTC)
            solved_at = None
            closed_at = None

        policy = MockPolicy()
        ticket = MockTicket()

        # At 09:20 (20 working minutes elapsed out of 120m target = 16.7%) -> on_track
        now_early = datetime(2026, 6, 1, 9, 20, tzinfo=UTC)
        eval_early = evaluate_ticket_sla(ticket, policy, self.standard_calendar, now_dt=now_early)
        self.assertEqual(eval_early["sla_health"], "on_track")
        self.assertFalse(eval_early["resolution"]["breached"])

        # At 10:10 (70 working minutes elapsed out of 120m target = 58.3%) -> warning_1 (50% warning)
        now_warn1 = datetime(2026, 6, 1, 10, 10, tzinfo=UTC)
        eval_warn1 = evaluate_ticket_sla(ticket, policy, self.standard_calendar, now_dt=now_warn1)
        self.assertEqual(eval_warn1["sla_health"], "warning_1")

        # At 10:40 (100 working minutes elapsed out of 120m target = 83.3%) -> warning_2 (75% warning)
        now_warn2 = datetime(2026, 6, 1, 10, 40, tzinfo=UTC)
        eval_warn2 = evaluate_ticket_sla(ticket, policy, self.standard_calendar, now_dt=now_warn2)
        self.assertEqual(eval_warn2["sla_health"], "warning_2")

        # At 11:15 (135 working minutes elapsed out of 120m target > 100%) -> breached
        now_breach = datetime(2026, 6, 1, 11, 15, tzinfo=UTC)
        eval_breach = evaluate_ticket_sla(ticket, policy, self.standard_calendar, now_dt=now_breach)
        self.assertEqual(eval_breach["sla_health"], "breached")
        self.assertTrue(eval_breach["resolution"]["breached"])


if __name__ == "__main__":
    unittest.main()
