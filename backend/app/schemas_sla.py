from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class BreakInterval(BaseModel):
    name: str = Field(default="Lunch", max_length=80)
    start: str = Field(default="12:00", pattern=r"^\d{2}:\d{2}$")
    end: str = Field(default="13:00", pattern=r"^\d{2}:\d{2}$")


class HolidayItem(BaseModel):
    name: str = Field(max_length=120)
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class WorkdayCalendarBase(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    timezone: str = Field(default="UTC", max_length=64)
    is_24_7: bool = False
    workdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    start_time: str = Field(default="09:00", pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(default="17:00", pattern=r"^\d{2}:\d{2}$")
    breaks: list[dict[str, str]] = Field(default_factory=list)
    holidays: list[dict[str, str]] = Field(default_factory=list)
    delayed_start_cutoff: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    is_active: bool = True


class WorkdayCalendarCreate(WorkdayCalendarBase):
    pass


class WorkdayCalendarUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    timezone: str | None = Field(default=None, max_length=64)
    is_24_7: bool | None = None
    workdays: list[int] | None = None
    start_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    end_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    breaks: list[dict[str, str]] | None = None
    holidays: list[dict[str, str]] | None = None
    delayed_start_cutoff: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    is_active: bool | None = None


class WorkdayCalendarRead(WorkdayCalendarBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    entity_id: int
    created_at: datetime
    updated_at: datetime


class PriorityTargetConfig(BaseModel):
    response_mins: int = Field(ge=1, default=60)
    resolution_mins: int = Field(ge=1, default=480)
    ola_mins: int | None = Field(default=None, ge=1)
    escalate_after_mins: int | None = Field(default=None, ge=1)


class SLAPolicyBase(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    calendar_id: int | None = None
    priorities_config: dict[str, dict[str, Any]] = Field(
        default_factory=lambda: {
            "P1": {"response_mins": 15, "resolution_mins": 120, "ola_mins": 60},
            "P2": {"response_mins": 30, "resolution_mins": 240, "ola_mins": 120},
            "P3": {"response_mins": 60, "resolution_mins": 480, "ola_mins": 240},
            "P4": {"response_mins": 120, "resolution_mins": 1440, "ola_mins": 480},
        }
    )
    warning1_pct: float = Field(default=50.0, ge=10.0, le=95.0)
    warning2_pct: float = Field(default=75.0, ge=20.0, le=99.0)
    auto_escalate_on_breach: bool = True
    require_breach_code: bool = True
    reset_response_on_user_reply: bool = False
    extend_to_eod: bool = False
    is_default: bool = False
    is_active: bool = True


class SLAPolicyCreate(SLAPolicyBase):
    pass


class SLAPolicyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = None
    calendar_id: int | None = None
    priorities_config: dict[str, dict[str, Any]] | None = None
    warning1_pct: float | None = Field(default=None, ge=10.0, le=95.0)
    warning2_pct: float | None = Field(default=None, ge=20.0, le=99.0)
    auto_escalate_on_breach: bool | None = None
    require_breach_code: bool | None = None
    reset_response_on_user_reply: bool | None = None
    extend_to_eod: bool | None = None
    is_default: bool | None = None
    is_active: bool | None = None


class SLAPolicyRead(SLAPolicyBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    entity_id: int
    calendar: WorkdayCalendarRead | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SLAHoldIntervalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ticket_id: int
    reason: str
    start_time: datetime
    end_time: datetime | None = None
    created_by_id: int | None = None
    created_at: datetime
    duration_minutes: int | None = None


class SLABreachCodeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    label: str
    description: str | None = None
    is_active: bool


class SLAPreviewRequest(BaseModel):
    calendar_id: int | None = None
    start_time: datetime | None = None
    minutes_to_add: int = Field(ge=1)
    extend_to_eod: bool = False


class SLAPreviewResponse(BaseModel):
    calculated_due_at: datetime
    calendar_name: str
    calendar_timezone: str


class TicketResolveWithBreachPayload(BaseModel):
    breach_code: str | None = Field(default=None, max_length=64)
    breach_reason: str | None = Field(default=None, max_length=1000)
    solution: str | None = Field(default=None, max_length=30000)
    solution_type: str = Field(default="FIX", max_length=40)
